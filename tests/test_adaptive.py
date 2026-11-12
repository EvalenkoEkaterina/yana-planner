"""Adaptive retries preserve feasible plans, proofs and executed history."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from test_engine import tiny

from yana_planner.planning import candidates, compare, optimize
from yana_planner.replanning import run_replanning
from yana_planner.validation import check_schedule


class AdaptiveTests(unittest.TestCase):
    def test_optimal_stage_does_not_retry(self):
        from yana_planner.planning import milp

        with patch("yana_planner.planning.milp", wraps=milp) as solver:
            result = compare(tiny(), 20, time_limit=1, adaptive=True)
        self.assertEqual(solver.call_count, 3)
        self.assertTrue(all(s["optimal"] for s in result["solver"]))
        self.assertTrue(all(len(s["attempts"]) == 1 for s in result["solver"]))
        self.assertEqual(result["planner"]["accepted_t"], 20)

    def test_stage_budget_is_shared_across_attempts(self):
        now = [0.0]
        budgets = []

        def timeout(*args, **kwargs):
            budget = kwargs["options"]["time_limit"]
            budgets.append(budget)
            now[0] += budget
            return SimpleNamespace(status=1, message="time limit", x=None)

        with (
            patch("yana_planner.planning.monotonic", side_effect=lambda: now[0]),
            patch("yana_planner.planning.milp", side_effect=timeout),
        ):
            plan, logs = optimize(tiny(), 20, adaptive=True, time_limit=30)
        self.assertEqual(budgets, [2, 8, 20] * 3)
        self.assertTrue(all(s["total_elapsed_s"] == 30 for s in logs))
        self.assertTrue(all(not s["optimal"] for s in logs))
        self.assertEqual(check_schedule(tiny(), plan, 20)["accepted_t"], 20)

    def test_valid_improvement_survives_later_timeouts(self):
        cfg = tiny()
        cfg["arrivals"] = [dict(h=0, t=20)]
        baseline = [dict(vessel_id="A", start_h=h, quantity_t=10) for h in [3, 10]]
        better = [dict(vessel_id="A", start_h=h, quantity_t=10) for h in [0, 7]]
        cand = candidates(cfg, 20)
        x = np.zeros(2 * len(cand) + 1)
        for trip in better:
            i = next(i for i, c in enumerate(cand) if c["start_h"] == trip["start_h"])
            x[i] = 1
            x[len(cand) + i] = 10
        x[-1] = 11
        results = [SimpleNamespace(status=1, message="time limit", x=x)] + [
            SimpleNamespace(status=1, message="time limit", x=None) for _ in range(8)
        ]
        with patch("yana_planner.planning.milp", side_effect=results):
            plan, logs = optimize(cfg, 20, baseline, adaptive=True, time_limit=30)
        verdict = check_schedule(cfg, plan, 20)
        self.assertEqual(verdict["status"], "valid_simulation")
        self.assertEqual(verdict["last_receipt_h"], 11)
        self.assertTrue(all(not s["optimal"] for s in logs))

    def test_smaller_budget_has_no_extra_attempt(self):
        with patch(
            "yana_planner.planning.milp",
            return_value=SimpleNamespace(status=1, message="time limit", x=None),
        ) as solver:
            optimize(tiny(), 20, adaptive=True, time_limit=0.1)
        self.assertEqual(solver.call_count, 3)
        self.assertTrue(
            all(c.kwargs["options"]["time_limit"] <= 0.1 for c in solver.call_args_list)
        )

    def test_delay_keeps_started_trips_in_adaptive_mode(self):
        cfg = tiny()
        event = dict(type="arrival_delay", announced_at_h=2, arrival_index=1, new_arrival_h=12)
        original = copy.deepcopy(cfg)
        result = run_replanning(cfg, event, 20, time_limit=0.2, adaptive=True)
        revised = result["revised_planner"]
        self.assertEqual(revised["result"]["status"], "valid_simulation")
        for trip in revised["committed"]:
            self.assertTrue(
                any(all(t[k] == v for k, v in trip.items()) for t in revised["result"]["trips"])
            )
        self.assertEqual(cfg, original)
