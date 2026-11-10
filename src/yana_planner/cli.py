"""Command-line entry point; invalid or unknown inputs produce no fake totals."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from . import __version__
from .comparison import select_economic_comparison
from .compat import normalize_result
from .deadline_sweep import (
    export_deadline_sweep,
    require_plotting,
    run_deadline_sweep,
    save_sweep_point,
)
from .domain import ConfirmationRequired, InputError, integer
from .economics import compare_cost
from .excel_import import import_excel
from .experiments import summarize_experiment
from .io import export_failure, export_result, read_json, read_plan, write_json
from .planning import compare
from .replanning import run_replanning
from .sensitivity import run_sensitivity
from .validation import check_schedule


def parser():
    p = argparse.ArgumentParser(
        prog="yana", description="Сценарное планирование доставки НКТ по Яне"
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("report", help="Отчёт и сводка из сохранённого результата")
    q.add_argument("input")
    q.add_argument("--out", required=True)
    q = sub.add_parser("summarize", help="Сводка проверенных результатов READY и MILP")
    q.add_argument("input", help="JSON от plan или replan")
    q.add_argument("--out", required=True, help="Файл JSON со сводкой")
    q = sub.add_parser("sweep-deadlines", help="Серия независимых опытов с разными сроками")
    q.add_argument("input")
    q.add_argument("--event", required=True)
    q.add_argument("--start", type=int, default=240)
    q.add_argument("--stop", type=int, default=336)
    q.add_argument("--step", type=int, default=8)
    q.add_argument("--time-limit", type=float, default=3)
    q.add_argument("--reference")
    q.add_argument("--plot", action="store_true")
    q.add_argument("--out", required=True)
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
    q.add_argument("--phase", choices=["before", "after"])
    q.add_argument("--out", required=True)
    q = sub.add_parser("sensitivity")
    q.add_argument("input", help="JSON сценария")
    q.add_argument("comparison", help="JSON результата plan или replan")
    q.add_argument("--deadline", type=int)
    q.add_argument("--phase", choices=["before", "after"])
    q.add_argument(
        "--horizon",
        type=int,
        required=True,
        help="Общий оплачиваемый горизонт для схемы all_available_time",
    )
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
        if args.command == "report":
            if Path(args.input).resolve().is_relative_to(Path(args.out).resolve()):
                print(
                    json.dumps(
                        dict(
                            status="invalid_input",
                            accepted_t=None,
                            message="Для report выберите выходную папку отдельно от исходных материалов",
                            actual_savings_rub=None,
                        ),
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )
                return 2
            result = normalize_result(read_json(args.input))
            rows = summarize_experiment(result)
            export_result(args.out, result)
            write_json(Path(args.out) / "summary.json", rows)
            print(f"Отчёт и сводка: {Path(args.out) / 'report.html'}")
            return (
                0
                if all(
                    r[f"{method}_status"] == "valid_simulation"
                    for r in rows
                    for method in ["READY", "MILP"]
                )
                else 2
            )
        if args.command == "sweep-deadlines":
            step = integer(args.step, "step", minimum=1)
            if args.plot:
                require_plotting()
            out = Path(args.out)

            def save_point(row, result):
                save_sweep_point(out, row, result)
                print(
                    f"{row['deadline_h']} ч: READY {row['READY_t']} т / MILP {row['MILP_t']} т",
                    flush=True,
                )

            result = run_deadline_sweep(
                read_json(args.input),
                read_json(args.event),
                list(range(args.start, args.stop + 1, step)),
                time_limit=args.time_limit,
                incumbent=read_plan(args.reference) if args.reference else None,
                on_start=lambda protocol: write_json(out / "protocol.json", protocol),
                on_result=save_point,
            )
            export_deadline_sweep(out, result, plot=args.plot)
            print(f"Отчёт: {out / 'report.html'}")
            return 0 if all(r["experiment_status"] == "completed" for r in result["rows"]) else 2
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
        if args.command in ("cost", "sensitivity"):
            data = normalize_result(read_json(args.comparison))
            deadline = integer(
                args.deadline
                if args.deadline is not None
                else data.get("deadline_h", cfg.get("delivery_deadline_h")),
                "deadline_h",
            )
            effective, baseline, alternative, context = select_economic_comparison(
                cfg,
                data,
                deadline,
                phase=args.phase,
            )
            if args.command == "cost":
                result = compare_cost(
                    effective, baseline, alternative, deadline, read_json(args.terms)
                )
            else:
                result = run_sensitivity(
                    effective,
                    baseline,
                    alternative,
                    deadline,
                    horizon_h=integer(args.horizon, "horizon_h"),
                )
            result["comparison_context"] = context
            write_json(args.out, result)
            if args.command == "sensitivity" and result["status"] == "scenario_sensitivity":
                for mode, values in result["billing_modes"].items():
                    rows = values["rows"]
                    wins = sum(r["alternative_is_cheaper"] is True for r in rows)
                    losses = sum(r["baseline_minus_alternative_rub"] < 0 for r in rows)
                    print(
                        f"{mode}: {len(rows)} сочетаний; MILP дешевле в {wins}, READY дешевле в {losses}"
                    )
                print(f"Расходы сценарные. Результат: {args.out}")
            else:
                print(json.dumps(result, ensure_ascii=False))
            return 2 if result["status"] == "missing_cost_inputs" else 0
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
                if args.command in ["plan", "replan", "benchmark", "sweep-deadlines", "report"]:
                    export_failure(args.out, failure)
                    if args.command == "report":
                        write_json(Path(args.out) / "summary.json", failure)
                    if args.command == "sweep-deadlines":
                        write_json(Path(args.out) / "sweep.json", failure)
                        for extension in ["png", "svg"]:
                            (Path(args.out) / f"deadline_sweep.{extension}").unlink(missing_ok=True)
                else:
                    write_json(args.out, failure)
            except OSError:
                pass  # The original filesystem error remains visible below.
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
