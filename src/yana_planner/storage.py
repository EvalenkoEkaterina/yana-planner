"""Short SQLite transactions storing complete, immutable run snapshots."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


@contextmanager
def connection(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY, created_at TEXT, scenario_name TEXT, deadline_h INTEGER,
            status TEXT, input_json TEXT, result_json TEXT, code_version TEXT)""")
        with db:
            yield db
    finally:
        db.close()


def save(path, scenario, deadline, inputs, result, version):
    """Save inputs and validated outputs after calculation, returning the run ID."""
    status = (
        "completed"
        if all(r["valid"] for c in result["comparisons"].values() for r in c["rows"].values())
        else "invalid_schedule"
    )
    with connection(path) as db:
        cur = db.execute(
            "INSERT INTO runs (created_at,scenario_name,deadline_h,status,input_json,result_json,code_version) VALUES (?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                scenario,
                deadline,
                status,
                json.dumps(inputs, ensure_ascii=False, allow_nan=False),
                json.dumps(result, ensure_ascii=False, allow_nan=False),
                version,
            ),
        )
        return cur.lastrowid


def load(path, run_id):
    """Load a saved snapshot; never run the solver while viewing history."""
    with connection(path) as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["inputs"] = json.loads(item.pop("input_json"))
    item["result"] = json.loads(item.pop("result_json"))
    return item


def history(path):
    with connection(path) as db:
        return [
            dict(x)
            for x in db.execute(
                "SELECT id,created_at,scenario_name,deadline_h,status FROM runs ORDER BY id DESC"
            )
        ]
