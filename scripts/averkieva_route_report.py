"""Validate a route package and run the installed core; never emulate new objectives."""

import argparse
import csv
import hashlib
import inspect
import json
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.economics import cost
from yana_planner.io import export_result
from yana_planner.planning import compare
from yana_planner.replanning import run_replanning
from yana_planner.route_import import RATE_FIELDS, import_route_package
from yana_planner.validation import check_schedule


def run(package, *, time_limit=2):
    package = import_route_package(package)
    cfg = package["scenario"]
    strategy = package["strategy"]
    modern = "strategy" in inspect.signature(compare).parameters
    if not modern and strategy == "cost":
        raise InputError(
            "Установленное ядро ещё не поддерживает strategy=cost; интегрируйте ветку Еваленко"
        )
    if not modern and any(v.get("load_rate_tph") is not None for v in cfg["vessels"]):
        raise InputError("Установленное ядро ещё не поддерживает погрузку по скорости")
    event = package.get("event")
    if event and event["type"] == "weather_closure" and not modern:
        raise InputError("Установленное ядро ещё не поддерживает погодное событие")
    kwargs = {"time_limit": time_limit}
    if modern:
        kwargs.update(strategy=strategy, cost_terms=package.get("cost_terms"))
    deadline = package["deadline_h"]
    result = (
        run_replanning(cfg, event, deadline, **kwargs)
        if event
        else compare(cfg, deadline, **kwargs)
    )
    terms = package.get("cost_terms")
    # Legacy evaluation is safe only for the explicitly uniform tariff case.
    # It evaluates schedules; it does not turn the trips objective into a cost objective.
    evaluated_terms = terms
    if terms and not modern:
        rates = list(terms["vessel_rates"].values())
        if any(any(r.get(f) != rates[0].get(f) for f in RATE_FIELDS) for r in rates):
            raise InputError("Разные ставки требуют экономического ядра Еваленко")
        evaluated_terms = {
            "billing_mode": terms["billing_mode"],
            "horizon_h": terms["horizon_h"],
            **{f: rates[0].get(f) for f in RATE_FIELDS},
        }
    focus = (
        result
        if not event
        else {
            "baseline": result["revised_baseline"]["result"],
            "planner": result["revised_planner"]["result"],
        }
    )
    costs = {}
    for method in ("baseline", "planner"):
        verdict = focus[method]
        plan = [
            {key: trip[key] for key in ("vessel_id", "start_h", "quantity_t")}
            for trip in verdict.get("trips", [])
        ]
        checked_cfg = result.get("updated_scenario", cfg) if event else cfg
        if not event:
            checked = check_schedule(checked_cfg, plan, deadline)
            if checked["status"] != "valid_simulation":
                raise InputError(f"{method}: независимая проверка не пройдена")
        if terms and not event and verdict["status"] == "valid_simulation":
            costs[method] = cost(cfg, plan, deadline, evaluated_terms)
    snapshot = {
        "route": package["route"],
        "planning_strategy": strategy,
        "result": result,
        "costs": costs,
        "cost_terms": terms,
        "package_sha256": hashlib.sha256(
            json.dumps(package, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "cost_note": "Оценка сохранённых расписаний; реальная экономия неизвестна",
        "actual_savings_rub": None,
    }
    return package, snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input", nargs="?", default="examples/routes/ust-kuyga-batagay.package.json"
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--out", default="reports/averkieva/batagay")
    parser.add_argument("--time-limit", type=float, default=2)
    args = parser.parse_args()
    try:
        raw = json.loads(Path(args.input).read_text())
        package = import_route_package(raw)
        if args.check_only:
            print(f"Пакет проверен: {package['route']['id']}")
            return
        package, snapshot = run(package, time_limit=args.time_limit)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        export_result(out, snapshot["result"])
        (out / "snapshot.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
        )
        with (out / "summary.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                ["method", "status", "accepted_t", "trips", "last_receipt_h", "cost_rub"]
            )
            result = snapshot["result"]
            for name in ("baseline", "planner"):
                v = result[name] if not package["event"] else result["revised_" + name]["result"]
                writer.writerow(
                    [
                        name,
                        v["status"],
                        v["accepted_t"],
                        v["trip_count"],
                        v["last_receipt_h"],
                        snapshot["costs"].get(name, {}).get("total_rub"),
                    ]
                )
        print(f"Готово: {out / 'report.html'}")
    except (InputError, ValueError, OSError) as exc:
        parser.exit(2, f"Ошибка: {exc}\n")


if __name__ == "__main__":
    main()
