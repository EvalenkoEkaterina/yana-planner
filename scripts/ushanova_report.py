"""Reproduce the economics handoff using saved schedules, not a new solver run."""

import csv
import hashlib
import json
from pathlib import Path

from yana_planner.economics import compare_cost
from yana_planner.replanning import apply_arrival_delay
from yana_planner.ushanova_economics import break_even, run_cost_grid

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main():
    cfg = read("examples/r03.json")
    terms = read("examples/ushanova/terms.json")
    static = read("demos/static/results.json")
    replanning = read("demos/replanning/results.json")
    revised_cfg = apply_arrival_delay(cfg, read("examples/arrival_delay.json"))
    pairs = {
        "static": (cfg, static["baseline"]["trips"], static["planner"]["trips"]),
        "after_delay": (
            revised_cfg,
            replanning["revised_baseline"]["result"]["trips"],
            replanning["revised_planner"]["result"]["trips"],
        ),
    }
    report = {
        "status": "scenario_analysis_not_actual_savings",
        "schedule_source": "saved_demo_schedules_revalidated_by_compare_cost",
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "examples/r03.json",
                "examples/arrival_delay.json",
                "examples/ushanova/terms.json",
                "demos/static/results.json",
                "demos/replanning/results.json",
            )
        },
        "terms": terms,
        "phases": {},
    }
    out = ROOT / "reports/ushanova"
    out.mkdir(parents=True, exist_ok=True)
    with (ROOT / "examples/ushanova/project_budget.csv").open(encoding="utf-8") as f:
        budget_rows = list(csv.DictReader(f))
    budget = {}
    for phase in ["development", "monthly"]:
        subtotal = sum(
            int(row["quantity"]) * int(row["unit_cost_rub"])
            for row in budget_rows
            if row["phase"] == phase
        )
        budget[phase] = {
            "subtotal_rub": subtotal,
            "contingency_20_percent_rub": subtotal * 20 // 100,
            "total_rub": subtotal * 120 // 100,
        }
    budget["pilot_operating_months"] = 1.5
    budget["pilot_operations_rub"] = budget["monthly"]["total_rub"] * 1.5
    budget["development_and_pilot_rub"] = (
        budget["development"]["total_rub"] + budget["pilot_operations_rub"]
    )
    budget["status"] = "scenario_resource_budget_not_a_quote"
    (out / "project_budget.json").write_text(
        json.dumps(budget, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for phase, (scenario, baseline, alternative) in pairs.items():
        grid = run_cost_grid(
            scenario,
            baseline,
            alternative,
            328,
            terms,
            x_parameter="active_vessel_hour_rub",
            x_values=[500, 1000, 2000],
            y_parameter="waiting_vessel_hour_rub",
            y_values=[250, 500, 1000],
        )
        report["phases"][phase] = {
            "comparison": compare_cost(scenario, baseline, alternative, 328, terms),
            "grid": grid,
            "break_even": break_even(
                scenario,
                baseline,
                alternative,
                328,
                terms,
                parameter="waiting_vessel_hour_rub",
            ),
        }
        with (out / f"{phase}_grid.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(
                [
                    "active_rate_rub_h",
                    "waiting_rate_rub_h",
                    "READY_rub",
                    "MILP_rub",
                    "READY_minus_MILP_rub",
                    "status",
                ]
            )
            for row in grid:
                writer.writerow(
                    [
                        row["terms"]["active_vessel_hour_rub"],
                        row["terms"]["waiting_vessel_hour_rub"],
                        row["baseline_total_rub"],
                        row["alternative_total_rub"],
                        row["scenario_difference_rub"],
                        row["status"],
                    ]
                )
    (out / "economics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(out / "economics.json")


if __name__ == "__main__":
    main()
