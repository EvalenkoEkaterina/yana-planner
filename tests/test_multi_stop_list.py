import json
import unittest
from copy import deepcopy

from yana_planner.domain import InputError
from yana_planner.multi_stop import check_voyage, normalize, plan_multi_stop, plan_two_stop
from yana_planner.service import ROOT


def example():
    return json.loads((ROOT / "examples/multi_stop/four-recipients.json").read_text())


class MultiStopListTests(unittest.TestCase):
    def test_four_recipients_keep_mass_and_order(self):
        cfg = example()
        before = deepcopy(cfg)
        result = plan_multi_stop(cfg)
        self.assertTrue(result["goal_met"])
        self.assertTrue(result["earliest_last_proven"])
        self.assertEqual(result["objective_stop_id"], "batagay")
        deliveries = result["plan"]["deliveries"]
        self.assertEqual([d["stop_id"] for d in deliveries], [s["id"] for s in cfg["stops"]])
        self.assertEqual([d["receipt_h"] for d in deliveries], [37, 59, 81, 101])
        self.assertEqual([d["cargo_after_t"] for d in deliveries], [450, 250, 150, 0])
        self.assertEqual(sum(d["accepted_t"] for d in deliveries), result["plan"]["loaded_t"])
        self.assertEqual(cfg, before)

    def test_third_leg_closure_propagates_to_later_stops(self):
        cfg = example()
        cfg["movement_closures"]["leg3"] = [[65, 85]]
        result = plan_multi_stop(cfg)
        self.assertTrue(result["goal_met"])
        self.assertEqual([d["receipt_h"] for d in result["plan"]["deliveries"]], [37, 59, 107, 127])
        self.assertEqual(check_voyage(cfg, result["plan"]["starts"])["errors"], [])

    def test_impossible_middle_deadline_and_checker_tampering(self):
        cfg = example()
        starts = plan_multi_stop(cfg)["plan"]["starts"]
        self.assertEqual(
            check_voyage(cfg, dict(starts, depart3_h=58))["status"], "invalid_schedule"
        )
        cfg["stops"][2]["deadline_h"] = 80
        result = plan_multi_stop(cfg)
        self.assertEqual(result["status"], "infeasible")
        self.assertIsNone(result["plan"])

    def test_one_and_eight_stops_are_supported(self):
        for count in (1, 8):
            cfg = example()
            cfg.update(initial_source_t=10 * count, arrivals=[])
            cfg["stops"] = [
                dict(id=f"stop-{i}", required_t=10, deadline_h=160, unload_h=1, windows=[[0, 160]])
                for i in range(count)
            ]
            cfg["vessel"]["travel_h"] = [2] * count
            cfg["movement_closures"] = {**{f"leg{i + 1}": [] for i in range(count)}, "return": []}
            result = plan_multi_stop(cfg)
            self.assertTrue(result["goal_met"])
            self.assertEqual(len(result["plan"]["deliveries"]), count)
            self.assertEqual(result["plan"]["deliveries"][-1]["cargo_after_t"], 0)

    def test_travel_and_closure_lists_must_match_stops(self):
        cfg = example()
        cfg["vessel"]["travel_h"].pop()
        with self.assertRaises(InputError):
            normalize(cfg)
        cfg = example()
        del cfg["movement_closures"]["leg3"]
        with self.assertRaises(InputError):
            normalize(cfg)
        with self.assertRaises(InputError):
            plan_two_stop(example())
        cfg = example()
        cfg["stops"].extend(deepcopy(cfg["stops"]) * 2)
        with self.assertRaises(InputError):
            normalize(cfg)
