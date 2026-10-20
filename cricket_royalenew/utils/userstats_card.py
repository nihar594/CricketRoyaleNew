"""
Builds the visual /userstats card: composites the player's Telegram
profile photo into the circular frame on config.userstats_template(),
and overlays their live career numbers into the template's stat boxes.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
from utils.userstats import compute_exp, level_progress

logger = logging.getLogger(__name__)

# Right-aligned x position of the value text in each column, and the
# vertical center of each of the three stat rows. Measured against the
# original 1264x842 template -- if you swap in a differently laid-out
# template, re-measure these with a grid overlay and update.
COL_X = {
    "batting": 180,
    "bowling": 340,
    "team": 495,
    "career": 655,
}
# (start_x, end_x) of a patch drawn *behind* each value to blot out the
# template's placeholder number before the real value is drawn on top.
# Each row has its own start_x per column because the label text ("AVG:"
# vs "STRIKE RATE:", "RUN OUTS:" vs "STUMPINGS:", etc) is a different
# length in each row -- a single shared range per column either clips
# into the longer labels or leaves the placeholder digits peeking out
# from under the shorter ones.
COL_PATCH_X = {
    ("batting", 1): (108, 188),   # "RUNS:" ends ~105
    ("batting", 2): (98, 188),    # "AVG:" ends ~95
    ("batting", 3): (98, 188),    # "STRIKE / RATE:" (2 lines) ends ~95
    ("bowling", 1): (288, 348),   # "WICKETS:" ends ~285
    ("bowling", 2): (308, 348),   # "ECONOMY:" ends ~305
    ("bowling", 3): (178, 348),   # "BEST FIG:" ends ~175
    ("team", 1): (438, 500),      # "CATCHES:" ends ~435
    ("team", 2): (398, 500),      # "RUN OUTS:" ends ~395
    ("team", 3): (393, 500),      # "STUMPINGS:" ends ~390
    ("career", 1): (593, 660),    # "MATCHES / PLAYED:" (2 lines) ends ~590
    ("career", 2): (493, 660),    # "PLAYER OF THE / MATCHES:" (2 lines) ends ~490
    ("career", 3): (508, 660),    # "TOTAL / ACHIEVEMENTS:" (2 lines) ends ~505
}
ROW_Y = {1: 518, 2: 558, 3: 598}
PATCH_HALF_HEIGHT = 14
PATCH_COLOR = (26, 11, 8)
RATING_POS = (295, 646)
RATING_PATCH = (288, 630, 660, 664)

VALUE_COLOR = (245, 231, 196)
RATING_COLOR = (255, 255, 255)


def _fmt(value) -> str:
    return str(value)


def _build_stat_fields(row) -> dict:
    strike_rate = round((row["runs_scored"] / row["balls_faced"]) * 100, 1) if row["balls_faced"] else 0.0
    batting_avg = round(row["runs_scored"] / row["times_out"], 1) if row["times_out"] else row["runs_scored"]
    economy = (
        round(row["runs_conceded"] / (row["balls_bowled"] / 6), 1) if row["balls_bowled"] else 0.0
    )
    best_bowling = f"{row['best_bowling']}" if row["best_bowling"] else "-"
    total_achievements = row["centuries"] + row["fifties"] + row["motm_awards"]

    highest = str(row["highest_score"])

    exp = compute_exp(row)
    current_level, _, _ = level_progress(exp)
    rating = current_level.split(" ")[0]  # strip the emoji, keep the plain word (Rookie/Pro/Elite/...)

    return {
        ("batting", 1): _fmt(highest),
        ("batting", 2): _fmt(batting_avg),
        ("batting", 3): _fmt(strike_rate),
        ("bowling", 1): _fmt(row["wickets_taken"]),
        ("bowling", 2): _fmt(economy),
        ("bowling", 3): _fmt(best_bowling),
        # NOTE: Cricket Royale has no fielding mechanic (catches / run outs /
        # stumpings aren't tracked), so these three read 0 by design.
        ("team", 1): "0",
        ("team", 2): "0",
        ("team", 3): "0",
        ("career", 1): _fmt(row["matches_played"]),
        ("career", 2): _fmt(row["motm_awards"]),
        ("career", 3): _fmt(total_achievements),
        "rating": rating,
    }


def _composite_sync(photo_bytes: Optional[bytes], row) -> Optional[bytes]:
    if not config.userstats_template().exists():
        logger.warning("Missing asset %s - skipping visual userstats card.", config.userstats_template())
        return None

    from PIL import Image, ImageDraw, ImageFont

    template = Image.open(config.userstats_template()).convert("RGBA")

    if photo_bytes:
        radius = config.USERSTATS_CIRCLE_RADIUS
        diameter = radius * 2
        cx, cy = config.USERSTATS_CIRCLE_CENTER

        profile = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
        w, h = profile.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        profile = profile.crop((left, top, left + side, top + side)).resize(
            (diameter, diameter), Image.LANCZOS
        )

        mask = Image.new("L", (diameter, diameter), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)
        template.paste(profile, (cx - radius, cy - radius), mask)

    draw = ImageDraw.Draw(template)
    try:
        value_font = ImageFont.truetype(str(config.USERSTATS_FONT), 22)
        rating_font = ImageFont.truetype(str(config.USERSTATS_FONT), 24)
    except OSError:
        logger.warning("Could not load %s - falling back to default font.", config.USERSTATS_FONT)
        value_font = ImageFont.load_default()
        rating_font = value_font

    fields = _build_stat_fields(row)

    # Blot out the template's placeholder numbers first, then draw the
    # real values on top of a clean patch.
    for (col, row_num), (x0, x1) in COL_PATCH_X.items():
        y = ROW_Y[row_num]
        draw.rectangle((x0, y - PATCH_HALF_HEIGHT, x1, y + PATCH_HALF_HEIGHT), fill=PATCH_COLOR)
    draw.rectangle(RATING_PATCH, fill=PATCH_COLOR)

    for key, text in fields.items():
        if key == "rating":
            continue
        col, row_num = key
        x = COL_X[col]
        y = ROW_Y[row_num]
        draw.text((x, y), text, font=value_font, fill=VALUE_COLOR, anchor="rm")

    draw.text(RATING_POS, fields["rating"], font=rating_font, fill=RATING_COLOR, anchor="lm")

    buf = io.BytesIO()
    template.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------
# Purchasable /shop themes (assets/userstats_themes/*.jpg)
# --------------------------------------------------------------------------
# These are art mockups, not pre-built templates -- there's no reserved
# blank space for a number next to each stat label (the label text runs
# right up to the box edge in every one of them). Rather than overlapping
# the label or clipping it with a patch, each value is drawn as a small
# circular badge sitting on the box's top-right corner, straddling the
# border -- it reads as an intentional stat-chip on every theme regardless
# of art style, and never overlaps the label text.
THEME_CANVAS = (1280, 720)
THEME_CIRCLE = (320, 225, 118)  # (cx, cy, radius)
THEME_COL_RIGHT_X = [350, 630, 910, 1190]
THEME_ROW_TOP_Y = [405, 510]
THEME_BADGE_RADIUS = 15
THEME_BADGE_FILL = (10, 10, 18, 235)
THEME_BADGE_OUTLINE = (255, 255, 255, 255)
THEME_BADGE_TEXT = (255, 255, 255, 255)

# Order the 8 boxes appear in, left-to-right then top-to-bottom, matching
# every theme image's fixed layout: Matches, Runs, Strike Rate, Wickets /
# Economy, Highest Score, Best Figures, Rating.
THEME_BOX_ORDER = [
    "matches", "runs", "strike_rate", "wickets",
    "economy", "highest_score", "best_figures", "rating",
]


def _build_theme_fields(row) -> dict:
    strike_rate = round((row["runs_scored"] / row["balls_faced"]) * 100, 1) if row["balls_faced"] else 0.0
    economy = round(row["runs_conceded"] / (row["balls_bowled"] / 6), 1) if row["balls_bowled"] else 0.0
    best_bowling = f"{row['best_bowling']}" if row["best_bowling"] else "-"

    exp = compute_exp(row)
    current_level, _, _ = level_progress(exp)
    rating = current_level.split(" ")[0]

    return {
        "matches": str(row["matches_played"]),
        "runs": str(row["runs_scored"]),
        "strike_rate": str(strike_rate),
        "wickets": str(row["wickets_taken"]),
        "economy": str(economy),
        "highest_score": str(row["highest_score"]),
        "best_figures": best_bowling,
        "rating": rating,
    }


def _composite_theme_sync(photo_bytes: Optional[bytes], row, theme_filename: str) -> Optional[bytes]:
    theme_path = config.userstats_theme_path(theme_filename)
    if not theme_path.exists():
        logger.warning("Missing theme asset %s - falling back to default card.", theme_path)
        return _composite_sync(photo_bytes, row)

    from PIL import Image, ImageDraw, ImageFont

    raw = Image.open(theme_path).convert("RGBA")

    # Normalize every theme (they ship at all sorts of different
    # resolutions/aspect ratios) onto one fixed canvas so the single set
    # of measured coordinates below applies to all of them consistently.
    # Center-crop to the target aspect ratio first, then resize -- this
    # keeps the circle/box layout intact instead of squashing it.
    target_w, target_h = THEME_CANVAS
    target_ratio = target_w / target_h
    w, h = raw.size
    src_ratio = w / h
    if src_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        raw = raw.crop((left, 0, left + new_w, h))
    elif src_ratio < target_ratio:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        raw = raw.crop((0, top, w, top + new_h))
    canvas = raw.resize(THEME_CANVAS, Image.LANCZOS)

    cx, cy, radius = THEME_CIRCLE
    if photo_bytes:
        diameter = radius * 2
        profile = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
        pw, ph = profile.size
        side = min(pw, ph)
        left = (pw - side) // 2
        top = (ph - side) // 2
        profile = profile.crop((left, top, left + side, top + side)).resize(
            (diameter, diameter), Image.LANCZOS
        )
        mask = Image.new("L", (diameter, diameter), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)
        canvas.paste(profile, (cx - radius, cy - radius), mask)

    draw = ImageDraw.Draw(canvas)
    try:
        badge_font = ImageFont.truetype(str(config.USERSTATS_FONT), 16)
    except OSError:
        badge_font = ImageFont.load_default()

    fields = _build_theme_fields(row)

    box_positions = [
        (x, y) for y in THEME_ROW_TOP_Y for x in THEME_COL_RIGHT_X
    ]
    for key, (edge_x, top_y) in zip(THEME_BOX_ORDER, box_positions):
        text = fields[key]
        text_w = draw.textlength(text, font=badge_font)
        pad_x, pad_y = 10, 7
        pill_w = max(text_w + pad_x * 2, THEME_BADGE_RADIUS * 2)
        pill_h = THEME_BADGE_RADIUS * 2
        # Right-anchor the pill so it hugs the box's top-right corner
        # regardless of how wide the value text ends up being.
        x2 = edge_x - 4
        x1 = x2 - pill_w
        y1 = top_y + 2
        y2 = y1 + pill_h
        draw.rounded_rectangle(
            (x1, y1, x2, y2), radius=pill_h // 2,
            fill=THEME_BADGE_FILL, outline=THEME_BADGE_OUTLINE, width=2,
        )
        draw.text(((x1 + x2) / 2, (y1 + y2) / 2), text, font=badge_font, fill=THEME_BADGE_TEXT, anchor="mm")

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


async def build_userstats_card(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, row
) -> Optional[bytes]:
    photo_bytes = None
    try:
        photos = await context.bot.get_user_profile_photos(user_id=user_id, limit=1)
        if photos.total_count > 0 and photos.photos:
            largest = photos.photos[0][-1]
            file = await context.bot.get_file(largest.file_id)
            raw = await file.download_as_bytearray()
            photo_bytes = bytes(raw)
    except TelegramError as exc:
        logger.debug("Could not fetch profile photo for %s: %s", user_id, exc)

    db = context.application.bot_data.get("db")
    theme_key = await db.get_active_theme(user_id) if db else None

    try:
        if theme_key:
            from config import USERSTATS_THEMES
            entry = next((t for t in USERSTATS_THEMES if t[0] == theme_key), None)
            if entry:
                _, _, _, filename = entry
                return await asyncio.to_thread(_composite_theme_sync, photo_bytes, row, filename)
        return await asyncio.to_thread(_composite_sync, photo_bytes, row)
    except Exception as exc:  # noqa: BLE001 - image processing can fail in many ways
        logger.warning("Userstats card compositing failed: %s", exc)
        return None
