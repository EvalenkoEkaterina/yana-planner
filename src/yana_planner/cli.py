"""Command-line entry point; invalid or unknown inputs produce no fake totals."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from . import __version__
from .domain import ConfirmationRequired, InputError, integer
from .economics import compare_cost
from .excel_import import import_excel
from .experiments import summarize_experiment
from .io import export_failure, export_result, read_json, read_plan, write_json
from .planning import compare
from .replanning import run_replanning
from .validation import check_schedule


def parser():
    p = argparse.ArgumentParser(
        prog="yana", description="Сценарное планирование доставки НКТ по Яне"
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("summarize", help="Сводка проверенных результатов READY и MILP")
    q.add_argument("input", help="JSON от plan или replan")
    q.add_argument("--out", required=True, help="Файл JSON со сводкой")
    for command in ["plan", "replan"]:
        q = sub.add_parser(command)
        q.add_argument("input")
        q.add_argument("--deadline", type=int)
        q.add_argument("--out", required=True)
        q.add_argument("--time-limit", type=float, default=3)
        q.add_argument(
            "--reference", help="Необязательный ранее найденный план; всегда перепроверяется"
        )
        if command == "replan":
            q.add_argument("--event", required=True)
    q = sub.add_parser("validate")
    q.add_argument("input")
    q.add_argument("schedule")
    q.add_argument("--deadline", type=int)
    q.add_argument("--method", choices=["planner", "baseline"], default="planner")
    q.add_argument("--out")
    q = sub.add_parser("import-excel")
    q.add_argument("input")
    q.add_argument("--profile", required=True, choices=["team-r03", "resource-v1"])
    q.add_argument("--sim-only", action="store_true")
    q.add_argument("--deadline", type=int)
    q.add_argument("--return-cutoff", type=int)
    q.add_argument("--out", required=True)
    q = sub.add_parser("cost")
    q.add_argument("input")
    q.add_argument("comparison")
    q.add_argument("--terms", required=True)
    q.add_argument("--deadline", type=int)
    q.add_argument("--out", required=True)
    q = sub.add_parser("benchmark")
    q.add_argument("input", help="JSON с scenarios и grid_hours")
    q.add_argument("--out", required=True)
    q.add_argument("--time-limit", type=float, default=3)
    q.add_argument("--scenario")
    q.add_argument("--deadline", type=int)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "summarize":
            rows = summarize_experiment(read_json(args.input))
            write_json(args.out, rows)
            for row in rows:
                print(
                    f"{row['phase']}: READY {row['READY_t']} т / MILP {row['MILP_t']} т; "
                    f"сэкономлено рейсов {row['trips_saved']}; "
                    f"выигрыш окончания {row['hours_saved']} ч; "
                    f"компромисс показателей {row['tradeoff']}"
                )
            return (
                0
                if all(
                    r[f"{method}_status"] == "valid_simulation"
                    for r in rows
                    for method in ["READY", "MILP"]
                )
                else 2
            )
        if args.command == "import-excel":
            result = import_excel(
                args.input,
                profile=args.profile,
                sim_only=args.sim_only,
                deadline=args.deadline,
                return_cutoff=args.return_cutoff,
            )
            write_json(args.out, result)
            print(f"Импортирован сценарий {result['id']}: {args.out}")
            return 0
        cfg = read_json(args.input)
        if args.command == "benchmark":
            if not isinstance(cfg, dict) or not isinstance(cfg.get("scenarios"), list):
                raise InputError("Нужен объект scenarios + grid_hours")
            scenarios = [
                c for c in cfg["scenarios"] if args.scenario is None or c.get("id") == args.scenario
            ]
            if not scenarios:
                raise InputError("Сценарий не найден")
            deadlines = [args.deadline] if args.deadline is not None else cfg.get("grid_hours", [])
            if not deadlines:
                raise InputError("Сроки не заданы")
            data = []
            for c in scenarios:
                for d in deadlines:
                    result = compare(c, integer(d, "deadline_h"), time_limit=args.time_limit)
                    name = re.sub(r"[^A-Za-z0-9_-]", "_", c["id"])
                    export_result(Path(args.out) / name / str(d), result)
                    a, b = result["baseline"], result["planner"]
                    data.append(
                        dict(
                            scenario=c["id"],
                            deadline_h=d,
                            READY_status=a["status"],
                            MILP_status=b["status"],
                            READY_t=a["accepted_t"],
                            MILP_t=b["accepted_t"],
                            READY_trips=a.get("trip_count"),
                            MILP_trips=b.get("trip_count"),
                        )
                    )
                    print(f"{c['id']} / {d} ч: {a['accepted_t']} → {b['accepted_t']} т", flush=True)
            write_json(Path(args.out) / "benchmark.json", data)
            with (Path(args.out) / "benchmark.csv").open(
                "w", encoding="utf-8-sig", newline=""
            ) as f:
                w = csv.DictWriter(f, fieldnames=list(data[0]))
                w.writeheader()
                w.writerows(data)
            return 0
        if not isinstance(cfg, dict):
            raise InputError("Ожидается объект сценария")
        deadline = integer(
            args.deadline if args.deadline is not None else cfg.get("delivery_deadline_h"),
            "deadline_h",
        )
        if args.command == "validate":
            verdict = check_schedule(cfg, read_plan(args.schedule, args.method), deadline)
            if args.out:
                write_json(args.out, verdict)
            print(json.dumps(verdict, ensure_ascii=False))
            return 0 if verdict["status"] == "valid_simulation" else 2
        if args.command == "cost":
            plans = read_json(args.comparison)
            if (
                not isinstance(plans, dict)
                or not isinstance(plans.get("baseline"), dict)
                or not isinstance(plans.get("planner"), dict)
            ):
                raise InputError("Для cost нужен JSON результата сравнения")
            result = compare_cost(
                cfg,
                plans["baseline"]["trips"],
                plans["planner"]["trips"],
                deadline,
                read_json(args.terms),
            )
            write_json(args.out, result)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        incumbent = read_plan(args.reference) if args.reference else None
        if args.command == "replan":
            result = run_replanning(
                cfg,
                read_json(args.event),
                deadline,
                time_limit=args.time_limit,
                incumbent=incumbent,
            )
        else:
            result = compare(cfg, deadline, time_limit=args.time_limit, incumbent=incumbent)
        export_result(args.out, result)
        values = (
            [
                ("READY", result["revised_baseline"]["result"]),
                ("MILP", result["revised_planner"]["result"]),
            ]
            if args.command == "replan"
            else [("READY", result["baseline"]), ("MILP", result["planner"])]
        )
        for name, value in values:
            print(
                f"{name}: {value['status']}; принято {value.get('accepted_t')} т; рейсов {value.get('trip_count')}; последняя приёмка {value.get('last_receipt_h')} ч"
            )
        print(f"Отчёт: {Path(args.out) / 'report.html'}")
        return 0 if all(v["status"] == "valid_simulation" for _, v in values) else 2
    except (InputError, KeyError, OSError) as error:
        status = (
            "requires_confirmation" if isinstance(error, ConfirmationRequired) else "invalid_input"
        )
        failure = dict(status=status, message=str(error), accepted_t=None, actual_savings_rub=None)
        if getattr(args, "out", None):
            try:
                if args.command in ["plan", "replan", "benchmark"]:
                    export_failure(args.out, failure)
                else:
                    write_json(args.out, failure)
            except OSError:
                pass  # The original filesystem error remains visible below.
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
