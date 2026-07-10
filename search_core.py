"""Instant hybrid search: dense (SigLIP) + BM25 (LanceDB FTS), RRF fusion."""

import db
import remote

RRF_K = 60


def _dense(vec, k: int, asset_type: str | None) -> list[dict]:
    q = db.get_table().search(vec).metric("cosine").limit(k)
    if asset_type:
        q = q.where(f"asset_type = '{asset_type}'", prefilter=True)
    return q.to_list()


def _fts(query: str, k: int, asset_type: str | None) -> list[dict]:
    try:
        q = db.get_table().search(query, query_type="fts").limit(k)
        if asset_type:
            q = q.where(f"asset_type = '{asset_type}'")
        return q.to_list()
    except Exception:
        return []  # no FTS index yet or no text


def rrf_merge(result_lists: list[list[dict]], k: int) -> list[dict]:
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    for results in result_lists:
        for rank, row in enumerate(results):
            rid = row["id"]
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)
            rows.setdefault(rid, row)
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:k]
    out = []
    for rid, score in ranked:
        row = db.strip_vector(rows[rid])
        row["score"] = round(score, 5)
        out.append(row)
    return out


def instant_search(
    query: str, k: int = 24, asset_type: str | None = None
) -> list[dict]:
    vec = remote.embed_texts([query])[0]
    dense = _dense(vec, k * 2, asset_type)
    sparse = _fts(query, k * 2, asset_type)
    return rrf_merge([dense, sparse], k)


def dense_search(query: str, k: int = 24, asset_type: str | None = None) -> list[dict]:
    vec = remote.embed_texts([query])[0]
    return [db.strip_vector(r) for r in _dense(vec, k, asset_type)]


def text_search(keywords: str, k: int = 24, asset_type: str | None = None) -> list[dict]:
    return [db.strip_vector(r) for r in _fts(keywords, k, asset_type)]
