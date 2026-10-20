"""
Shared AFK-timeout scheduling core, used by both Solo Match (Royale mode)
and Team Match. Both modes give the active bowler/batter a 60-second
response clock with reminders at the 10s and 30s marks (i.e. "50 seconds
left" and "30 seconds left"), then a final consequence if the clock runs
out -- but the two modes differ in what that consequence is:

  - Solo Match: the unresponsive player is removed from the match
    entirely (handlers/solo.py's _remove_player_for_timeout).
  - Team Match: a run penalty is applied and the match continues
    (handlers/team_timeout.py's _penalize_afk_bowler / _penalize_afk_batter).

Rather than duplicating the job-naming/scheduling/cancellation plumbing
in both files (which was previously identical in both), that machinery
lives here once. Each mode registers its own reminder text and
final-timeout callback via a TimeoutRole.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from utils.models import MatchState

logger = logging.getLogger(__name__)

REMINDER_1_DELAY = 10   # fires with 50s left
REMINDER_2_DELAY = 30   # fires with 30s left
FINAL_DELAY = 60


def mention(player) -> str:
    """A Markdown mention that actually notifies/tags the player, even if
    they don't have a public @username."""
    return f"[{player.first_name}](tg://user?id={player.user_id})"


def plain_name(player) -> str:
    """Just the player's name, no tg://user link -- so this doesn't ping
    them. Used for the 50s/30s reminder nudges, which fire on every
    single ball and would otherwise notify them constantly; a final
    timeout/removal message is rarer and important enough to actually
    tag the player with mention() instead."""
    return player.display_name


@dataclass(frozen=True)
class TimeoutRole:
    """One of "bowler" or "batter" for a given mode. Bundles the bits
    that differ between modes: which MatchState field holds the
    awaiting-input flag and the active player's user_id, how to look
    that player up, the reminder message's verb, and what to do when the
    clock actually runs out."""

    role_name: str  # "bowler" | "batter", also used in job-name prefixes
    job_name_prefix: str  # e.g. "royale_bowler" | "team_bowler"
    awaiting_flag: str  # "awaiting_bowler_input" | "awaiting_batter_input"
    active_id_field: str  # "bowler_id" | "striker_id"
    lookup_player: Callable[[MatchState, int], Optional[object]]
    reminder_verb: str  # e.g. "send your number in my DM! 🥎"
    on_final_timeout: Callable[[MatchState, ContextTypes.DEFAULT_TYPE, int], Awaitable[None]]
    mode_check: Callable[[MatchState], bool]  # True if this match is the right mode for this role


def _job_names_field(role: TimeoutRole) -> str:
    return "bowler_timeout_job_names" if role.role_name == "bowler" else "batter_timeout_job_names"


def _cancel_named_jobs(context: ContextTypes.DEFAULT_TYPE, job_names: list) -> None:
    for name in job_names:
        for job in context.application.job_queue.get_jobs_by_name(name):
            job.schedule_removal()


def cancel_timeout_jobs(match: MatchState, context: ContextTypes.DEFAULT_TYPE, role: TimeoutRole) -> None:
    field = _job_names_field(role)
    _cancel_named_jobs(context, getattr(match, field))
    setattr(match, field, [])


def schedule_timeout(match: MatchState, context: ContextTypes.DEFAULT_TYPE, role: TimeoutRole) -> None:
    if not role.mode_check(match):
        return
    active_id = getattr(match, role.active_id_field)
    if not active_id:
        return
    cancel_timeout_jobs(match, context, role)

    base = f"{role.job_name_prefix}_{match.chat_id}_{match.match_id}_{active_id}_{time.time()}"
    names = [f"{base}_r1", f"{base}_r2", f"{base}_final"]
    setattr(match, _job_names_field(role), names)

    data = {"chat_id": match.chat_id, "user_id": active_id, "role": role}
    context.job_queue.run_once(
        _reminder_job, when=REMINDER_1_DELAY, chat_id=match.chat_id, name=names[0],
        data={**data, "seconds_left": 50},
    )
    context.job_queue.run_once(
        _reminder_job, when=REMINDER_2_DELAY, chat_id=match.chat_id, name=names[1],
        data={**data, "seconds_left": 30},
    )
    context.job_queue.run_once(
        _final_timeout_job, when=FINAL_DELAY, chat_id=match.chat_id, name=names[2], data=data,
    )


def _get_match(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> Optional[MatchState]:
    from handlers.start import get_store
    return get_store(context).get(chat_id)


async def _reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    role: TimeoutRole = data["role"]
    match = _get_match(context, data["chat_id"])
    if match is None or not role.mode_check(match):
        return
    if not getattr(match, role.awaiting_flag) or getattr(match, role.active_id_field) != data["user_id"]:
        return
    player = role.lookup_player(match, data["user_id"])
    if player is None:
        return

    text = f"⏳ *{data['seconds_left']} seconds left!* {plain_name(player)} — {role.reminder_verb}"
    try:
        await context.bot.send_message(chat_id=match.chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
    except TelegramError as exc:
        logger.debug("Could not send %s reminder: %s", role.role_name, exc)


async def _final_timeout_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    role: TimeoutRole = data["role"]
    match = _get_match(context, data["chat_id"])
    if match is None or not role.mode_check(match):
        return
    if not getattr(match, role.awaiting_flag) or getattr(match, role.active_id_field) != data["user_id"]:
        return
    async with match.state_lock:
        # Re-check after acquiring the lock -- their input may have been
        # mid-flight (already inside numeric_input_router's own lock
        # acquisition) when this job fired, and could have completed by
        # the time we get the lock.
        if not getattr(match, role.awaiting_flag) or getattr(match, role.active_id_field) != data["user_id"]:
            return
        await role.on_final_timeout(match, context, data["user_id"])
