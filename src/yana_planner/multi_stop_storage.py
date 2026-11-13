"""Separate immutable snapshots; leave legacy READY/MILP history untouched."""

import json
from datetime import datetime, timezone

from .storage import connection


def _setup(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS multi_stop_runs (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, input_json TEXT NOT NULL, result_json TEXT NOT NULL, code_version TEXT NOT NULL)"
    )


def save(path, inputs, result):
    with connection(path) as db:
        _setup(db)
        row = db.execute(
            "INSERT INTO multi_stop_runs (created_at,status,input_json,result_json,code_version) VALUES (?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                result["status"],
                json.dumps(inputs, ensure_ascii=False, allow_nan=False),
                json.dumps(result, ensure_ascii=False, allow_nan=False),
                result["code_version"],
            ),
        )
        return row.lastrowid


def load(path, ident):
    with connection(path) as db:
        _setup(db)
        row = db.execute("SELECT * FROM multi_stop_runs WHERE id=?", (ident,)).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["inputs"] = json.loads(record.pop("input_json"))
    record["result"] = json.loads(record.pop("result_json"))
    return record


def history(path, limit=100):
    with connection(path) as db:
        _setup(db)
        rows = db.execute(
            "SELECT id,created_at,status,input_json FROM multi_stop_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    output = []
    for row in rows:
        record = dict(row)
        cfg = json.loads(record.pop("input_json"))["scenario"]
        record.update(
            origin_id=cfg["origin_id"],
            stop_ids=[s["id"] for s in cfg["stops"]],
            required_t=sum(s["required_t"] for s in cfg["stops"]),
        )
        output.append(record)
    return output
