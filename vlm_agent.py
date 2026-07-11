"""Deep search driven by the Qwen2.5-VL endpoint itself (no Claude).

Same tools and SSE event shape as agent.deep_search, but the reasoning
loop is a ReAct-style JSON protocol running on the Modal VLM's `chat`
method — brain and eyes share one warm L4 container.
"""

import asyncio
import json
from typing import AsyncGenerator

import db
import remote
import search_core
from agent import MAX_VLM_IMAGES_PER_QUERY, _brief, _fmt, _parse_caption, _thumb_bytes
from config import AGENT_MAX_TURNS

MAX_OBSERVATION_CHARS = 4000

SYSTEM_PROMPT = """You are a search agent for a personal photo/video library. You cannot see images directly; tools return text.

Index rows: id, asset_type (photo|video), t_start/t_end seconds (video only), caption (empty until enriched), transcript (spoken words), tags, enriched flag.

TOOLS:
1. search_dense — semantic visual search. args: {"query": "<visual description>", "k": 20}
2. search_text — keyword (BM25) search over transcripts/captions/tags. args: {"keywords": "<words>", "k": 20}
3. look — vision model answers a question about each segment's keyframe (max 8 ids). Use to verify negation, counts, visible text, spatial relations. args: {"segment_ids": ["<id>", ...], "question": "<question>"}
4. enrich — vision model writes caption+tags into the index for unenriched segments (max 8 ids). Returns the new captions. args: {"segment_ids": ["<id>", ...]}
5. finish — submit final ranked results, best first, max 20 ids. Call exactly once, at the end. args: {"segment_ids": ["<id>", ...], "summary": "<one sentence>"}

RULES:
- Respond with EXACTLY one JSON object and nothing else, in this form:
  {"thought": "<brief reasoning>", "tool": "<tool name>", "args": {...}}
- Start with search_dense and/or search_text; try multiple phrasings if results are weak.
- Verify doubtful candidates with look; trust look answers over search rank.
- enrich promising candidates that have enriched=false.
- Be economical; then call finish. Always call finish, even if nothing matched."""


def _parse_action(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def _ids(args: dict) -> list[str]:
    v = args.get("segment_ids", [])
    if isinstance(v, str):
        v = v.replace(",", " ").split()
    return [str(x) for x in v]


def _exec_tool(state: dict, name: str, args: dict) -> str:
    if name == "search_dense":
        rows = search_core.dense_search(
            str(args.get("query", "")), k=min(int(args.get("k") or 20), 50)
        )
        state["last_ids"] = [r["id"] for r in rows]
        return _fmt(rows)

    if name == "search_text":
        rows = search_core.text_search(
            str(args.get("keywords", "")), k=min(int(args.get("k") or 20), 50)
        )
        state["last_ids"] = [r["id"] for r in rows]
        return _fmt(rows)

    if name == "look":
        ids = _ids(args)[:8]
        segs = db.get_segments(ids)
        budget = MAX_VLM_IMAGES_PER_QUERY - state["vlm_calls"]
        if budget <= 0:
            return "VLM budget exhausted; decide from current evidence."
        segs = segs[:budget]
        images, kept = [], []
        for s in segs:
            b = _thumb_bytes(s)
            if b:
                images.append(b)
                kept.append(s)
        if not images:
            return "no thumbnails found for those ids"
        state["vlm_calls"] += len(images)
        answers = remote.vlm_look(images, str(args.get("question", "")))
        return json.dumps(
            {s["id"]: a for s, a in zip(kept, answers)}, ensure_ascii=False
        )

    if name == "enrich":
        ids = _ids(args)[:8]
        segs = [s for s in db.get_segments(ids) if not s.get("enriched")]
        budget = MAX_VLM_IMAGES_PER_QUERY - state["vlm_calls"]
        segs = segs[: max(budget, 0)]
        if not segs:
            return "nothing to enrich (already enriched or budget exhausted)"
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
        return json.dumps(result, ensure_ascii=False)

    return f"unknown tool '{name}'. Valid: search_dense, search_text, look, enrich, finish."


async def deep_search_vlm(user_query: str) -> AsyncGenerator[dict, None]:
    """Yields SSE-ready events: text / tool / results / error."""
    state = {
        "results": [],
        "summary": "",
        "vlm_calls": 0,
        "enriched": 0,
        "last_ids": [],
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Search request: {user_query}"},
    ]
    seen_calls: set[str] = set()
    finished = False

    try:
        for turn in range(AGENT_MAX_TURNS):
            if turn == AGENT_MAX_TURNS - 1:
                messages.append(
                    {"role": "user", "content": "Turn budget exhausted. Call finish now with your best results."}
                )
            raw = await asyncio.to_thread(remote.vlm_chat, messages, 512)
            messages.append({"role": "assistant", "content": raw})
            action = _parse_action(raw)
            if not action or not isinstance(action.get("args", {}), dict):
                messages.append(
                    {"role": "user", "content": 'Invalid response. Reply with exactly one JSON object: {"thought": "...", "tool": "...", "args": {...}}'}
                )
                continue

            name = str(action.get("tool", ""))
            args = action.get("args") or {}
            thought = str(action.get("thought", "")).strip()
            if thought:
                yield {"type": "text", "text": thought}
            yield {"type": "tool", "name": name, "input": _brief(args)}

            if name == "finish":
                state["results"] = _ids(args)[:20]
                state["summary"] = str(args.get("summary", ""))
                finished = True
                break

            key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            if key in seen_calls:
                obs = "You already ran this exact call. Try a different query or call finish."
            else:
                seen_calls.add(key)
                obs = await asyncio.to_thread(_exec_tool, state, name, args)
            messages.append(
                {"role": "user", "content": f"Observation: {obs[:MAX_OBSERVATION_CHARS]}"}
            )
    except Exception as e:
        yield {"type": "error", "message": str(e)}
        return

    if not finished and state["last_ids"]:
        state["results"] = state["last_ids"][:20]
        state["summary"] = "Agent did not converge; returning last search results."

    if state["enriched"]:
        db.rebuild_fts()  # make new captions visible to BM25
    segments = db.get_segments(state["results"]) if state["results"] else []
    yield {
        "type": "results",
        "summary": state["summary"],
        "enriched": state["enriched"],
        "segments": segments,
    }
