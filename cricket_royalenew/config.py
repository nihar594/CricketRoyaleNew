"""
Cricket Royale - Configuration
All tunables, file paths and constants live here so the rest of the
codebase never hard-codes a magic number or path.
"""

import os
import logging
from pathlib import Path

# Loads variables from a .env file in the project root into the process
# environment (if the file exists) -- so locally you just fill in .env
# once instead of re-typing/exporting CRICKET_ROYALE_BOT_TOKEN etc. in the
# terminal every time. Hosts like Railway set real environment variables
# directly, so this is a no-op there (no .env file gets deployed) and
# nothing needs to change for production.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --------------------------------------------------------------------------
# Core bot settings
# --------------------------------------------------------------------------
BOT_TOKEN: str = os.environ.get("CRICKET_ROYALE_BOT_TOKEN", "PUT-YOUR-BOT-TOKEN-HERE")

# Your personal Telegram numeric user ID(s). Owner-only commands
# (/broadcast, /gift, /banuser, /endmatch, /ownerhelp, etc) only work for
# these IDs. Find your ID by messaging @userinfobot on Telegram.
#
# Supports more than one owner: separate multiple IDs with commas, e.g.
#   CRICKET_ROYALE_OWNER_ID=123456789,987654321,555555555
# A single ID still works exactly as before.
_owner_ids_raw = os.environ.get("CRICKET_ROYALE_OWNER_ID", "0")
OWNER_IDS: set[int] = {
    int(part.strip())
    for part in _owner_ids_raw.split(",")
    if part.strip().lstrip("-").isdigit()
} or {0}

# Kept for any older code that expects a single OWNER_ID -- resolves to
# the first ID in OWNER_IDS. New code should check membership in
# OWNER_IDS instead (e.g. "if user.id in config.OWNER_IDS").
OWNER_ID: int = next(iter(OWNER_IDS))

# --------------------------------------------------------------------------
# Gems economy
# --------------------------------------------------------------------------
STARTING_GEMS: int = 5000
OWNER_STARTING_GEMS: int = 865442211458
WINNER_GEMS_REWARD: int = 200   # per player on the winning side
POTM_GEMS_REWARD: int = 100     # bonus for Player of the Match

# --------------------------------------------------------------------------
# Gems Shop
# --------------------------------------------------------------------------
# Cosmetic titles: bought once (permanently owned after that), one can be
# "equipped" at a time and shows up as a prefix on the player's name in
# /teams, /score, and /userstats. (key, display prefix, price in Gems).
SHOP_TITLES: list = [
    ("rookie", "🌱 Rookie", 500),
    ("blazer", "🔥 Blazer", 2500),
    ("champion", "🏆 Champion", 7500),
    ("legend", "👑 Legend", 20000),
    ("goat", "🐐 GOAT", 50000),
]

# Life Saver: a consumable perk. The instant a player who owns one would
# be given out, it's automatically used instead -- they stay not out on
# 0 extra runs and the ball still counts, but the wicket doesn't fall.
# Stacks (buying more than one just adds to their reserve).
LIFE_SAVER_PRICE: int = 3000

# /userstats card background themes: each purchasable and equippable,
# one active at a time, applied to the profile card background instead
# of the default assets/userstats_template.png. (key, display label,
# price, filename under assets/userstats_themes/).
USERSTATS_THEMES: list = [
    ("sky_blue", "☁️ Sky Blue", 3000, "sky_blue.jpg"),
    ("forest_zen", "🌲 Forest Zen", 3000, "forest_zen.jpg"),
    ("white_tech", "⚙️ White Tech", 4000, "white_tech.jpg"),
    ("sakura_dream", "🌸 Sakura Dream", 4000, "sakura_dream.jpg"),
    ("boxing_ring", "🥊 Boxing Ring", 4500, "boxing_ring.jpg"),
    ("spiderweb_red", "🕷️ Spiderweb", 4500, "spiderweb_red.jpg"),
    ("gotham_dark", "🦇 Gotham Night", 5000, "gotham_dark.jpg"),
    ("gothic_bat", "🦇 Gothic Cathedral", 5000, "gothic_bat.jpg"),
    ("dark_rose", "🥀 Dark Rose", 5500, "dark_rose.jpg"),
    ("romantic_night", "🕯️ Romantic Night", 5500, "romantic_night.jpg"),
    ("angel_gold", "😇 Angelic Gold", 7000, "angel_gold.jpg"),
    ("mafia_gold", "🎩 Mafia Boss", 7000, "mafia_gold.jpg"),
    ("barbie_pink", "💗 Barbie", 8000, "barbie_pink.jpg"),
    ("toilet_troll", "🚽 Toilet (Troll)", 100, "toilet_troll.jpg"),
]

# /flip coin-flip gambling odds -- the house always keeps a slight edge.
FLIP_WIN_CHANCE: float = 0.45   # 45% chance the player wins

# /dice gambling odds -- same house edge as /flip. A "win" always shows a
# roll of 4/5/6 and a "loss" always shows 1/2/3, but which one happens is
# still governed by this probability (not a flat 50/50 on the face value).
DICE_WIN_CHANCE: float = 0.45

BASE_DIR: Path = Path(__file__).resolve().parent
ASSETS_DIR: Path = BASE_DIR / "assets"
GIFS_DIR: Path = ASSETS_DIR / "gifs"
DB_PATH: Path = Path(os.environ.get("CRICKET_ROYALE_DB_PATH", str(BASE_DIR / "cricket_royale.db")))

# --------------------------------------------------------------------------
# Gameplay tunables
# --------------------------------------------------------------------------
MIN_OVERS: int = 1
MAX_OVERS: int = 20

MIN_PLAYERS_PER_TEAM: int = 2
MAX_PLAYERS_PER_TEAM: int = 11

LOBBY_TIMEOUT_SECONDS: int = 120  # 2 minutes
LOBBY_REJOIN_EXTENSION_SECONDS: int = 30  # how much /rejoin adds to a Team Match lobby
SPAM_FREE_DECISION_SECONDS: int = 15  # host's window to enable spam-free mode after overs are set

# Balls that rotate the strike when a batter scores them
STRIKE_ROTATING_RUNS = (1, 3, 5)

BALLS_PER_OVER: int = 6

# --------------------------------------------------------------------------
# Asset file paths
# --------------------------------------------------------------------------
# Drop your actual media files into assets/ using these exact base names and
# the bot will pick them up automatically -- .gif, .mp4, or .mov all work,
# since Telegram "GIFs" are sent via send_animation which accepts any of
# them (Telegram itself stores GIFs as soundless MP4s internally).
GIF_SEARCH_EXTENSIONS = (".gif", ".mp4", ".mov")


def _resolve_asset(base_name: str) -> Path:
    """Return the first existing file matching base_name.<ext>, trying
    .gif first -- Telegram renders/loads a GIF noticeably faster than an
    MP4 animation (smaller file, no audio track, near-instant preview),
    which matters here since these fire right after every shot/wicket and
    a slow-loading MP4 shows up as a 1-2s lag before the reaction plays.
    Falls back to .mp4 / .mov if no .gif exists yet for that name. If
    none exist yet, returns the .gif path as a placeholder -- utils/media.py
    falls back to text if it's missing.

    This is called fresh every time a GIF is about to be sent (not cached
    at import time), so dropping in or renaming a file takes effect
    immediately -- no bot restart required."""
    for ext in GIF_SEARCH_EXTENSIONS:
        candidate = GIFS_DIR / f"{base_name}{ext}"
        if candidate.exists():
            return candidate
    return GIFS_DIR / f"{base_name}.gif"


def _resolve_image(base_name: str) -> Path:
    """Same idea as _resolve_asset but for static images (banner). Also
    re-checked live on every call -- see _resolve_asset's note above."""
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = ASSETS_DIR / f"{base_name}{ext}"
        if candidate.exists():
            return candidate
    return ASSETS_DIR / f"{base_name}.jpg"


# NOTE: these are functions, not fixed Path values -- call them (e.g.
# banner_image()) right before sending so a file dropped in after the bot
# started is picked up immediately.
def banner_image() -> Path:
    return _resolve_image("banner")


def team_match_banner() -> Path:
    return _resolve_image("team_match_banner")


def solo_match_banner() -> Path:
    return _resolve_image("solo_match_banner")


def player_of_match_banner() -> Path:
    return _resolve_image("player_of_match_banner")


def userstats_template() -> Path:
    return _resolve_image("userstats_template")


def userstats_theme_path(filename: str) -> Path:
    return ASSETS_DIR / "userstats_themes" / filename


def scoreboard_banner() -> Path:
    return _resolve_image("scoreboard_banner")


USERSTATS_FONT: Path = ASSETS_DIR / "fonts" / "DejaVuSans-Bold.ttf"


def welcome_gif() -> Path:
    return _resolve_asset("welcome")


def toss_gif() -> Path:
    return _resolve_asset("toss")


def wicket_gif() -> Path:
    return _resolve_asset("wicket")


def bowling_gif() -> Path:
    return _resolve_asset("bowling")


def batting_gif() -> Path:
    return _resolve_asset("batting")


def non_striker_gif() -> Path:
    return _resolve_asset("non_striker")


def ball_delivered_gif() -> Path:
    return _resolve_asset("ball_delivered")


def fifty_gif() -> Path:
    return _resolve_asset("fifty")


def century_gif() -> Path:
    return _resolve_asset("century")


def duck_gif() -> Path:
    return _resolve_asset("duck")


def run_gif(runs: int) -> Path:
    return _resolve_asset(str(runs))


# Pixel position/size of the circular photo frame on the Player of the
# Match banner, measured against the original 1264x842 template. If you
# swap in a differently-sized template, re-measure and update these.
POM_CIRCLE_CENTER = (910, 413)
POM_CIRCLE_RADIUS = 210

# Same idea for the /userstats card template (also 1264x842).
USERSTATS_CIRCLE_CENTER = (905, 435)
USERSTATS_CIRCLE_RADIUS = 190

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging() -> None:
    """Configure root logging once at process start."""
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    # Silence the very chatty httpx/telegram internals a bit.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)
