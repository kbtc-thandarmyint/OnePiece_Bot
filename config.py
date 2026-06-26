"""Configuration loaded from environment / .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Bot Token (from @BotFather) ──────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# ── Telegram API credentials (for downloader/uploader scripts) ───────
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "").strip()
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()

# ── Media directory (local video storage) ────────────────────────────
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "./media")).expanduser().resolve()

# ── Access control ───────────────────────────────────────────────────
_allowed = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS = {
    int(x) for x in _allowed.replace(" ", "").split(",") if x
}

# ── Bot display settings ────────────────────────────────────────────
RANGE_SIZE = int(os.getenv("RANGE_SIZE", "25"))      # episodes per range button
TOTAL_EPISODES = int(os.getenv("TOTAL_EPISODES", "0"))  # 0 = auto-detect from manifest

# ── Storage channel (where videos are stored for forwarding) ────────
# This is the channel ID where uploader.py posted all the videos.
# The bot forwards from this channel — no size limit!
STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID", "0"))

# ── Source bot (for the downloader) ──────────────────────────────────
SOURCE_BOT = os.getenv("SOURCE_BOT", "OnePiece_MMSub_s1_bot")

# ── Manifest files ──────────────────────────────────────────────────
MANIFEST_FILE = Path(os.getenv("MANIFEST_FILE", "manifest.json"))
CHANNEL_MANIFEST_FILE = Path(os.getenv("CHANNEL_MANIFEST_FILE", "channel_manifest.json"))

# ── Legacy settings (kept for compatibility) ────────────────────────
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "8"))
CACHE_FILE = Path(__file__).parent / "file_id_cache.json"
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def validate() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "put-your-token-here":
        raise SystemExit(
            "BOT_TOKEN is not set. Copy .env.example to .env and add your "
            "token from @BotFather."
        )
    if not MEDIA_DIR.exists():
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
