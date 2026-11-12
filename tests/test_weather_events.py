"""Weather events change the future and preserve explicit provenance/history."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.cli import main
from yana_planner.domain import InputError, proposals
from yana_planner.experiments import comparisons
from yana_planner.io import export_result
from yana_planner.planning import compare
from yana_planner.presentation import event_card
from yana_planner.replanning import apply_event, apply_weather_closure, run_replanning
from yana_planner.validation import check_schedule

ROOT = Path(__file__).resolve().parents[1]


class WeatherEventTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "examples/economic-choice.json").read_text())
        self.cfg.update(
            target_t=40,
            initial_source_t=20,
            destination_capacity_t=40,
            arrivals=[dict(h=8, t=20)],
            t0="2026-08-10T00:00:00+00:00",
        )
        self.event = dict(
            type="weather_closure",
            announced_at_h=2,
            closures=[[8, 12]],
            rule_status="scenario_assumption",
            data_kind="synthetic",
            forecast_issued_at=None,
        )
        self.terms = json.loads((ROOT / "examples/economic-choice.costs.json").read_text())

    def run_case(self, **kwargs):
        return run_replanning(self.cfg, self.event, 18, time_limit=2, **kwargs)

    def test_future_closure_is_added_and_intervals_are_merged(self):
        self.cfg["closures"] = [[0, 1], [12, 13]]
        self.event["closures"] = [[9, 12], [8, 9], [8, 10]]
        changed = apply_weather_closure(self.cfg, self.event)
        self.assertEqual(changed["closures"], [[0, 1], [8, 13]])
        self.assertEqual(changed["provenance"]["update"], self.event)
        self.assertEqual(changed["provenance"]["parent_id"], self.cfg["id"])

    def test_end_to_end_reschedules_future_and_preserves_first_trip(self):
        result = self.run_case()
        self.assertEqual(result["experiment"], "single_weather_closure")
        self.assertEqual(result["initial"]["planner"]["last_receipt_h"], 11)
        for name in ["revised_baseline", "revised_planner"]:
            revised = result[name]
            self.assertEqual(revised["result"]["accepted_t"], 40)
            self.assertEqual(revised["result"]["last_receipt_h"], 14)
            self.assertEqual(revised["updated_input"]["closures"], [[8, 12]])
            self.assertEqual(revised["committed"], [dict(vessel_id="A", start_h=0, quantity_t=20)])
            self.assertIn(revised["committed"][0], proposals(revised["result"]["trips"]))
            self.assertEqual(
                check_schedule(revised["updated_input"], revised["result"]["trips"], 18)["status"],
                "valid_simulation",
            )

    def test_initial_planner_does_not_receive_future_closures(self):
        with patch("yana_planner.replanning.compare", wraps=compare) as initial:
            result = self.run_case()
        self.assertEqual(initial.call_args.args[0]["closures"], [])
        self.assertNotIn("update", initial.call_args.args[0].get("provenance", {}))
        # The originally planned second movement 9-10 crosses the later closure.
        self.assertTrue(any(t["load_end_h"] == 9 for t in result["initial"]["planner"]["trips"]))

    def test_committed_return_conflict_requires_intervention_without_fake_volume(self):
        self.event["closures"] = [[3, 4]]
        result = self.run_case()
        for name in ["revised_baseline", "revised_planner"]:
            revised = result[name]
            self.assertEqual(revised["result"]["status"], "requires_intervention")
            self.assertIsNone(revised["result"]["accepted_t"])
            self.assertEqual(revised["committed"], [dict(vessel_id="A", start_h=0, quantity_t=20)])
            self.assertEqual(revised["solver"], [])

    def test_boundary_after_return_does_not_block_preparation(self):
        self.event["closures"] = [[4, 5]]
        result = self.run_case()
        self.assertEqual(result["revised_planner"]["result"]["status"], "valid_simulation")
        self.assertEqual(result["revised_planner"]["result"]["accepted_t"], 40)

    def test_empty_event_adds_no_restriction(self):
        self.event["closures"] = []
        changed = apply_event(self.cfg, self.event)
        self.assertEqual(changed["closures"], [])
        self.assertIn("новых запретов нет", event_card(dict(event=self.event)))

    def test_invalid_event_is_rejected_before_solving(self):
        invalid = [
            None,
            [],
            dict(self.event, type="unknown"),
            dict(self.event, closures=None),
            dict(self.event, closures=[[1, 3]]),
            dict(self.event, closures=[[8, 8]]),
            dict(self.event, closures=[[9, 8]]),
            dict(self.event, closures=[[True, 8]]),
            dict(self.event, closures=[[8.5, 9]]),
            dict(self.event, rule_status=None),
            dict(self.event, data_kind=None),
            dict(self.event, forecast_issued_at="2026-08-10T00:00:00Z"),
        ]
        with patch("yana_planner.replanning.compare") as solver:
            for event in invalid:
                with self.subTest(event=event), self.assertRaises(InputError):
                    run_replanning(self.cfg, event, 18)
            solver.assert_not_called()

    def test_forecast_must_be_issued_by_announcement(self):
        self.event.update(data_kind="forecast", forecast_issued_at="2026-08-10T02:00:00+00:00")
        changed = apply_event(self.cfg, self.event)
        self.assertEqual(changed["closures"], [[8, 12]])
        self.event["forecast_issued_at"] = "2026-08-10T02:00:01+00:00"
        with self.assertRaisesRegex(InputError, "позже"):
            apply_event(self.cfg, self.event)

    def test_forecast_requires_t0_and_timezones(self):
        self.event.update(data_kind="forecast", forecast_issued_at=None)
        with self.assertRaises(InputError):
            apply_event(self.cfg, self.event)
        self.event["forecast_issued_at"] = "2026-08-10T01:00:00"
        with self.assertRaises(InputError):
            apply_event(self.cfg, self.event)
        self.event["forecast_issued_at"] = "2026-08-10T01:00:00Z"
        self.cfg["t0"] = "2026-08-10T00:00:00"
        with self.assertRaises(InputError):
            apply_event(self.cfg, self.event)
        self.cfg["timezone"] = "UTC"
        self.assertEqual(apply_event(self.cfg, self.event)["closures"], [[8, 12]])

    def test_reanalysis_is_an_explicit_historical_scenario(self):
        self.event.update(data_kind="reanalysis", source="Test metadata, not fetched ERA5")
        result = self.run_case()
        self.assertIsNone(result["event"]["forecast_issued_at"])
        self.assertEqual(result["event"]["source"], self.event["source"])
        page = event_card(result)
        self.assertIn("Исторический реанализ", page)
        self.assertIn("не проверка качества прогноза", page)

    def test_economic_strategy_survives_weather_update(self):
        result = self.run_case(strategy="cost", cost_terms=self.terms, adaptive=True)
        self.assertEqual(result["planning_strategy"], "cost")
        self.assertEqual(result["objective_priority"][1], "total_rub")
        self.assertEqual(result["revised_planner"]["result"]["accepted_t"], 40)
        self.assertEqual(result["costs"]["planner"]["status"], "scenario_cost")
        self.assertEqual(result["cost_terms"], self.terms)
        self.assertIsNone(result["costs"]["actual_savings_rub"])
        self.assertIn("меньше сценарные затраты", comparisons(result)["После события"]["tradeoff"])
        for committed in result["revised_planner"]["committed"]:
            self.assertIn(committed, proposals(result["revised_planner"]["result"]["trips"]))

    def test_intervention_has_no_economic_savings(self):
        self.event["closures"] = [[3, 4]]
        result = self.run_case(strategy="cost", cost_terms=self.terms)
        self.assertFalse(result["costs"]["equal_volume"])
        self.assertIsNone(result["costs"]["planner"])
        self.assertIsNone(result["costs"]["scenario_difference_rub"])
        self.assertFalse(comparisons(result)["После события"]["optimal"])

    def test_economic_strategy_also_handles_existing_arrival_delay(self):
        event = dict(type="arrival_delay", announced_at_h=2, arrival_index=0, new_arrival_h=10)
        result = run_replanning(
            self.cfg, event, 18, strategy="cost", cost_terms=self.terms, time_limit=2
        )
        self.assertEqual(result["experiment"], "single_arrival_delay")
        self.assertEqual(result["revised_planner"]["result"]["accepted_t"], 40)
        self.assertEqual(result["costs"]["planner"]["status"], "scenario_cost")

    def test_inputs_and_metadata_are_not_mutated(self):
        before = copy.deepcopy((self.cfg, self.event, self.terms))
        self.run_case(strategy="cost", cost_terms=self.terms)
        self.assertEqual((self.cfg, self.event, self.terms), before)

    def test_report_and_cli_accept_new_event(self):
        result = self.run_case(strategy="cost", cost_terms=self.terms)
        with tempfile.TemporaryDirectory() as directory:
            export_result(directory, result)
            page = (Path(directory) / "report.html").read_text()
            self.assertIn("Погодный запрет движения", page)
            self.assertIn("[8, 12)", page)
            self.assertIn("Сценарная стоимость после события", page)
            self.assertNotIn("Задержка поступления", page)
            cfg_path, event_path = Path(directory) / "input.json", Path(directory) / "event.json"
            cfg_path.write_text(json.dumps(self.cfg))
            event_path.write_text(json.dumps(self.event))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "replan",
                        str(cfg_path),
                        "--event",
                        str(event_path),
                        "--deadline",
                        "18",
                        "--strategy",
                        "cost",
                        "--cost-terms",
                        str(ROOT / "examples/economic-choice.costs.json"),
                        "--time-limit",
                        "2",
                        "--out",
                        str(Path(directory) / "cli"),
                    ]
                )
            self.assertEqual(code, 0)
            saved = json.loads((Path(directory) / "cli/results.json").read_text())
            self.assertEqual(saved["experiment"], "single_weather_closure")
            self.assertEqual(saved["planning_strategy"], "cost")


if __name__ == "__main__":
    unittest.main()
