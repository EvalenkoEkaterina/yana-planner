import copy
import json
import unittest
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.economics import compare_cost
from yana_planner.ushanova_economics import break_even, run_cost_grid

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = "active_vessel_hour_rub"
WAITING = "waiting_vessel_hour_rub"


class EconomicsHandoffTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "examples/r03.json").read_text())
        result = json.loads((ROOT / "demos/static/results.json").read_text())
        self.baseline = result["baseline"]["trips"]
        self.alternative = result["planner"]["trips"]
        self.terms = json.loads((ROOT / "examples/ushanova/terms.json").read_text())

    def grid(self):
        return run_cost_grid(
            self.cfg,
            self.baseline,
            self.alternative,
            328,
            self.terms,
            x_parameter=ACTIVE,
            x_values=[500, 1000, 2000],
            y_parameter=WAITING,
            y_values=[250, 500, 1000],
        )

    def threshold(self, **kwargs):
        return break_even(
            self.cfg,
            self.baseline,
            self.alternative,
            328,
            self.terms,
            parameter=kwargs.get("parameter", WAITING),
        )

    def test_nine_combinations_and_manual_base_cost(self):
        rows = self.grid()
        self.assertEqual(len(rows), 9)
        base = rows[4]
        # 936 available hours, 510/404 active hours, 6/5 trips.
        self.assertEqual(base["baseline_total_rub"], 6 * 25000 + 510 * 1000 + 426 * 500)
        self.assertEqual(base["alternative_total_rub"], 5 * 25000 + 404 * 1000 + 532 * 500)
        self.assertEqual(base["scenario_difference_rub"], 78000)

    def test_does_not_mutate_inputs(self):
        before = copy.deepcopy((self.cfg, self.baseline, self.alternative, self.terms))
        self.grid()
        self.threshold()
        self.assertEqual(before, (self.cfg, self.baseline, self.alternative, self.terms))

    def test_explicit_zero_and_missing_rate(self):
        self.terms["separate_trip_operations_rub"] = 0
        rows = self.grid()
        self.assertIsNotNone(rows[0]["scenario_difference_rub"])
        self.terms["separate_trip_operations_rub"] = None
        self.assertTrue(all(row["status"] == "missing_cost_inputs" for row in self.grid()))
        self.assertEqual(self.threshold()["status"], "missing_cost_inputs")

    def test_threshold_changes_sign_on_both_sides(self):
        result = self.threshold()
        threshold = result["threshold_rub"]
        self.assertAlmostEqual(threshold, 1000 + 25000 / 106)
        differences = []
        for value in [result["lower_grid_rate_rub"], result["upper_grid_rate_rub"]]:
            terms = dict(self.terms, waiting_vessel_hour_rub=value)
            differences.append(
                compare_cost(self.cfg, self.baseline, self.alternative, 328, terms)[
                    "scenario_difference_rub"
                ]
            )
        self.assertGreater(differences[0], 0)
        self.assertLess(differences[1], 0)
        self.assertEqual(result["lower_grid_rate_rub"], 1235.84)
        self.assertEqual(result["upper_grid_rate_rub"], 1235.85)
        self.assertEqual(differences, [0.96, -0.1])

    def test_exact_kopeck_threshold_has_equal_cost(self):
        self.terms["separate_trip_operations_rub"] = 26500
        result = self.threshold()
        self.assertEqual(result["threshold_rub"], 1250)
        self.assertEqual(result["lower_grid_rate_rub"], result["upper_grid_rate_rub"])
        self.assertEqual(result["lower_grid_difference_rub"], 0)

    def test_grid_does_not_exhaust_generator_of_rates(self):
        rows = run_cost_grid(
            self.cfg,
            self.baseline,
            self.alternative,
            328,
            self.terms,
            x_parameter=ACTIVE,
            x_values=iter([500, 1000]),
            y_parameter=WAITING,
            y_values=iter([250, 500]),
        )
        self.assertEqual(len(rows), 4)

    def test_analytic_threshold_rejects_fractional_billing_horizon(self):
        self.terms["horizon_h"] = 492.5
        with self.assertRaisesRegex(InputError, "целочасового"):
            self.threshold()

    def test_no_threshold_when_plans_identical(self):
        result = break_even(
            self.cfg, self.baseline, self.baseline, 328, self.terms, parameter=WAITING
        )
        self.assertEqual(result["status"], "all_rates_equal")
        self.assertIsNone(result["threshold_rub"])

    def test_zero_coefficient_with_nonzero_intercept(self):
        # Equal trip counts with different activity have no trip-rate threshold.
        baseline = [dict(vessel_id="SIM-A", start_h=0, quantity_t=100)]
        alternative = [dict(vessel_id="SIM-A", start_h=0, quantity_t=100)]
        alternative[0] = dict(vessel_id="SIM-B", start_h=48, quantity_t=100)
        result = break_even(
            self.cfg,
            baseline,
            alternative,
            328,
            self.terms,
            parameter="separate_trip_operations_rub",
        )
        self.assertEqual(result["status"], "no_threshold")

    def test_negative_root_is_not_valid_tariff(self):
        self.terms[WAITING] = 0
        result = self.threshold(parameter=ACTIVE)
        self.assertEqual(result["status"], "no_nonnegative_threshold")
        self.assertIsNone(result["threshold_rub"])
        self.assertLess(result["mathematical_root_rub"], 0)

    def test_unequal_volume_and_invalid_plan_rejected(self):
        self.alternative[0]["quantity_t"] -= 1
        with self.assertRaises(InputError):
            self.grid()
        self.alternative[0]["start_h"] = -1
        with self.assertRaises(InputError):
            self.threshold()

    def test_invalid_rates_rejected(self):
        for invalid in [-1, True, float("nan"), float("inf")]:
            with self.subTest(invalid=invalid), self.assertRaises(InputError):
                run_cost_grid(
                    self.cfg,
                    self.baseline,
                    self.alternative,
                    328,
                    self.terms,
                    x_parameter=ACTIVE,
                    x_values=[invalid],
                    y_parameter=WAITING,
                    y_values=[0],
                )


if __name__ == "__main__":
    unittest.main()
