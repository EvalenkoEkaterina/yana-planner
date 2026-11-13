"""Offline historical replay; run after editable install from the repository root."""

import json
from pathlib import Path

from weather_report import report

from yana_planner.domain import proposals
from yana_planner.planning import compare
from yana_planner.weather import apply_weather_event, assess_weather
from yana_planner.weather_rules import event_from_rule, validate_rule

ROOT = Path(__file__).resolve().parents[1]


def main():
    cfg = json.loads((ROOT / "examples/r03.json").read_text())
    cfg.update(t0="2026-08-10T08:00:00+10:00", timezone="Asia/Vladivostok")
    records = [
        json.loads((ROOT / "examples/weather" / name).read_text())
        for name in ("nizhneyansk.json", "ust_kuyga.json")
    ]
    rule = validate_rule(json.loads((ROOT / "examples/weather_rules/yana_wind.json").read_text()))
    cfg["route_id"] = rule["route_id"]
    initial = compare(cfg, 328, time_limit=2)
    output = dict(
        description="Historical ERA5 replay, NOT an archived forecast or navigation advice",
        initial=initial,
        experiments=[],
    )
    # Fixed sensitivity grid; no threshold selected to maximise an advantage.
    for threshold in rule["sensitivity_thresholds"]:
        selected_rule = dict(rule, threshold=threshold)
        event = event_from_rule(
            records,
            selected_rule,
            route_id=cfg["route_id"],
            t0=cfg["t0"],
            horizon_h=492,
            announced_at_h=60,
        )
        runs = {}
        for key, method in [("baseline", "ready"), ("planner", "milp")]:
            runs[method] = assess_weather(
                cfg, proposals(initial[key]["trips"]), event, 328, method=method, time_limit=2
            )
        # Before any voyage is committed, evaluate the same historical scenario.
        early_event = event_from_rule(
            records,
            selected_rule,
            route_id=cfg["route_id"],
            t0=cfg["t0"],
            horizon_h=492,
            announced_at_h=0,
        )
        weather_cfg = apply_weather_event(cfg, early_event, at_h=0)
        known = compare(weather_cfg, 328, time_limit=2)
        output["experiments"].append(
            dict(
                threshold_ms=threshold,
                event=event,
                runs=runs,
                weather_known_before_start=known,
                early_event=early_event,
            )
        )
        print(
            threshold,
            "m/s;",
            len(event["closures"]),
            "closures;",
            {k: (v["action"], v["result"].get("accepted_t")) for k, v in runs.items()},
        )
    out = ROOT / "outputs/weather"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    report(output, out / "report.html")
    print(out / "report.html")


if __name__ == "__main__":
    main()
