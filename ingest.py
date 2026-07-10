"""Ingest photos and videos into the search index.

Usage:
    python ingest.py <path> [<path> ...]

Paths may be files or directories (recursed). Cheap non-LLM ingest:
embeddings + shots + transcript only. Captions/tags arrive lazily via
the agent's enrich tool at query time.
"""

import argparse
import io
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from PIL import Image

import db
import remote
from config import (
    FFMPEG,
    PHOTO_EXTS,
    SHOT_MAX_SECONDS,
    SHOT_MIN_SECONDS,
    THUMB_MAX_PX,
    THUMBS_DIR,
    VIDEO_EXTS,
)

EMBED_BATCH = 32


def downscaled_jpeg(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


# ---------- photos ----------


def ingest_photos(paths: list[Path]):
    pending: list[tuple[dict, bytes]] = []
    for p in paths:
        try:
            seg_id = new_id()
            img = Image.open(p)
            # photos: no thumb file on disk — grid and VLM read the original;
            # downscaled bytes here only feed the embedding call
            thumb_bytes = downscaled_jpeg(img)
            row = {
                "id": seg_id,
                "asset_id": seg_id,
                "asset_type": "photo",
                "uri": str(p.resolve()),
                "thumb": "",
                "t_start": 0.0,
                "t_end": 0.0,
                "caption": "",
                "transcript": "",
                "tags": "",
                "text": "",
                "enriched": 0,
                "created": time.time(),
            }
            pending.append((row, thumb_bytes))
        except Exception as e:
            print(f"  skip {p}: {e}")
        if len(pending) >= EMBED_BATCH:
            _flush_photos(pending)
            pending = []
    _flush_photos(pending)


def _flush_photos(pending):
    if not pending:
        return
    vecs = remote.embed_images([b for _, b in pending])
    rows = []
    for (row, _), vec in zip(pending, vecs):
        row["vector"] = vec
        rows.append(row)
    db.insert_rows(rows)
    print(f"  indexed {len(rows)} photos")


# ---------- videos ----------


def detect_shots(path: Path) -> list[tuple[float, float]]:
    from scenedetect import ContentDetector, detect

    scenes = detect(str(path), ContentDetector(threshold=27.0))
    shots = [(s.get_seconds(), e.get_seconds()) for s, e in scenes]
    if not shots:
        duration = probe_duration(path)
        shots = [(0.0, duration)]
    # normalize: split long shots, drop micro shots
    out: list[tuple[float, float]] = []
    for start, end in shots:
        if end - start < SHOT_MIN_SECONDS:
            continue
        t = start
        while t < end:
            out.append((t, min(t + SHOT_MAX_SECONDS, end)))
            t += SHOT_MAX_SECONDS
    return out or [(0.0, probe_duration(path))]


def probe_duration(path: Path) -> float:
    from config import FFPROBE

    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def extract_keyframe(path: Path, t: float) -> bytes | None:
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=True) as f:
        r = subprocess.run(
            [FFMPEG, "-y", "-ss", f"{t:.3f}", "-i", str(path),
             "-frames:v", "1", "-q:v", "3",
             "-vf", f"scale='min({THUMB_MAX_PX},iw)':-2",
             f.name],
            capture_output=True,
        )
        if r.returncode != 0:
            return None
        data = Path(f.name).read_bytes()
        return data or None


def extract_audio(path: Path) -> bytes | None:
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as f:
        r = subprocess.run(
            [FFMPEG, "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "libopus", "-b:a", "24k", f.name],
            capture_output=True,
        )
        if r.returncode != 0:
            return None
        data = Path(f.name).read_bytes()
        return data if len(data) > 200 else None


def transcript_for_range(segments: list[dict], start: float, end: float) -> str:
    parts = [
        s["text"] for s in segments
        if s["end"] > start and s["start"] < end
    ]
    return " ".join(parts).strip()


def ingest_video(path: Path):
    asset_id = new_id()
    print(f"  shots...", end=" ", flush=True)
    shots = detect_shots(path)
    print(f"{len(shots)} shots")

    print(f"  transcript...", end=" ", flush=True)
    audio = extract_audio(path)
    asr = remote.whisper_transcribe(audio) if audio else []
    print(f"{len(asr)} speech segments")

    rows, frames = [], []
    for start, end in shots:
        mid = (start + end) / 2
        frame = extract_keyframe(path, mid)
        if frame is None:
            continue
        seg_id = new_id()
        thumb_path = THUMBS_DIR / f"{seg_id}.jpg"
        thumb_path.write_bytes(frame)
        transcript = transcript_for_range(asr, start, end)
        rows.append({
            "id": seg_id,
            "asset_id": asset_id,
            "asset_type": "video",
            "uri": str(path.resolve()),
            "thumb": str(thumb_path),
            "t_start": round(start, 2),
            "t_end": round(end, 2),
            "caption": "",
            "transcript": transcript,
            "tags": "",
            "text": db.build_text("", transcript, ""),
            "enriched": 0,
            "created": time.time(),
        })
        frames.append(frame)

    print(f"  embedding {len(frames)} keyframes...")
    for i in range(0, len(frames), EMBED_BATCH):
        vecs = remote.embed_images(frames[i : i + EMBED_BATCH])
        for row, vec in zip(rows[i : i + EMBED_BATCH], vecs):
            row["vector"] = vec
    db.insert_rows(rows)
    print(f"  indexed {len(rows)} video segments")


# ---------- main ----------


def collect_files(paths: list[str]) -> tuple[list[Path], list[Path]]:
    photos, videos = [], []
    for raw in paths:
        p = Path(raw)
        candidates = (
            [f for f in p.rglob("*") if f.is_file()] if p.is_dir() else [p]
        )
        for f in candidates:
            ext = f.suffix.lower()
            if ext in PHOTO_EXTS:
                photos.append(f)
            elif ext in VIDEO_EXTS:
                videos.append(f)
    return photos, videos


def main():
    ap = argparse.ArgumentParser(description="Ingest media into search index")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()

    photos, videos = collect_files(args.paths)
    print(f"found {len(photos)} photos, {len(videos)} videos")
    if not photos and not videos:
        sys.exit(1)

    if photos:
        print("photos:")
        ingest_photos(photos)
    for v in videos:
        print(f"video: {v.name}")
        ingest_video(v)

    print("rebuilding text index...")
    db.rebuild_fts()
    print("done")


if __name__ == "__main__":
    main()
