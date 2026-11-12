"""One nominal plan, independent events, unknown outcomes and explicit costs."""

import contextlib
import copy
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.cli import main
from yana_planner.domain import InputError
from yana_planner.risk_analysis import analyze_risks, export_risk_report

ROOT = Path(__file__).resolve().parents[1]


def weather(interval):
    return dict(
        type="weather_closure",
        announced_at_h=2,
        closures=[interval],
        rule_status="scenario_assumption",
        data_kind="synthetic",
        forecast_issued_at=None,
    )


class RiskAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "examples/economic-choice.json").read_text())
        self.cfg.update(
            target_t=40, initial_source_t=20, destination_capacity_t=40, arrivals=[dict(h=8, t=20)]
        )
        self.plan = [dict(vessel_id="A", start_h=h, quantity_t=20) for h in (0, 8)]
        self.cases = [
            dict(
                id="delay",
                label="Партия позже",
                event=dict(
                    type="arrival_delay", announced_at_h=2, arrival_index=0, new_arrival_h=12
                ),
            ),
            dict(id="closure", label="Запрет", event=weather([8, 12])),
            dict(id="intervention", label="Начатый рейс", event=weather([3, 4])),
        ]
        self.terms = json.loads((ROOT / "examples/economic-choice.costs.json").read_text())

    def run_analysis(self, **kwargs):
        return analyze_risks(self.cfg, self.plan, self.cases, 18, time_limit=2, **kwargs)

    def test_recovery_and_intervention_have_separate_counts(self):
        result = self.run_analysis()
        summary = result["summary"]
        self.assertEqual(result["nominal"]["accepted_t"], 40)
        self.assertEqual(result["nominal"]["deadline_slack_h"], 7)
        self.assertIsNone(result["nominal"]["optimal"])
        self.assertEqual(summary["case_count"], 3)
        self.assertEqual(summary["unchanged_valid_cases"], 0)
        self.assertEqual(summary["replanned_full_delivery_cases"], 2)
        self.assertEqual(summary["intervention_cases"], 1)
        self.assertEqual(summary["unconfirmed_cases"], 1)
        self.assertEqual(summary["max_known_shortfall_t"], 0)
        self.assertEqual(summary["min_slack_full_delivery_h"], 3)
        self.assertIsNone(summary["probability_of_success"])
        row = result["cases"][2]
        self.assertIsNone(row["unchanged"]["accepted_t"])
        self.assertIsNone(row["replanned"]["accepted_t"])
        self.assertIsNone(row["replanned"]["shortfall_t"])
        self.assertIsNone(row["replanned"]["total_rub"])

    def test_every_case_uses_same_saved_plan_and_original_conditions(self):
        with patch(
            "yana_planner.replanning.compare", side_effect=AssertionError("no initial solve")
        ):
            result = self.run_analysis()
        self.assertEqual(result["initial_plan"], self.plan)
        for case in result["cases"]:
            self.assertEqual(case["replanning"]["committed"], [self.plan[0]])
        self.assertEqual(result["cases"][0]["replanning"]["updated_input"]["closures"], [])
        self.assertEqual(
            result["cases"][1]["replanning"]["updated_input"]["arrivals"], [dict(h=8, t=20)]
        )

    def test_partial_delivery_is_distinct_from_unknown_result(self):
        self.cases = [
            dict(
                id="too-late",
                event=dict(
                    type="arrival_delay", announced_at_h=2, arrival_index=0, new_arrival_h=20
                ),
            )
        ]
        result = self.run_analysis(strategy="cost", cost_terms=self.terms)
        row = result["cases"][0]
        self.assertTrue(row["replanned"]["valid"])
        self.assertFalse(row["replanned"]["target_met"])
        self.assertEqual(row["replanned"]["accepted_t"], 20)
        self.assertEqual(row["replanned"]["shortfall_t"], 20)
        self.assertEqual(result["summary"]["unconfirmed_cases"], 0)
        self.assertEqual(result["summary"]["max_known_shortfall_t"], 20)
        self.assertIsNone(result["summary"]["min_slack_full_delivery_h"])
        self.assertIsNone(row["replanning_cost_difference_rub"])

    def test_cost_difference_only_for_equal_valid_volume(self):
        self.cases = [dict(id="safe", event=weather([4, 5]))]
        result = self.run_analysis(strategy="cost", cost_terms=self.terms)
        row = result["cases"][0]
        self.assertEqual(row["unchanged"]["total_rub"], 2000)
        self.assertEqual(row["replanned"]["total_rub"], 1200)
        self.assertEqual(row["replanning_cost_difference_rub"], 800)
        self.assertEqual(row["replanned"]["trip_count"], 3)
        self.assertTrue(row["replanned"]["optimal"])
        self.assertEqual(result["settings"]["strategy"], "cost")
        self.assertIsNone(result["actual_savings_rub"])

    def test_invalid_later_case_prevents_all_solver_calls(self):
        self.cases.append(dict(id="bad", event=weather([1, 4])))
        with patch("yana_planner.risk_analysis.replan") as solver:
            with self.assertRaises(InputError):
                self.run_analysis()
            solver.assert_not_called()

    def test_invalid_nominal_plan_and_duplicate_ids_are_rejected(self):
        with self.assertRaises(InputError):
            analyze_risks(self.cfg, [dict(vessel_id="A", start_h=0, quantity_t=50)], self.cases, 18)
        for cases in [
            [],
            None,
            [dict(id="nominal", event=weather([8, 12]))],
            [self.cases[0], self.cases[0]],
        ]:
            with self.subTest(cases=cases), self.assertRaises(InputError):
                analyze_risks(self.cfg, self.plan, cases, 18)

    def test_invalid_strategy_budget_and_missing_rates_are_rejected(self):
        for kwargs in [
            dict(strategy="unknown"),
            dict(strategy="cost"),
            dict(time_limit=0),
            dict(time_limit=float("nan")),
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(InputError):
                analyze_risks(self.cfg, self.plan, self.cases, 18, **kwargs)

    def test_input_snapshots_and_case_set_metadata_are_preserved(self):
        cases = dict(status="synthetic", cases=self.cases)
        before = copy.deepcopy((self.cfg, self.plan, cases, self.terms))
        result = analyze_risks(
            self.cfg, self.plan, cases, 18, strategy="cost", cost_terms=self.terms, time_limit=2
        )
        self.assertEqual((self.cfg, self.plan, cases, self.terms), before)
        self.assertEqual(result["case_set"]["status"], "synthetic")
        self.assertEqual(len(result["initial_plan_sha256"]), 64)
        self.assertEqual(result["settings"]["cost_terms"], self.terms)

    def test_export_preserves_nulls_and_escapes_labels(self):
        self.cases[0]["label"] = "<script>unsafe</script>"
        result = self.run_analysis()
        with tempfile.TemporaryDirectory() as directory:
            export_risk_report(directory, result)
            page = (Path(directory) / "report.html").read_text()
            saved = json.loads((Path(directory) / "results.json").read_text())
            with (Path(directory) / "risk-summary.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 7)
            self.assertIn("&lt;script&gt;unsafe&lt;/script&gt;", page)
            self.assertNotIn("<script>unsafe", page)
            self.assertIn("не вероятность успешной доставки", page)
            self.assertIsNone(saved["cases"][2]["replanned"]["accepted_t"])

    def test_cli_exports_report_and_replaces_stale_output_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, obj in [("input", self.cfg), ("plan", self.plan), ("cases", self.cases)]:
                (root / f"{name}.json").write_text(json.dumps(obj))
            args = [
                "risk",
                str(root / "input.json"),
                "--schedule",
                str(root / "plan.json"),
                "--cases",
                str(root / "cases.json"),
                "--deadline",
                "18",
                "--time-limit",
                "2",
                "--out",
                str(root / "out"),
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            self.assertTrue((root / "out/risk-summary.csv").exists())
            (root / "cases.json").write_text("[]")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 2)
            failed = json.loads((root / "out/results.json").read_text())
            self.assertIsNone(failed["accepted_t"])
            self.assertNotIn("Партия позже", (root / "out/risk-summary.csv").read_text())


if __name__ == "__main__":
    unittest.main()
