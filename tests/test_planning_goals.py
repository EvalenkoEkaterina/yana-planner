import json
import unittest
from unittest.mock import patch

from yana_planner.domain import InputError
from yana_planner.planning import compare
from yana_planner.planning_goals import earliest_required_volume
from yana_planner.service import ROOT


def scenario():
    cfg = json.loads((ROOT / "examples/economic-choice.json").read_text())
    cfg["vessels"][0]["up_h"] = 8
    cfg["return_cutoff_h"] = 30
    return cfg


class PlanningGoalTests(unittest.TestCase):
    def test_earlier_completion_can_require_more_trips(self):
        cfg = scenario()
        original = compare(cfg, 20, time_limit=2)["planner"]
        result = earliest_required_volume(cfg, 20)
        self.assertEqual(
            (original["accepted_t"], original["trip_count"], original["last_receipt_h"]),
            (20, 1, 10),
        )
        self.assertTrue(result["goal_met"])
        self.assertTrue(result["earliest_proven"])
        self.assertEqual(
            (result["plan"]["accepted_t"], result["plan"]["trip_count"], result["last_receipt_h"]),
            (20, 2, 8),
        )

    def test_partial_volume_never_satisfies_required_volume(self):
        cfg = scenario()
        cfg["target_t"] = 30
        result = earliest_required_volume(cfg, 20)
        self.assertFalse(result["goal_met"])
        self.assertEqual(result["status"], "required_volume_infeasible")
        self.assertIsNone(result["plan"])
        self.assertIsNone(result["last_receipt_h"])
        self.assertIsNotNone(result["best_partial"])

    def test_unproved_partial_result_cannot_prove_shorter_deadline_infeasible(self):
        cfg = scenario()
        real = compare(cfg, 20, time_limit=2)
        unresolved = compare(cfg, 2, time_limit=2)
        unresolved["solver"] = [dict(stage="maximize_tonnes", optimal=False, status=1)]
        with patch("yana_planner.planning_goals.compare", side_effect=[real, unresolved]):
            result = earliest_required_volume(cfg, 20)
        self.assertTrue(result["goal_met"])
        self.assertFalse(result["earliest_proven"])
        self.assertEqual(result["stop_reason"], "shorter_deadline_unresolved")

    def test_budget_parameters_and_deadline_are_validated(self):
        for value in (True, 0, -1, float("inf")):
            with self.subTest(value=value), self.assertRaises(InputError):
                earliest_required_volume(scenario(), 20, total_budget_s=value)
        with self.assertRaises(InputError):
            earliest_required_volume(scenario(), 31)
