"""
Team Match AFK-timeout system. Mirrors handlers/solo.py's Royale-mode
response clock (10s / 30s reminders, 60s final) but with Team Match's own
consequences instead of removing the player from the match entirely:

  - Batter doesn't play their shot within 60s -> they're given OUT, and a
    5-run penalty is deducted from both the team's score and their own
    individual score. The batting captain/Host picks the next batter.

  - Bowler doesn't bowl within 60s -> no elimination, but the batting team
    is awarded a 5-run penalty. The bowling captain/Host picks a new
    bowler to continue the same over.

Neither case consumes a ball -- the over/ball counters don't move, since
no delivery actually happened.

The scheduling plumbing (job naming, reminders, cancellation) is shared
with Solo Match's identical clock via utils/timeouts.py -- this module
only defines what's different: how to look up the active player, and
what happens when the clock runs out.
"""

from __future__ import annotations

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from utils.models import MatchMode, MatchState
from utils.timeouts import TimeoutRole, cancel_timeout_jobs, schedule_timeout

PENALTY_RUNS = 5


def _is_team_match(match: MatchState) -> bool:
    return match.mode is MatchMode.TEAM


_BOWLER_ROLE = TimeoutRole(
    role_name="bowler",
    job_name_prefix="team_bowler",
    awaiting_flag="awaiting_bowler_input",
    active_id_field="bowler_id",
    lookup_player=lambda match, uid: match.bowling_team.players.get(uid),
    reminder_verb="send your number in my DM! 🥎",
    on_final_timeout=lambda match, context, uid: _penalize_afk_bowler(match, context, uid),
    mode_check=_is_team_match,
)

_BATTER_ROLE = TimeoutRole(
    role_name="batter",
    job_name_prefix="team_batter",
    awaiting_flag="awaiting_batter_input",
    active_id_field="striker_id",
    lookup_player=lambda match, uid: match.batting_team.players.get(uid),
    reminder_verb="send your number in group from 1-6! 🏏",
    on_final_timeout=lambda match, context, uid: _penalize_afk_batter(match, context, uid),
    mode_check=_is_team_match,
)


def schedule_bowler_timeout(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    schedule_timeout(match, context, _BOWLER_ROLE)


def cancel_bowler_timeout_jobs(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    cancel_timeout_jobs(match, context, _BOWLER_ROLE)


def schedule_batter_timeout(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    schedule_timeout(match, context, _BATTER_ROLE)


def cancel_batter_timeout_jobs(match: MatchState, context: ContextTypes.DEFAULT_TYPE) -> None:
    cancel_timeout_jobs(match, context, _BATTER_ROLE)


async def _penalize_afk_bowler(match: MatchState, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    bowling_team = match.bowling_team
    bowler = bowling_team.players.get(user_id)
    if bowler is None:
        return

    cancel_bowler_timeout_jobs(match, context)
    cancel_batter_timeout_jobs(match, context)
    match.awaiting_bowler_input = False
    match.awaiting_batter_input = False
    match.pending_bowler_number = None

    # +5 to the batting team only -- no ball bowled, no delivery, no
    # individual batter/bowler stat changes, over/ball counters untouched.
    match.score += PENALTY_RUNS

    match.bowler_id = None

    text = (
        f"⏳ *TIME'S UP!* ' {bowler.display_name} ! timed out! ❌\n"
        f"📈 *PENALTY: +{PENALTY_RUNS} Runs to Batting Team!*\n\n"
        f"Captain/Host, please select a NEW bowler to continue the over using /bowling [number]."
    )
    await context.bot.send_message(chat_id=match.chat_id, text=text, parse_mode=ParseMode.MARKDOWN)

    from utils.scoreboard import format_scoreboard
    await context.bot.send_message(
        chat_id=match.chat_id, text=format_scoreboard(match), parse_mode=ParseMode.MARKDOWN
    )


async def _penalize_afk_batter(match: MatchState, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    batting_team = match.batting_team
    batter = batting_team.players.get(user_id)
    if batter is None:
        return

    cancel_bowler_timeout_jobs(match, context)
    cancel_batter_timeout_jobs(match, context)
    match.awaiting_bowler_input = False
    match.awaiting_batter_input = False
    match.pending_bowler_number = None

    # -5 to both the team score and the batter's own score (floored at 0),
    # then the batter is given out. No ball bowled, no delivery, over/ball
    # counters untouched.
    match.score = max(0, match.score - PENALTY_RUNS)
    batter.runs = max(0, batter.runs - PENALTY_RUNS)
    batter.is_out = True
    match.wickets += 1

    text = (
        f"⏳ *TIME'S UP!* {batter.display_name} was AFK for 60 seconds! ❌\n"
        f"📉 *PENALTY: -{PENALTY_RUNS} Runs to the team and batter!* They are OUT!\n\n"
        f"🏏 Captain/Host, please select the next batter using /batting [number]."
    )
    await context.bot.send_message(chat_id=match.chat_id, text=text, parse_mode=ParseMode.MARKDOWN)

    from utils.scoreboard import format_scoreboard
    await context.bot.send_message(
        chat_id=match.chat_id, text=format_scoreboard(match), parse_mode=ParseMode.MARKDOWN
    )

    # The batter who was just out leaves the crease; the not-out partner
    # (if any) stays on strike, leaving the non-striker slot open for the
    # incoming batter -- same handling as a normal mid-over wicket.
    if match.non_striker_id:
        match.striker_id = match.non_striker_id
        match.non_striker_id = None
    else:
        match.striker_id = None

    all_out = match.wickets >= batting_team.size - 1
    overs_done = match.current_over >= match.overs
    target_reached = match.target is not None and match.score >= match.target

    if target_reached or overs_done or all_out:
        from handlers.gameplay import end_innings
        await end_innings(match, context)
        return

    cap = batting_team.players[batting_team.captain_id]
    await context.bot.send_message(
        chat_id=match.chat_id,
        text=f"🏏 {cap.display_name}, use /batting to send in the next batter.",
    )

    from handlers.gameplay import maybe_ready_to_bowl
    await maybe_ready_to_bowl(match, context)
