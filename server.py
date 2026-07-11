"""Local web server. Run:  uvicorn server:app --port 8000

Binds localhost only by default — media endpoints are unauthenticated.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import search_core
import sync
from agent import deep_search
from config import AGENT_ENGINE, ROOT, THUMBS_DIR
from vlm_agent import deep_search_vlm


@asynccontextmanager
async def lifespan(app: FastAPI):
    sync.start_background_loop()
    yield


app = FastAPI(title="video-search", lifespan=lifespan)

app.mount("/thumbs", StaticFiles(directory=str(THUMBS_DIR)), name="thumbs")


@app.get("/")
def index():
    # always revalidate the shell so ?v= bumps on assets take effect
    return FileResponse(
        ROOT / "static" / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def _present(seg: dict) -> dict:
    return {
        "id": seg["id"],
        "asset_id": seg["asset_id"],
        "type": seg["asset_type"],
        "t_start": seg["t_start"],
        "t_end": seg["t_end"],
        "caption": seg.get("caption", ""),
        "transcript": (seg.get("transcript") or "")[:200],
        "tags": seg.get("tags", ""),
        "enriched": bool(seg.get("enriched")),
        "score": seg.get("score"),
        "thumb": (
            f"/thumbs/{seg['id']}.jpg"
            if seg["asset_type"] == "video"
            else f"/api/file/{seg['id']}"
        ),
        "media": f"/api/file/{seg['id']}",
    }


@app.get("/api/browse")
def api_browse(
    type: str | None = Query(None, pattern="^(photo|video)$"),
    limit: int = Query(500, le=2000),
):
    tbl = db.get_table()
    q = tbl.search().limit(100_000)
    if type:
        q = q.where(f"asset_type = '{type}'")
    rows = [db.strip_vector(r) for r in q.to_list()]

    assets: dict[str, dict] = {}
    for r in rows:
        a = assets.get(r["asset_id"])
        if a is None:
            assets[r["asset_id"]] = {"rep": r, "shots": 1, "duration": r["t_end"]}
        else:
            a["shots"] += 1
            a["duration"] = max(a["duration"], r["t_end"])
            if r["t_start"] < a["rep"]["t_start"]:
                a["rep"] = r

    items = sorted(assets.values(), key=lambda a: -a["rep"]["created"])[:limit]
    out = []
    for a in items:
        d = _present(a["rep"])
        if d["type"] == "video":
            d["duration"] = round(a["duration"], 1)
            d["shots"] = a["shots"]
        out.append(d)
    return {"results": out, "total_assets": len(assets)}


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=1),
    k: int = Query(24, le=100),
    type: str | None = Query(None, pattern="^(photo|video)$"),
):
    return {"results": [_present(s) for s in search_core.instant_search(q, k, type)]}


@app.get("/api/deep")
async def api_deep(
    q: str = Query(..., min_length=1),
    engine: str | None = Query(None, pattern="^(vlm|claude)$"),
):
    run = deep_search_vlm if (engine or AGENT_ENGINE) == "vlm" else deep_search

    async def stream():
        async for event in run(q):
            if event.get("type") == "results":
                event["segments"] = [_present(s) for s in event["segments"]]
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class FolderIn(BaseModel):
    path: str


def _folders_payload() -> dict:
    folders = [
        {"path": f, "exists": Path(f).is_dir()} for f in sync.load_folders()
    ]
    return {"folders": folders, "status": sync.status}


@app.get("/api/folders")
def api_folders():
    return _folders_payload()


@app.post("/api/folders")
def api_add_folder(body: FolderIn):
    try:
        sync.add_folder(body.path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _folders_payload()


@app.delete("/api/folders")
def api_remove_folder(path: str = Query(..., min_length=1)):
    try:
        sync.remove_folder(path)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return _folders_payload()


@app.post("/api/sync")
def api_sync():
    sync.request_sync()
    return {"status": sync.status}


@app.get("/api/stats")
def api_stats():
    tbl = db.get_table()
    rows = (
        tbl.search()
        .select(["asset_id", "asset_type", "uri", "t_end", "enriched", "transcript"])
        .limit(1_000_000)
        .to_list()
    )

    photos: set[str] = set()
    videos: dict[str, float] = {}  # asset_id -> max t_end
    uri_type: dict[str, str] = {}
    shots = 0
    enriched = 0
    transcribed = 0
    for r in rows:
        uri_type[r["uri"]] = r["asset_type"]
        if r["asset_type"] == "video":
            shots += 1
            videos[r["asset_id"]] = max(videos.get(r["asset_id"], 0.0), r["t_end"])
        else:
            photos.add(r["asset_id"])
        if r["enriched"]:
            enriched += 1
        if r.get("transcript"):
            transcribed += 1

    bytes_by_type = {"photo": 0, "video": 0}
    missing = 0
    for uri, atype in uri_type.items():
        try:
            bytes_by_type[atype] += Path(uri).stat().st_size
        except OSError:
            missing += 1

    thumb_bytes = sum(
        f.stat().st_size for f in THUMBS_DIR.glob("*.jpg") if f.is_file()
    )

    return {
        "photos": len(photos),
        "videos": len(videos),
        "segments": len(rows),
        "shots": shots,
        "video_seconds": round(sum(videos.values()), 1),
        "enriched": enriched,
        "enriched_pct": round(100 * enriched / len(rows), 1) if rows else 0.0,
        "transcribed": transcribed,
        "storage": {
            "photo_bytes": bytes_by_type["photo"],
            "video_bytes": bytes_by_type["video"],
            "thumb_bytes": thumb_bytes,
            "total_bytes": bytes_by_type["photo"] + bytes_by_type["video"] + thumb_bytes,
            "missing_files": missing,
        },
    }


@app.get("/api/file/{segment_id}")
def api_file(segment_id: str):
    segs = db.get_segments([segment_id])
    if not segs:
        raise HTTPException(404)
    path = Path(segs[0]["uri"])
    if not path.exists():
        raise HTTPException(404, "media file moved or deleted")
    return FileResponse(path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
