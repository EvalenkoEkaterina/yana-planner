"""Economic goals are checked on tiny instances with hand-derived optima."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yana_planner.cli import main
from yana_planner.domain import InputError
from yana_planner.economics import cost
from yana_planner.experiments import comparisons
from yana_planner.io import export_result
from yana_planner.planning import compare, optimize
from yana_planner.validation import check_schedule

ROOT = Path(__file__).resolve().parents[1]


class CostPlanningTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "examples/economic-choice.json").read_text())
        self.terms = json.loads((ROOT / "examples/economic-choice.costs.json").read_text())

    def calculate(self, deadline=13, **kwargs):
        return compare(
            self.cfg, deadline, strategy="cost", cost_terms=self.terms, time_limit=2, **kwargs
        )

    def test_two_cheap_trips_beat_one_expensive_at_equal_volume(self):
        old = compare(self.cfg, 13, time_limit=2, cost_terms=self.terms)
        new = self.calculate()
        self.assertEqual(old["planner"]["trip_count"], 1)
        self.assertEqual(old["costs"]["planner"]["total_rub"], 1000)
        self.assertEqual(new["planner"]["accepted_t"], 20)
        self.assertEqual(new["planner"]["trip_count"], 2)
        self.assertEqual(new["costs"]["planner"]["total_rub"], 200)
        self.assertEqual(new["planner"]["last_receipt_h"], 8)
        self.assertEqual({t["vessel_id"] for t in new["planner"]["trips"]}, {"B"})
        self.assertEqual(new["costs"]["scenario_difference_rub"], 800)
        self.assertEqual(new["objective_priority"], ["accepted_t", "total_rub", "last_receipt_h"])
        self.assertTrue(all(stage["optimal"] for stage in new["solver"]))

    def test_tariffs_change_the_selected_ship(self):
        self.terms["vessel_rates"]["A"]["separate_trip_operations_rub"] = 50
        new = self.calculate()
        self.assertEqual(new["planner"]["trip_count"], 1)
        self.assertEqual(new["planner"]["trips"][0]["vessel_id"], "A")
        self.assertEqual(new["costs"]["planner"]["total_rub"], 50)

    def test_full_delivery_precedes_a_cheaper_partial_plan(self):
        new = self.calculate(deadline=4)
        self.assertEqual(new["planner"]["accepted_t"], 20)
        self.assertEqual(new["costs"]["planner"]["total_rub"], 1000)
        self.assertEqual(new["planner"]["trip_count"], 1)

    def test_last_receipt_breaks_equal_cost_tie(self):
        self.terms["vessel_rates"]["A"]["separate_trip_operations_rub"] = 200
        new = self.calculate()
        self.assertEqual(new["costs"]["planner"]["total_rub"], 200)
        self.assertEqual(new["planner"]["last_receipt_h"], 3)
        self.assertEqual(new["planner"]["trip_count"], 1)

    def test_one_kopeck_difference_is_not_lost(self):
        self.terms["vessel_rates"]["A"]["separate_trip_operations_rub"] = 2.51
        self.terms["vessel_rates"]["B"]["separate_trip_operations_rub"] = 1.25
        new = self.calculate()
        self.assertEqual(new["costs"]["planner"]["total_rub"], 2.5)
        self.assertEqual(new["planner"]["trip_count"], 2)
        self.assertEqual(new["costs"]["scenario_difference_rub"], 0.01)

    def test_paid_waiting_constant_is_excluded_from_adaptive_bounds(self):
        for rates in self.terms["vessel_rates"].values():
            rates["active_vessel_hour_rub"] = 10
        self.terms["vessel_rates"]["A"]["waiting_vessel_hour_rub"] = 1
        self.terms["vessel_rates"]["B"]["waiting_vessel_hour_rub"] = 2
        new = self.calculate(adaptive=True)
        # A unused: 20*1. B: 2*100 + 10*10 + 10*2 = 320.
        self.assertEqual(new["costs"]["planner"]["total_rub"], 340)
        self.assertEqual(new["planner"]["last_receipt_h"], 8)
        self.assertTrue(all(stage["optimal"] for stage in new["solver"]))
        self.assertEqual(new["solver"][1]["attempts"][-1]["best_objective"], 28000)

    def test_waiting_more_expensive_than_activity_has_negative_trip_coefficient(self):
        for rates in self.terms["vessel_rates"].values():
            rates["separate_trip_operations_rub"] = 0
        self.terms["vessel_rates"]["A"]["waiting_vessel_hour_rub"] = 10
        new = self.calculate(adaptive=True)
        # Three 5-hour turns replace 15 hours of paid waiting: (20-15)*10.
        self.assertEqual(new["costs"]["planner"]["total_rub"], 50)
        self.assertEqual(sum(t["vessel_id"] == "A" for t in new["planner"]["trips"]), 3)
        self.assertTrue(all(stage["optimal"] for stage in new["solver"]))

    def test_timeout_preserves_cheaper_incumbent_even_with_more_trips(self):
        incumbent = [dict(vessel_id="B", start_h=h, quantity_t=10) for h in (0, 5)]
        with patch(
            "yana_planner.planning.milp",
            return_value=SimpleNamespace(
                status=1,
                message="time limit",
                x=None,
            ),
        ):
            new = self.calculate(adaptive=True, incumbent=incumbent)
        self.assertEqual(new["costs"]["planner"]["total_rub"], 200)
        self.assertEqual(new["planner"]["trip_count"], 2)
        self.assertTrue(all(not stage["optimal"] for stage in new["solver"]))

    def test_started_trip_is_preserved_in_economic_mode(self):
        fixed = [dict(vessel_id="A", start_h=0, quantity_t=10)]
        plan, logs = optimize(
            self.cfg,
            13,
            strategy="cost",
            cost_terms=self.terms,
            fixed=fixed,
            decision_h=1,
            time_limit=2,
        )
        self.assertIn(fixed[0], plan)
        self.assertEqual(check_schedule(self.cfg, plan, 13)["accepted_t"], 20)
        self.assertEqual(cost(self.cfg, plan, 13, self.terms)["total_rub"], 1100)
        self.assertTrue(all(stage["optimal"] for stage in logs))

    def test_missing_or_invalid_rates_stop_before_solver(self):
        bad = copy.deepcopy(self.terms)
        bad["vessel_rates"]["B"]["active_vessel_hour_rub"] = None
        for terms in [None, {}, bad]:
            with self.subTest(terms=terms), patch("yana_planner.planning.milp") as solver:
                with self.assertRaises(InputError):
                    optimize(self.cfg, 13, strategy="cost", cost_terms=terms)
                solver.assert_not_called()

    def test_billing_horizon_does_not_shrink_the_physical_search_space(self):
        self.terms["horizon_h"] = 6  # Covers an early plan, not every possible turn.
        with patch("yana_planner.planning.milp") as solver:
            with self.assertRaisesRegex(InputError, "все варианты"):
                self.calculate()
            solver.assert_not_called()

    def test_invalid_strategy_and_unsafe_money_range_are_rejected(self):
        with self.assertRaises(InputError):
            optimize(self.cfg, 13, strategy="unknown")
        self.terms["vessel_rates"]["A"]["separate_trip_operations_rub"] = 10**16
        with self.assertRaisesRegex(InputError, "слишком велики"):
            self.calculate()

    def test_no_valid_ready_plan_is_not_priced_as_zero(self):
        cfg = json.loads((ROOT / "examples/urgent-stock.json").read_text())
        terms = dict(
            billing_mode="all_available_time",
            horizon_h=12,
            separate_trip_operations_rub=1,
            active_vessel_hour_rub=0,
            waiting_vessel_hour_rub=0,
        )
        new = compare(cfg, 8, strategy="cost", cost_terms=terms, time_limit=2)
        self.assertEqual(new["planner"]["accepted_t"], 20)
        self.assertIsNone(new["costs"]["baseline"])
        self.assertIsNone(new["costs"]["scenario_difference_rub"])
        self.assertFalse(new["costs"]["equal_volume"])

    def test_report_discloses_economic_priorities_and_synthetic_rates(self):
        new = self.calculate()
        summary = comparisons(new)["Расчёт"]
        self.assertIn("меньше сценарные затраты", summary["tradeoff"])
        self.assertNotIn("меньше рейсов", summary["tradeoff"])
        self.assertIn("на 1 больше", summary["conclusion"])
        self.assertIn("800.00 руб.", summary["conclusion"])
        with tempfile.TemporaryDirectory() as directory:
            export_result(directory, new)
            page = (Path(directory) / "report.html").read_text()
        self.assertIn("Сценарная стоимость", page)
        self.assertIn("Сценарные", page)
        self.assertIn("200.00", page)
        self.assertNotIn("затем меньше рейсов", page)

    def test_input_objects_remain_unchanged(self):
        before = copy.deepcopy((self.cfg, self.terms))
        self.calculate()
        self.assertEqual((self.cfg, self.terms), before)

    def test_cli_runs_cost_strategy_and_missing_terms_leave_failure_report(self):
        with tempfile.TemporaryDirectory() as directory:
            args = [
                "plan",
                str(ROOT / "examples/economic-choice.json"),
                "--strategy",
                "cost",
                "--time-limit",
                "2",
                "--out",
                directory,
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    args + ["--cost-terms", str(ROOT / "examples/economic-choice.costs.json")]
                )
            self.assertEqual(code, 0)
            result = json.loads((Path(directory) / "results.json").read_text())
            self.assertEqual(result["costs"]["planner"]["total_rub"], 200)
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(args)
            self.assertEqual(code, 2)
            failed = json.loads((Path(directory) / "results.json").read_text())
            self.assertIsNone(failed["accepted_t"])
            self.assertNotIn("Сценарная стоимость", (Path(directory) / "report.html").read_text())


if __name__ == "__main__":
    unittest.main()
