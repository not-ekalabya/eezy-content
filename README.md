# Lumina — semantic media search

Semantic search over your photos and videos. Ingest is cheap and non-LLM (shot detection → SigLIP-2 embeddings + Whisper ASR + OCR → LanceDB). Intelligence lives on the query side: an agent (Claude) with search/look/enrich tools inspects candidate frames through a VLM and writes captions/tags back to the index, so the library gets smarter in the regions you actually search.

- **GPU inference** runs serverless on [Modal](https://modal.com) (`modal_app.py`)
- **Index** is local LanceDB under `data/lancedb`, thumbnails under `data/thumbs`
- **UI** is a static single-page app (`static/`) served by FastAPI, styled after the Lumina design system (`stitch_semantic_ai_photo_gallery/`)

## Prerequisites

- Python 3.11+
- A [Modal](https://modal.com) account (for embeddings, ASR, OCR, and the VLM)
- Claude Code installed and authenticated, or `ANTHROPIC_API_KEY` set — the deep-search agent runs on `claude-agent-sdk`
- `ffmpeg`/`ffprobe` on PATH (auto-downloaded via `static-ffmpeg` if missing)

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# authenticate Modal (once)
.venv/bin/modal setup

# optional — deploy the GPU app for persistent serving (app name: "video-search")
.venv/bin/modal deploy modal_app.py
```

Deploying is optional. If no deployed app exists, the server automatically starts an **ephemeral** Modal app in-process on the first GPU call (embedding, ASR, VLM) — it lives and dies with the server process. First call after a server start pays the container cold-start.

> Note: after redeploying `modal_app.py`, stop warm containers (`modal container stop`) or they keep serving stale code. `modal serve` is not needed — served apps can't be looked up by name.

## Ingest media

```bash
.venv/bin/python ingest.py /path/to/photos_and_videos [more paths...]
```

Accepts files or directories. Photos: `.jpg .jpeg .png .webp .bmp`. Videos: `.mp4 .mov .mkv .webm .avi .m4v`. Videos are split into shots; each shot becomes a searchable segment with `(video_id, t_start, t_end)`.

### Synced folders

Instead of one-off ingests you can watch folders: click **Folders** in the UI top bar (or use the API) and add an absolute path. Watched folders are rescanned every 60 s (`SYNC_INTERVAL_SECONDS` in `config.py`) — new media is ingested automatically, and files deleted from disk are pruned from the index (thumbnails included). The folder list persists in `data/folders.json`. Removing a folder stops syncing it but keeps already-indexed media.

## Start the server + UI

```bash
.venv/bin/uvicorn server:app --port 8000
```

Open **http://localhost:8000** — the UI is served at the root, no build step required.

## Using the UI

- **Browse** — library loads on open; filter with the `All media / Photos / Videos` chips
- **Instant search** — type a query (`⌘K` / `Ctrl+K` focuses the search bar) and press Enter; pure vector + text retrieval, no LLM
- **Deep search** — toggle the indigo `Deep search` chip before submitting. Instant results appear first, then the agent refines them; its reasoning and tool calls stream live into the **AI Insights** panel. Segments the agent inspects get captions/tags written back to the index
- **Viewer** — click any result. Videos seek to the matched segment; the side panel shows AI caption, transcript, and semantic tags

## API

| Endpoint | Description |
|---|---|
| `GET /api/browse?type=photo\|video` | List library items |
| `GET /api/search?q=...&type=...&k=...` | Instant hybrid search |
| `GET /api/deep?q=...` | Deep agentic search (SSE stream) |
| `GET /api/file/{id}` | Serve original media |
| `/thumbs/{id}.jpg` | Thumbnails |
| `GET /api/folders` | List watched folders + sync status |
| `POST /api/folders` `{"path": "..."}` | Watch a folder (triggers immediate sync) |
| `DELETE /api/folders?path=...` | Stop watching a folder |
| `POST /api/sync` | Trigger a sync now |
