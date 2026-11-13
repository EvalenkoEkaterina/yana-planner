import json
import unittest
from copy import deepcopy
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.weather_feed import mapped_points, normalize, restrictions

ROOT = Path(__file__).resolve().parents[1]


class FeedTests(unittest.TestCase):
    def setUp(self):
        folder = ROOT / "examples/weather_feed"
        self.values = normalize(json.loads((folder / "input.json").read_text()))
        self.rule = json.loads((folder / "rule.json").read_text())

    def run_rule(self, values, **kwargs):
        return restrictions(
            values,
            ["nizhneyansk", "ust_kuyga"],
            "2025-07-01T00:00:00+00:00",
            "2025-07-06T00:00:00+00:00",
            self.rule,
            **kwargs,
        )

    def test_raw_and_complete(self):
        self.assertEqual(len(self.values), 240)
        self.assertTrue(all(not x["correction_applied"] for x in self.values))
        self.assertEqual(self.run_rule(self.values)["closures"], [])

    def test_missing_blocks_instead_of_clear_weather(self):
        for values in [self.values[1:], deepcopy(self.values)]:
            values[0]["value"] = None
            self.assertEqual(self.run_rule(values)["status"], "insufficient_weather_data")
            self.assertIsNone(self.run_rule(values)["closures"])

    def test_unknown_issuance_blocks_historical_decision(self):
        result = self.run_rule(
            self.values, mode="as_of_decision", decision_at="2025-07-01T00:00:00+00:00"
        )
        self.assertEqual(result["status"], "insufficient_weather_data")

    def test_merge_and_correction_rejection(self):
        values = deepcopy(self.values)
        for x in values[:2]:
            x["value"] = 8
        self.assertEqual(self.run_rule(values)["closures"], [[0, 2]])
        values[0]["correction_applied"] = True
        with self.assertRaises(InputError):
            self.run_rule(values)

    def test_unconfirmed_mapping_and_duplicates(self):
        with self.assertRaises(InputError):
            mapped_points({"segments": {}}, ["unknown"])
        with self.assertRaises(InputError):
            self.run_rule(self.values + [self.values[0]])
