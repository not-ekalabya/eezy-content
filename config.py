import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
DATA_DIR = ROOT / "data"
DB_DIR = DATA_DIR / "lancedb"
THUMBS_DIR = DATA_DIR / "thumbs"

MODAL_APP_NAME = "video-search"
VECTOR_DIM = 1152  # siglip2-so400m

THUMB_MAX_PX = 512
SHOT_MAX_SECONDS = 20.0
SHOT_MIN_SECONDS = 1.0

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

AGENT_MAX_TURNS = 16
AGENT_ENGINE = os.environ.get("AGENT_ENGINE", "vlm")  # vlm | claude

FOLDERS_FILE = DATA_DIR / "folders.json"
SYNC_INTERVAL_SECONDS = 60

for d in (DATA_DIR, THUMBS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _resolve_ffmpeg() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    from static_ffmpeg import run

    return run.get_or_fetch_platform_executables_else_raise()


FFMPEG, FFPROBE = _resolve_ffmpeg()
