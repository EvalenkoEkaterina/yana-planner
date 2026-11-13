import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from yana_planner.domain import InputError
from yana_planner.multi_stop import check_voyage, normalize, plan_two_stop
from yana_planner.service import ROOT


def example():
    return json.loads((ROOT / "examples/multi_stop/two-recipients.json").read_text())


class MultiStopTests(unittest.TestCase):
    def test_required_deliveries_mass_balance_and_priority(self):
        cfg = example()
        before = deepcopy(cfg)
        result = plan_two_stop(cfg)
        self.assertTrue(result["goal_met"])
        self.assertTrue(result["earliest_second_proven"])
        plan = result["plan"]
        self.assertEqual(plan["loaded_t"], 600)
        self.assertEqual(
            [(d["accepted_t"], d["receipt_h"], d["cargo_after_t"]) for d in plan["deliveries"]],
            [(200, 52, 400), (400, 88, 0)],
        )
        self.assertEqual(plan["ready_h"], 132)
        self.assertEqual(cfg, before)
        self.assertEqual(check_voyage(cfg, plan["starts"])["errors"], [])

    def test_impossible_first_deadline_is_not_sacrificed_for_second(self):
        cfg = example()
        cfg["stops"][0]["deadline_h"] = 51
        result = plan_two_stop(cfg)
        self.assertEqual(result["status"], "infeasible")
        self.assertFalse(result["goal_met"])
        self.assertIsNone(result["plan"])

    def test_second_leg_closure_delays_second_without_breaking_first(self):
        cfg = example()
        cfg["movement_closures"]["leg2"] = [[54, 60]]
        result = plan_two_stop(cfg)
        self.assertTrue(result["goal_met"])
        self.assertEqual(result["plan"]["deliveries"][0]["receipt_h"], 52)
        self.assertEqual(result["plan"]["deliveries"][1]["receipt_h"], 96)

    def test_return_closure_is_checked_and_preparation_is_inside_horizon(self):
        cfg = example()
        cfg["movement_closures"]["return"] = [[90, 100]]
        result = plan_two_stop(cfg)
        self.assertEqual(result["plan"]["ready_h"], 144)
        self.assertEqual(result["plan"]["starts"]["depart_return_h"], 100)
        cfg["horizon_h"] = 140
        self.assertEqual(plan_two_stop(cfg)["status"], "infeasible")

    def test_capacity_and_source_shortage_are_explicit_infeasibility(self):
        for key in ("capacity", "source"):
            cfg = example()
            if key == "capacity":
                cfg["vessel"]["capacity_t"] = 599
            else:
                cfg["arrivals"][0]["t"] = 499
            with self.subTest(key=key):
                self.assertEqual(plan_two_stop(cfg)["status"], "infeasible")

    def test_checker_catches_independently_tampered_schedule(self):
        cfg = example()
        starts = plan_two_stop(cfg)["plan"]["starts"]
        invalid = dict(starts, depart2_h=50)
        self.assertEqual(check_voyage(cfg, invalid)["status"], "invalid_schedule")
        cfg["movement_closures"]["leg1"] = [[30, 40]]
        self.assertTrue(any("запрет" in e for e in check_voyage(cfg, starts)["errors"]))
        self.assertEqual(plan_two_stop(cfg)["status"], "infeasible")

    def test_bad_inputs_and_unresolved_solver_are_not_success(self):
        cfg = example()
        cfg["stops"][1]["id"] = cfg["stops"][0]["id"]
        with self.assertRaises(InputError):
            normalize(cfg)
        for budget in (True, 0, float("nan")):
            with self.assertRaises(InputError):
                plan_two_stop(example(), stage_limit_s=budget)
        with patch("yana_planner.multi_stop.milp", return_value=SimpleNamespace(status=1, x=None)):
            result = plan_two_stop(example())
        self.assertEqual(result["status"], "no_feasible_solution_found")
        self.assertFalse(result["earliest_second_proven"])
        self.assertFalse(result["goal_met"])
