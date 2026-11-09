"""Historical prototype outputs are regression fixtures, not real observations."""

import json
import unittest
from pathlib import Path

from yana_planner.planning import ready
from yana_planner.validation import check_schedule

ROOT = Path(__file__).resolve().parents[1]


class V2RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configs = {
            c["id"]: c
            for c in json.loads((ROOT / "examples/v2_suite.json").read_text())["scenarios"]
        }
        cls.reference = json.loads((ROOT / "tests/fixtures/v2_schedules.json").read_text())

    def test_all_144_saved_schedules_remain_physically_valid(self):
        self.assertEqual(len(self.reference), 72)
        for case in self.reference:
            for method in ["baseline", "planner"]:
                with self.subTest(
                    case=case["scenario_id"], deadline=case["deadline_h"], method=method
                ):
                    r = check_schedule(
                        self.configs[case["scenario_id"]],
                        case[method]["schedule"],
                        case["deadline_h"],
                    )
                    self.assertEqual(r["status"], "valid_simulation", r["errors"])
                    for key in ["accepted_t", "trip_count", "last_receipt_h"]:
                        self.assertEqual(r[key], case[method][key])

    def test_all_72_greedy_results_reproduce_without_archived_schedules(self):
        for case in self.reference:
            with self.subTest(case=case["scenario_id"], deadline=case["deadline_h"]):
                c = self.configs[case["scenario_id"]]
                p = ready(c, case["deadline_h"])
                r = check_schedule(c, p, case["deadline_h"])
                self.assertEqual(r["status"], "valid_simulation")
                for key in ["accepted_t", "trip_count", "last_receipt_h"]:
                    self.assertEqual(r[key], case["baseline"][key])
