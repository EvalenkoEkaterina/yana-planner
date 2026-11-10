import copy
import unittest

from test_economics import r03

from yana_planner.comparison import select_economic_comparison
from yana_planner.domain import InputError
from yana_planner.economics import cost
from yana_planner.replanning import apply_event
from yana_planner.sensitivity import run_sensitivity
from yana_planner.validation import check_schedule


def packet():
    cfg, ready, milp = r03()
    event = dict(type="arrival_delay", announced_at_h=60, arrival_index=1, new_arrival_h=168)
    changed = apply_event(cfg, event)
    after_ready = [
        dict(vessel_id=v, start_h=h, quantity_t=q)
        for v, h, q in [
            ("SIM-A", 0, 400),
            ("SIM-B", 48, 400),
            ("SIM-A", 106, 25),
            ("SIM-B", 168, 400),
            ("SIM-A", 212, 400),
            ("SIM-B", 244, 25),
        ]
    ]
    after_milp = [
        dict(vessel_id=v, start_h=h, quantity_t=q)
        for v, h, q in [
            ("SIM-A", 8, 400),
            ("SIM-B", 48, 400),
            ("SIM-B", 168, 400),
            ("SIM-A", 192, 400),
            ("SIM-B", 232, 50),
        ]
    ]
    result = dict(
        scenario_id="R03",
        deadline_h=328,
        event=event,
        initial_input=cfg,
        initial=dict(
            baseline=check_schedule(cfg, ready, 328), planner=check_schedule(cfg, milp, 328)
        ),
        revised_baseline=dict(
            updated_input=changed, result=check_schedule(changed, after_ready, 328)
        ),
        revised_planner=dict(
            updated_input=changed, result=check_schedule(changed, after_milp, 328)
        ),
    )
    return cfg, changed, result


class EconomicsFixTests(unittest.TestCase):
    def test_after_uses_updated_conditions_and_recalculates_gaps(self):
        cfg, _, result = packet()
        effective, a, b, context = select_economic_comparison(cfg, result, 328)
        self.assertEqual(effective["arrivals"][1]["h"], 168)
        self.assertEqual(context["phase"], "after")
        grid = run_sensitivity(effective, a, b, 328, horizon_h=492)
        mode = grid["billing_modes"]["inter_voyage_gaps"]
        self.assertEqual(mode["baseline_metrics"]["paid_waiting_h"], 68)
        self.assertEqual(mode["alternative_metrics"]["paid_waiting_h"], 134)
        before, _, _, context = select_economic_comparison(cfg, result, 328, phase="before")
        self.assertEqual(before["arrivals"][1]["h"], 120)
        self.assertEqual(context["phase"], "before")

    def test_invalid_status_and_false_valid_status_are_both_rejected(self):
        cfg, changed, result = packet()
        _, ready, milp = r03()
        for force_valid in [False, True]:
            broken = copy.deepcopy(result)
            for key, plan in [("revised_baseline", ready), ("revised_planner", milp)]:
                verdict = check_schedule(changed, plan, 328)
                if force_valid:
                    verdict.update(status="valid_simulation", accepted_t=1650)
                broken[key]["result"] = verdict
            with self.subTest(force_valid=force_valid), self.assertRaises(InputError):
                select_economic_comparison(cfg, broken, 328)

    def test_unknown_horizon_and_invalid_mode_are_controlled(self):
        cfg, a, b = r03()
        result = run_sensitivity(cfg, a, b, 328)
        self.assertEqual(result["status"], "missing_cost_inputs")
        self.assertIsNone(result["total_rub"])
        self.assertEqual(result["missing"], ["horizon_h"])
        with self.assertRaises(InputError):
            cost(cfg, a, 328, dict(billing_mode=[]))

    def test_negative_root_is_not_a_feasible_rate_and_parity_is_explicit(self):
        cfg, ready, milp = r03()
        ready[-1]["start_h"] = 260
        result = run_sensitivity(cfg, ready, milp, 328, horizon_h=492)
        threshold = result["billing_modes"]["inter_voyage_gaps"]["break_even_thresholds"][0]
        self.assertEqual(threshold["mathematical_waiting_rate_root_rub"], -3000)
        self.assertIsNone(threshold["break_even_waiting_vessel_hour_rub"])
        self.assertEqual(threshold["threshold_status"], "outside_nonnegative_domain")
        equal = run_sensitivity(cfg, milp, milp, 328, horizon_h=492)
        self.assertEqual(
            equal["billing_modes"]["inter_voyage_gaps"]["break_even_thresholds"][0][
                "threshold_status"
            ],
            "always_equal",
        )

    def test_different_updated_inputs_and_unequal_volume_are_rejected(self):
        cfg, changed, result = packet()
        broken = copy.deepcopy(result)
        broken["revised_planner"]["updated_input"]["destination_capacity_t"] = 1000
        with self.assertRaises(InputError):
            select_economic_comparison(cfg, broken, 328)
        broken = copy.deepcopy(result)
        short = broken["revised_baseline"]["result"]["trips"][:-1]
        broken["revised_baseline"]["result"] = check_schedule(changed, short, 328)
        with self.assertRaises(InputError):
            select_economic_comparison(cfg, broken, 328)
