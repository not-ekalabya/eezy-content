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
from config import ROOT, THUMBS_DIR


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
async def api_deep(q: str = Query(..., min_length=1)):
    async def stream():
        async for event in deep_search(q):
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
