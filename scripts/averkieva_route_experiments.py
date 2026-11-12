"""Independent scenario experiments; run after averkieva_route_report.py from repo root."""

import copy
import csv
import json
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.economics import cost
from yana_planner.io import export_result
from yana_planner.planning import compare
from yana_planner.replanning import run_replanning
from yana_planner.validation import check_schedule

p = json.load(open("examples/routes/ust-kuyga-batagay.package.json"))
cfg = p["scenario"]
terms = {
    "billing_mode": "all_available_time",
    "horizon_h": 492,
    "separate_trip_operations_rub": 25000,
    "active_vessel_hour_rub": 1000,
    "waiting_vessel_hour_rub": 500,
}
rows = []


def collect(case, c, r, deadline, phase="static"):
    for method in ["baseline", "planner"]:
        v = r[method]
        plan = [{k: t[k] for k in ["vessel_id", "start_h", "quantity_t"]} for t in v["trips"]]
        if check_schedule(c, plan, deadline)["status"] != "valid_simulation":
            raise InputError(f"{case}/{method}: independent verification failed")
        costs = cost(c, plan, deadline, terms)
        rows.append(
            {
                "case": case,
                "phase": phase,
                "method": method,
                "accepted_t": v["accepted_t"],
                "trips": v["trip_count"],
                "last_receipt_h": v["last_receipt_h"],
                "active_h": costs["active_vessel_h"],
                "waiting_h": costs["paid_waiting_h"],
                "cost_rub": costs["total_rub"],
                "all_stages_optimal": all(s.get("optimal") for s in r.get("solver", []))
                if method == "planner"
                else None,
            }
        )


base = json.load(open("reports/averkieva/batagay/snapshot.json"))["result"]
collect("control", cfg, base, 328)
for case in ["deadline160", "payload100", "static_closure"]:
    c = copy.deepcopy(cfg)
    deadline = 160 if case == "deadline160" else 328
    if case == "payload100":
        c["payload_calendar"] = [{"from_h": 0, "capacity_t": 100}]
    if case == "static_closure":
        c["closures"] = [[120, 132]]
    r = compare(c, deadline, time_limit=3)
    directory = Path("reports/averkieva") / case
    export_result(directory, r)
    (directory / "input.json").write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n")
    collect(case, c, r, deadline)
event = {
    "id": "UKB-DELAY",
    "type": "arrival_delay",
    "announced_at_h": 60,
    "arrival_index": 1,
    "new_arrival_h": 168,
    "status": "scenario_assumption",
}
r = run_replanning(cfg, event, 328, time_limit=3)
export_result("reports/averkieva/arrival_delay", r)
# Recheck the revised input, not the original supply calendar.
c = copy.deepcopy(cfg)
c["arrivals"][1]["h"] = 168
rr = {
    "baseline": r["revised_baseline"]["result"],
    "planner": r["revised_planner"]["result"],
    "solver": r["revised_planner"]["solver"],
}
collect("arrival_delay", c, rr, 328, "after")
with open("reports/averkieva/experiments.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)
print(json.dumps(rows, ensure_ascii=False, indent=2))
