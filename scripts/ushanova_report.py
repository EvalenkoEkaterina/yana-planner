"""Reproduce the economics handoff using saved schedules, not a new solver run."""

import csv
import hashlib
import html
import json
import re
from pathlib import Path

from yana_planner.economics import compare_cost
from yana_planner.pilot_economics import illustrative_payback, order_value, pilot_budget
from yana_planner.rate_import import load_vessel_rates
from yana_planner.replanning import apply_arrival_delay
from yana_planner.ushanova_economics import break_even, run_cost_grid

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def render_expert_document(markdown):
    """Render the document's headings, paragraphs, lists and tables without dependencies."""

    def inline(value):
        value = html.escape(value)
        value = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', value)
        value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", value)

    blocks = []
    in_table = False
    for line in markdown.splitlines():
        if line.startswith("|"):
            if re.fullmatch(r"[|\s:\-]+", line):
                continue
            first = not in_table
            if first:
                blocks.append('<div class="table-wrap"><table>')
                in_table = True
            tag = "th" if first else "td"
            blocks.append(
                "<tr>"
                + "".join(
                    f"<{tag}>{inline(cell.strip())}</{tag}>" for cell in line.strip("|").split("|")
                )
                + "</tr>"
            )
            continue
        if in_table:
            blocks.append("</table></div>")
            in_table = False
        if not line.strip():
            continue
        if line.startswith("## "):
            blocks.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            blocks.append(f"<h1>{inline(line[2:])}</h1>")
        else:
            blocks.append(f"<p>{inline(line)}</p>")
    if in_table:
        blocks.append("</table></div>")
    return (
        """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Yana Planner — экономика и предложение пилота</title>
<style>
body{font:16px/1.6 Arial,sans-serif;color:#1b2937;margin:0;background:#eef2f5}
main{max-width:980px;margin:32px auto;background:white;padding:44px 52px}
h1{font-size:32px;line-height:1.2;color:#163f50}h2{font-size:23px;margin-top:36px;color:#163f50}
table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.45;margin:16px 0}
td,th{border:1px solid #cbd5dc;padding:9px;text-align:left;vertical-align:top}
th{background:#eaf2f5}a{color:#12627b;overflow-wrap:anywhere}p{margin:12px 0}
code{font-size:13px;overflow-wrap:anywhere}.table-wrap{overflow-x:auto}
@media(max-width:700px){main{margin:0;padding:22px}h1{font-size:26px}}
@media print{body{background:white;font-size:11pt}main{margin:0;padding:0;max-width:none}
h1,h2{break-after:avoid}tr{break-inside:avoid}.table-wrap{overflow:visible}table{font-size:9pt}
@page{size:A4;margin:16mm}}
</style><main>"""
        + "\n".join(blocks)
        + "</main></html>\n"
    )


def main():
    cfg = read("examples/r03.json")
    terms = read("examples/ushanova/terms.json")
    planning_terms = load_vessel_rates(cfg, terms, ROOT / "examples/ushanova/vessel_rates.csv")
    (ROOT / "examples/ushanova/terms.per-vessel.json").write_text(
        json.dumps(planning_terms, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
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
                "examples/ushanova/vessel_rates.csv",
                "examples/ushanova/pilot_budget.json",
                "demos/static/results.json",
                "demos/replanning/results.json",
            )
        },
        "terms": planning_terms,
        "sensitivity_terms": terms,
        "sensitivity_scope": "Uniform rates across all vessels; fixed saved schedules, no reoptimization",
        "phases": {},
    }
    out = ROOT / "reports/ushanova"
    out.mkdir(parents=True, exist_ok=True)
    budget_spec = read("examples/ushanova/pilot_budget.json")
    budget = {
        "scope": budget_spec["scope"],
        "assumptions": budget_spec,
        "student": pilot_budget(budget_spec, mode="student"),
        "paid": pilot_budget(budget_spec, mode="paid"),
    }
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
            "comparison": compare_cost(scenario, baseline, alternative, 328, planning_terms),
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
    evidence = read("examples/ushanova/evidence.json")
    published_positions = []
    for position in evidence["procurement_positions"]:
        value = order_value(position["quantity_t"], position["rate_rub_t"])
        if value["order_value_rub"] != position["published_total_rub"]:
            raise ValueError(f"Published amount does not match Q*p: {position['id']}")
        published_positions.append({**position, "arithmetic_check": value})
    business = {
        "status": "separate_evidence_and_illustrative_economics",
        "published_orders": published_positions,
        "historical_waiting": evidence["waiting_case"],
        "contract": evidence["contract"],
        "first_year_scenarios": {},
        "actual_savings_rub": None,
        "cash_benefit_warning": "Scenario cost reduction is not product revenue or a verified payment reduction",
    }
    differences = [row["scenario_difference_rub"] for row in report["phases"]["static"]["grid"]]
    benefit = report["phases"]["static"]["comparison"]["scenario_difference_rub"]
    for mode in ["student", "paid"]:
        business["first_year_scenarios"][mode] = {
            "single_base_case": illustrative_payback(budget[mode], benefit, cases=1),
            "three_comparable_base_cases": illustrative_payback(budget[mode], benefit, cases=3),
            "three_adverse_grid_cases": illustrative_payback(
                budget[mode], min(differences), cases=3
            ),
        }
    business["repeat_assumption"] = (
        "Three comparable independent future cases are hypothetical; static and delayed phases "
        "of the same campaign are not added as separate savings. Historical facts are not pooled."
    )
    business["source_sha256"] = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in ("examples/ushanova/evidence.json", "examples/ushanova/pilot_budget.json")
    }
    (out / "business_case.json").write_text(
        json.dumps(business, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    expert_markdown = (ROOT / "docs/ushanova-economics-business.md").read_text(encoding="utf-8")
    (out / "expert-economics.html").write_text(
        render_expert_document(expert_markdown), encoding="utf-8"
    )
    print(out / "economics.json")


if __name__ == "__main__":
    main()
