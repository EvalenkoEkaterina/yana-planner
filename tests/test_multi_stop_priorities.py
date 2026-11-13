import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import yana_planner.multi_stop as module
from yana_planner.domain import InputError
from yana_planner.service import ROOT


def example():
    return json.loads((ROOT / "examples/multi_stop/four-recipients.json").read_text())


class MultiStopPriorityTests(unittest.TestCase):
    def test_priority_order_changes_objectives_not_visits(self):
        cfg = example()
        cfg["receipt_priority"] = ["ust-kuyga", "kazachye", "batagay", "saydy-verkhoyansky"]
        before = deepcopy(cfg)
        result = module.plan_multi_stop(cfg)
        self.assertTrue(result["goal_met"])
        self.assertTrue(result["primary_receipt_proven"])
        self.assertTrue(result["lexicographic_optimal"])
        self.assertEqual(
            [log["stop_id"] for log in result["solver"] if "stop_id" in log],
            cfg["receipt_priority"],
        )
        self.assertEqual(result["objective_stop_id"], "ust-kuyga")
        self.assertEqual(
            [d["stop_id"] for d in result["plan"]["deliveries"]], [s["id"] for s in cfg["stops"]]
        )
        self.assertEqual(cfg, before)
        self.assertFalse(result["earliest_last_proven"])
        self.assertTrue(all(p["optimal_given_higher_priorities"] for p in result["receipt_optima"]))

    def test_receipt_times_can_coincide_under_different_priorities(self):
        cfg = example()
        original = module.plan_multi_stop(cfg)
        cfg["receipt_priority"] = [s["id"] for s in cfg["stops"]]
        changed = module.plan_multi_stop(cfg)
        self.assertEqual(
            [d["receipt_h"] for d in original["plan"]["deliveries"]],
            [d["receipt_h"] for d in changed["plan"]["deliveries"]],
        )
        self.assertNotEqual(original["receipt_priority"], changed["receipt_priority"])

    def test_priority_never_waives_another_required_deadline(self):
        cfg = example()
        cfg["receipt_priority"] = [s["id"] for s in cfg["stops"]]
        cfg["stops"][2]["deadline_h"] = 80
        result = module.plan_multi_stop(cfg)
        self.assertEqual(result["status"], "infeasible")
        self.assertIsNone(result["plan"])

    def test_every_recipient_must_appear_once(self):
        for priority in (
            [],
            "batagay",
            ["batagay"] * 4,
            ["kazachye", "ust-kuyga", "missing", "batagay"],
            ["kazachye", "ust-kuyga", [], True],
        ):
            cfg = example()
            cfg["receipt_priority"] = priority
            with self.subTest(priority=priority), self.assertRaises(InputError):
                module.plan_multi_stop(cfg)

    def test_timeout_keeps_checked_plan_without_claiming_full_proof(self):
        real_solver = module.milp
        calls = 0

        def limited(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_solver(*args, **kwargs)
            return SimpleNamespace(status=1, x=None)

        with patch("yana_planner.multi_stop.milp", side_effect=limited):
            result = module.plan_multi_stop(example())
        self.assertTrue(result["goal_met"])
        self.assertTrue(result["primary_receipt_proven"])
        self.assertFalse(result["lexicographic_optimal"])
        self.assertFalse(result["receipt_optima"][1]["optimal_given_higher_priorities"])
        self.assertEqual(
            module.check_voyage(example(), result["plan"]["starts"])["status"], "valid_simulation"
        )
