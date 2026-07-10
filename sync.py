"""Watched folders: persistence + incremental sync into the index.

Folders are stored in data/folders.json. A daemon thread rescans them
every SYNC_INTERVAL_SECONDS (or immediately via request_sync()):
new media files are ingested, files deleted from disk are pruned.
"""

import json
import threading
import time
from pathlib import Path

import db
import ingest
from config import FOLDERS_FILE, PHOTO_EXTS, SYNC_INTERVAL_SECONDS, VIDEO_EXTS

_folders_lock = threading.Lock()
_sync_lock = threading.Lock()
_wake = threading.Event()

status: dict = {
    "running": False,
    "last_sync": None,
    "last_added": 0,
    "last_removed": 0,
    "last_error": None,
}


def load_folders() -> list[str]:
    try:
        return json.loads(FOLDERS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_folders(folders: list[str]):
    FOLDERS_FILE.write_text(json.dumps(folders, indent=2))


def add_folder(raw: str) -> list[str]:
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"not a directory: {path}")
    with _folders_lock:
        folders = load_folders()
        if str(path) in folders:
            raise ValueError("folder already watched")
        folders.append(str(path))
        _save_folders(folders)
    request_sync()
    return folders


def remove_folder(raw: str) -> list[str]:
    path = str(Path(raw).expanduser().resolve())
    with _folders_lock:
        folders = load_folders()
        if path not in folders:
            raise ValueError("folder not watched")
        folders.remove(path)
        _save_folders(folders)
    return folders


def sync_once() -> dict:
    if not _sync_lock.acquire(blocking=False):
        return status
    status.update(running=True, last_error=None)
    try:
        indexed = db.indexed_uris()
        on_disk: set[str] = set()
        scanned_roots: list[str] = []
        photos: list[Path] = []
        videos: list[Path] = []

        for folder in (Path(f) for f in load_folders()):
            if not folder.is_dir():
                continue  # unmounted/deleted root: never prune what we can't see
            scanned_roots.append(str(folder))
            for f in folder.rglob("*"):
                if not f.is_file():
                    continue
                ext = f.suffix.lower()
                if ext not in PHOTO_EXTS and ext not in VIDEO_EXTS:
                    continue
                uri = str(f.resolve())
                on_disk.add(uri)
                if uri in indexed:
                    continue
                (photos if ext in PHOTO_EXTS else videos).append(f)

        added = len(photos) + len(videos)
        if photos:
            print(f"[sync] ingesting {len(photos)} new photos")
            ingest.ingest_photos(photos)
        for v in videos:
            print(f"[sync] ingesting video {v.name}")
            ingest.ingest_video(v)

        sep = "/"
        stale = [
            u for u in indexed
            if u not in on_disk
            and any(u.startswith(root + sep) for root in scanned_roots)
            and not Path(u).exists()
        ]
        removed = db.delete_by_uris(stale)
        if removed:
            print(f"[sync] pruned {removed} segments for {len(stale)} deleted files")

        if added or removed:
            db.rebuild_fts()
        status.update(last_sync=time.time(), last_added=added, last_removed=removed)
    except Exception as e:
        print(f"[sync] error: {e}")
        status["last_error"] = str(e)
    finally:
        status["running"] = False
        _sync_lock.release()
    return status


def request_sync():
    _wake.set()


def start_background_loop():
    def loop():
        while True:
            _wake.wait(timeout=SYNC_INTERVAL_SECONDS)
            _wake.clear()
            if load_folders():
                sync_once()

    threading.Thread(target=loop, daemon=True, name="folder-sync").start()
