import lancedb
import pyarrow as pa

from config import DB_DIR, THUMBS_DIR, VECTOR_DIM

TABLE = "segments"

SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("asset_id", pa.string()),
        pa.field("asset_type", pa.string()),  # photo | video
        pa.field("uri", pa.string()),
        pa.field("thumb", pa.string()),
        pa.field("t_start", pa.float64()),
        pa.field("t_end", pa.float64()),
        pa.field("caption", pa.string()),
        pa.field("transcript", pa.string()),
        pa.field("tags", pa.string()),
        pa.field("text", pa.string()),  # caption + transcript + tags, FTS target
        pa.field("enriched", pa.int32()),
        pa.field("created", pa.float64()),
        pa.field("vector", pa.list_(pa.float32(), VECTOR_DIM)),
    ]
)

_db = None
_table = None


def get_db():
    global _db
    if _db is None:
        _db = lancedb.connect(str(DB_DIR))
    return _db


def get_table():
    global _table
    if _table is None:
        db = get_db()
        if TABLE not in db.table_names():
            _table = db.create_table(TABLE, schema=SCHEMA)
        else:
            _table = db.open_table(TABLE)
    else:
        # handles pin a dataset version; refresh to see writes from
        # other handles/processes (e.g. ingest while server runs)
        _table.checkout_latest()
    return _table


def build_text(caption: str, transcript: str, tags: str) -> str:
    return " ".join(p for p in (caption, transcript, tags) if p).strip()


def insert_rows(rows: list[dict]):
    if rows:
        get_table().add(rows)


def rebuild_fts():
    tbl = get_table()
    try:
        tbl.create_fts_index("text", replace=True, use_tantivy=False)
    except Exception as e:  # empty table etc.
        print(f"[db] fts index skipped: {e}")


def update_enrichment(segment_id: str, caption: str, tags: str, transcript: str):
    tbl = get_table()
    safe_id = segment_id.replace("'", "")
    tbl.update(
        where=f"id = '{safe_id}'",
        values={
            "caption": caption,
            "tags": tags,
            "text": build_text(caption, transcript, tags),
            "enriched": 1,
        },
    )


def get_segments(segment_ids: list[str]) -> list[dict]:
    tbl = get_table()
    safe = ",".join("'" + s.replace("'", "") + "'" for s in segment_ids)
    rows = tbl.search().where(f"id IN ({safe})").limit(len(segment_ids)).to_list()
    by_id = {r["id"]: r for r in rows}
    return [strip_vector(by_id[s]) for s in segment_ids if s in by_id]


def indexed_uris() -> set[str]:
    tbl = get_table()
    rows = tbl.search().select(["uri"]).limit(1_000_000).to_list()
    return {r["uri"] for r in rows}


def delete_by_uris(uris: list[str]) -> int:
    # paths with quotes can't be expressed safely in the SQL filter; skip them
    uris = [u for u in uris if "'" not in u]
    if not uris:
        return 0
    tbl = get_table()
    removed = 0
    for i in range(0, len(uris), 100):
        safe = ",".join(f"'{u}'" for u in uris[i : i + 100])
        rows = tbl.search().select(["id"]).where(f"uri IN ({safe})").limit(100_000).to_list()
        for r in rows:
            (THUMBS_DIR / f"{r['id']}.jpg").unlink(missing_ok=True)
        tbl.delete(f"uri IN ({safe})")
        removed += len(rows)
    return removed


def strip_vector(row: dict) -> dict:
    row = dict(row)
    row.pop("vector", None)
    return row
