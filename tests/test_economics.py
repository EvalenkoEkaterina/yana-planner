import json
import unittest
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.economics import cost, compare_cost
from yana_planner.sensitivity import run_sensitivity


ROOT = Path(__file__).resolve().parents[1]


def r03():
    cfg = json.loads((ROOT / "examples/r03.json").read_text())
    reference = json.loads((ROOT / "examples/r03.reference.json").read_text())

    def proposals(section):
        return [
            {k: trip[k] for k in ("vessel_id", "start_h", "quantity_t")}
            for trip in reference[section]["trips"]
        ]

    return cfg, proposals("baseline"), proposals("planner")


class EconomicsModesTests(unittest.TestCase):
    def setUp(self):
        self.cfg, self.ready_plan, self.milp_plan = r03()
        self.deadline = 328
        self.rates = dict(
            separate_trip_operations_rub=25_000,
            active_vessel_hour_rub=500,
            waiting_vessel_hour_rub=250,
        )

    def test_inter_voyage_gaps_ready_and_milp(self):
        ready_result = cost(
            self.cfg, self.ready_plan, self.deadline,
            dict(billing_mode="inter_voyage_gaps", **self.rates),
        )
        milp_result = cost(
            self.cfg, self.milp_plan, self.deadline,
            dict(billing_mode="inter_voyage_gaps", **self.rates),
        )
        self.assertEqual(ready_result["paid_waiting_h"], 8)
        self.assertEqual(milp_result["paid_waiting_h"], 30)
        self.assertEqual(ready_result["active_vessel_h"], 510)
        self.assertEqual(milp_result["active_vessel_h"], 404)

    def test_all_available_time_uses_common_horizon(self):
        terms = dict(
            billing_mode="all_available_time",
            horizon_h=328,
            **self.rates,
        )
        ready_result = cost(self.cfg, self.ready_plan, self.deadline, terms)
        milp_result = cost(self.cfg, self.milp_plan, self.deadline, terms)
        self.assertEqual(ready_result["paid_waiting_h"], 98)
        self.assertEqual(milp_result["paid_waiting_h"], 204)

    def test_inter_voyage_mode_does_not_require_horizon(self):
        result = cost(
            self.cfg, self.ready_plan, self.deadline,
            dict(billing_mode="inter_voyage_gaps", **self.rates),
        )
        self.assertEqual(result["status"], "scenario_cost")
        self.assertIsNone(result["horizon_h"])

    def test_unknown_rate_is_not_zero(self):
        terms = dict(
            billing_mode="inter_voyage_gaps",
            **{**self.rates, "waiting_vessel_hour_rub": None},
        )
        result = cost(self.cfg, self.ready_plan, self.deadline, terms)
        self.assertEqual(result["status"], "missing_cost_inputs")
        self.assertIsNone(result["total_rub"])

    def test_invalid_schedule_is_not_costed(self):
        invalid = [dict(x) for x in self.ready_plan]
        invalid[0]["quantity_t"] = 401
        with self.assertRaises(InputError):
            cost(
                self.cfg, invalid, self.deadline,
                dict(billing_mode="inter_voyage_gaps", **self.rates),
            )

    def test_unequal_delivered_volume_cannot_be_compared(self):
        with self.assertRaises(InputError):
            compare_cost(
                self.cfg, self.ready_plan, self.ready_plan[:1], self.deadline,
                dict(billing_mode="inter_voyage_gaps", **self.rates),
            )

    def test_sensitivity_has_nine_rows_per_mode(self):
        result = run_sensitivity(
            self.cfg, self.ready_plan, self.milp_plan, self.deadline, horizon_h=328
        )
        self.assertEqual(len(result["billing_modes"]["inter_voyage_gaps"]["rows"]), 9)
        self.assertEqual(len(result["billing_modes"]["all_available_time"]["rows"]), 9)

    def test_break_even_is_derived_from_metrics(self):
        result = run_sensitivity(
            self.cfg, self.ready_plan, self.milp_plan, self.deadline, horizon_h=328
        )
        inter = result["billing_modes"]["inter_voyage_gaps"]["break_even_thresholds"]
        # (25,000 + (510 - 404) * 500) / (30 - 8)
        self.assertAlmostEqual(inter[0]["break_even_waiting_vessel_hour_rub"], 3545.45454545)
        all_time = result["billing_modes"]["all_available_time"]["break_even_thresholds"]
        self.assertAlmostEqual(all_time[0]["break_even_waiting_vessel_hour_rub"], 735.84905660)

    def test_zero_waiting_delta_has_no_break_even_division(self):
        result = run_sensitivity(
            self.cfg, self.ready_plan, self.ready_plan, self.deadline, horizon_h=328
        )
        for mode in ("inter_voyage_gaps", "all_available_time"):
            thresholds = result["billing_modes"][mode]["break_even_thresholds"]
            self.assertTrue(all(x["break_even_waiting_vessel_hour_rub"] is None for x in thresholds))


if __name__ == "__main__":
    unittest.main()
