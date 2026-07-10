"""Deep search: Claude agent orchestrating the index + 4B VLM on Modal.

The agent never sees pixels — the VLM reads frames and returns text.
`enrich` writes captions/tags back to LanceDB (lazy index enrichment).
"""

import json
from pathlib import Path
from typing import AsyncGenerator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

import db
import remote
import search_core
from config import AGENT_MAX_TURNS

MAX_VLM_IMAGES_PER_QUERY = 40

SYSTEM_PROMPT = """You are a media search agent over the user's personal photo/video library.

Index structure: each row is a photo or a video *shot* (segment) with:
id, asset_type (photo|video), t_start/t_end (seconds, video only),
caption (may be empty if not yet enriched), transcript (spoken words), tags.

Workflow:
1. Decompose the user's query. Run search_dense (visual semantics) and
   search_text (keywords in transcripts/captions/tags) — multiple angles help.
2. Candidates with enriched=0 have no caption. Use enrich on promising ones —
   this writes captions/tags into the index permanently (helps future queries).
3. Use look to verify specific claims embeddings get wrong: negation
   ("no helmet"), counts, spatial relations, text-in-image. Trust look over
   embedding rank.
4. For multi-event queries ("X then Y"), find both events as separate segments
   of the SAME asset_id and check t_start ordering.
5. Call finish with the final ranked segment ids (best first, max 20) and a
   one-sentence summary. Always call finish, even if nothing matched.

Be economical: don't enrich/look more segments than needed to answer well."""


def _fmt(rows: list[dict]) -> str:
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "type": r["asset_type"],
                "asset_id": r["asset_id"],
                "t": [r["t_start"], r["t_end"]] if r["asset_type"] == "video" else None,
                "caption": r.get("caption") or None,
                "transcript": (r.get("transcript") or "")[:300] or None,
                "tags": r.get("tags") or None,
                "enriched": bool(r.get("enriched")),
            }
        )
    return json.dumps(out, ensure_ascii=False)


def _thumb_bytes(seg: dict) -> bytes | None:
    """Video: cached keyframe. Photo: original downscaled in memory."""
    thumb = seg.get("thumb") or ""
    if thumb and Path(thumb).exists():
        return Path(thumb).read_bytes()
    if seg["asset_type"] == "photo" and Path(seg["uri"]).exists():
        from PIL import Image

        from ingest import downscaled_jpeg

        return downscaled_jpeg(Image.open(seg["uri"]))
    return None


def build_tools(state: dict):
    @tool(
        "search_dense",
        "Semantic (visual) search over image embeddings. Natural-language visual description works best.",
        {"query": str, "k": int},
    )
    async def search_dense(args):
        rows = search_core.dense_search(args["query"], k=min(int(args.get("k", 20)), 50))
        return {"content": [{"type": "text", "text": _fmt(rows)}]}

    @tool(
        "search_text",
        "Keyword (BM25) search over transcripts, captions and tags. Good for spoken words, names, text.",
        {"keywords": str, "k": int},
    )
    async def search_text(args):
        rows = search_core.text_search(args["keywords"], k=min(int(args.get("k", 20)), 50))
        return {"content": [{"type": "text", "text": _fmt(rows)}]}

    @tool(
        "look",
        "Ask the vision model a question about specific segments' keyframes. "
        "Use to verify negation, counts, spatial relations, visible text. Max 8 ids per call.",
        {"segment_ids": list, "question": str},
    )
    async def look(args):
        ids = list(args["segment_ids"])[:8]
        segs = db.get_segments(ids)
        budget = MAX_VLM_IMAGES_PER_QUERY - state["vlm_calls"]
        if budget <= 0:
            return {"content": [{"type": "text", "text": "VLM budget exhausted; decide from current evidence."}]}
        segs = segs[:budget]
        images, kept = [], []
        for s in segs:
            b = _thumb_bytes(s)
            if b:
                images.append(b)
                kept.append(s)
        if not images:
            return {"content": [{"type": "text", "text": "no thumbnails found for those ids"}]}
        state["vlm_calls"] += len(images)
        answers = remote.vlm_look(images, args["question"])
        result = {s["id"]: a for s, a in zip(kept, answers)}
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

    @tool(
        "enrich",
        "Generate caption+tags for segments and write them into the index permanently. "
        "Use on promising unenriched candidates. Max 8 ids per call. Returns the new captions.",
        {"segment_ids": list},
    )
    async def enrich(args):
        ids = list(args["segment_ids"])[:8]
        segs = [s for s in db.get_segments(ids) if not s.get("enriched")]
        budget = MAX_VLM_IMAGES_PER_QUERY - state["vlm_calls"]
        segs = segs[: max(budget, 0)]
        if not segs:
            return {"content": [{"type": "text", "text": "nothing to enrich (already enriched or budget exhausted)"}]}
        images, kept = [], []
        for s in segs:
            b = _thumb_bytes(s)
            if b:
                images.append(b)
                kept.append(s)
        state["vlm_calls"] += len(images)
        raw = remote.vlm_caption(images)
        result = {}
        for s, r in zip(kept, raw):
            caption, tags = _parse_caption(r)
            db.update_enrichment(s["id"], caption, tags, s.get("transcript", ""))
            result[s["id"]] = {"caption": caption, "tags": tags}
        state["enriched"] += len(result)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

    @tool(
        "finish",
        "Submit final ranked results. Call exactly once when done.",
        {"segment_ids": list, "summary": str},
    )
    async def finish(args):
        state["results"] = [str(s) for s in args["segment_ids"]][:20]
        state["summary"] = str(args.get("summary", ""))
        return {"content": [{"type": "text", "text": "results submitted"}]}

    return [search_dense, search_text, look, enrich, finish]


def _parse_caption(raw: str) -> tuple[str, str]:
    try:
        start, end = raw.find("{"), raw.rfind("}")
        obj = json.loads(raw[start : end + 1])
        caption = str(obj.get("caption", "")).strip()
        tags = ", ".join(str(t) for t in obj.get("tags", []))
        return caption, tags
    except Exception:
        return raw.strip()[:500], ""


async def deep_search(user_query: str) -> AsyncGenerator[dict, None]:
    """Yields SSE-ready events: text / tool / results / error."""
    state = {"results": [], "summary": "", "vlm_calls": 0, "enriched": 0}
    server = create_sdk_mcp_server(name="search", version="1.0.0", tools=build_tools(state))
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"search": server},
        allowed_tools=[
            "mcp__search__search_dense",
            "mcp__search__search_text",
            "mcp__search__look",
            "mcp__search__enrich",
            "mcp__search__finish",
        ],
        max_turns=AGENT_MAX_TURNS,
        permission_mode="bypassPermissions",
    )

    try:
        async for message in query(prompt=f"Search request: {user_query}", options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        yield {"type": "text", "text": block.text.strip()}
                    elif isinstance(block, ToolUseBlock):
                        yield {
                            "type": "tool",
                            "name": block.name.replace("mcp__search__", ""),
                            "input": _brief(block.input),
                        }
            elif isinstance(message, ResultMessage):
                break
    except Exception as e:
        yield {"type": "error", "message": str(e)}
        return

    if state["enriched"]:
        db.rebuild_fts()  # make new captions visible to BM25
    segments = db.get_segments(state["results"]) if state["results"] else []
    yield {
        "type": "results",
        "summary": state["summary"],
        "enriched": state["enriched"],
        "segments": segments,
    }


def _brief(tool_input) -> str:
    try:
        s = json.dumps(tool_input, ensure_ascii=False)
        return s if len(s) <= 200 else s[:200] + "…"
    except Exception:
        return str(tool_input)[:200]
