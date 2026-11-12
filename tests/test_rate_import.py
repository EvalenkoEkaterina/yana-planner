"""The economist's CSV feeds the same validated costing and optimizer inputs."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.economics import compare_cost, cost
from yana_planner.planning import compare
from yana_planner.rate_import import load_vessel_rates

ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "vessel_id",
    "separate_trip_operations_rub",
    "active_vessel_hour_rub",
    "waiting_vessel_hour_rub",
    "source",
    "status",
]


class RateImportTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "examples/r03.json").read_text())
        self.terms = json.loads((ROOT / "examples/ushanova/terms.json").read_text())
        with (ROOT / "examples/ushanova/vessel_rates.csv").open() as stream:
            self.rows = list(csv.DictReader(stream))
        self.saved = json.loads((ROOT / "demos/static/results.json").read_text())

    def load(self, rows=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rates.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(self.rows if rows is None else rows)
            return load_vessel_rates(self.cfg, self.terms, path)

    def test_lizas_rates_match_independent_saved_plan_totals(self):
        before = copy.deepcopy((self.cfg, self.terms))
        terms = self.load()
        report = compare_cost(
            self.cfg, self.saved["baseline"]["trips"], self.saved["planner"]["trips"], 328, terms
        )
        self.assertEqual(report["baseline"]["total_rub"], 873000)
        self.assertEqual(report["alternative"]["total_rub"], 795000)
        self.assertEqual(report["scenario_difference_rub"], 78000)
        self.assertIsNone(report["actual_savings_rub"])
        self.assertEqual(terms["vessel_rates"]["SIM-A"]["rate_basis"], "scenario")
        self.assertNotIn("active_vessel_hour_rub", terms)
        self.assertEqual((self.cfg, self.terms), before)

    def test_unknown_rate_stays_null_and_explicit_zero_is_valid(self):
        self.rows[0]["active_vessel_hour_rub"] = ""
        terms = self.load()
        self.assertIsNone(terms["vessel_rates"]["SIM-A"]["active_vessel_hour_rub"])
        report = cost(self.cfg, self.saved["planner"]["trips"], 328, terms)
        self.assertEqual(report["status"], "missing_cost_inputs")
        self.assertIsNone(report["total_rub"])
        self.rows[0]["active_vessel_hour_rub"] = "0"
        self.assertIsNotNone(
            cost(self.cfg, self.saved["planner"]["trips"], 328, self.load())["total_rub"]
        )

    def test_duplicates_missing_or_unknown_vessels_are_rejected(self):
        for rows in [
            self.rows[:1],
            self.rows + [self.rows[0]],
            [dict(self.rows[0], vessel_id="TYPO"), self.rows[1]],
        ]:
            with self.subTest(rows=rows), self.assertRaises(InputError):
                self.load(rows)

    def test_bad_money_and_status_are_rejected(self):
        for value in ["-1", "NaN", "Infinity", "0.001", "1e999999999", "unknown"]:
            rows = copy.deepcopy(self.rows)
            rows[0]["active_vessel_hour_rub"] = value
            with self.subTest(value=value), self.assertRaises(InputError):
                self.load(rows)
        rows = copy.deepcopy(self.rows)
        rows[0]["status"] = "verified_by_application"
        with self.assertRaises(InputError):
            self.load(rows)

    def test_imported_rates_reach_cost_objective(self):
        cfg = json.loads((ROOT / "examples/economic-choice.json").read_text())
        self.cfg = cfg
        self.rows[0].update(
            vessel_id="A",
            separate_trip_operations_rub="1000",
            active_vessel_hour_rub="0",
            waiting_vessel_hour_rub="0",
        )
        self.rows[1].update(
            vessel_id="B",
            separate_trip_operations_rub="100",
            active_vessel_hour_rub="0",
            waiting_vessel_hour_rub="0",
        )
        self.terms["horizon_h"] = 20
        result = compare(cfg, 13, strategy="cost", cost_terms=self.load(), time_limit=2)
        self.assertEqual(result["planner"]["accepted_t"], 20)
        self.assertEqual(result["planner"]["trip_count"], 2)
        self.assertEqual(result["costs"]["planner"]["total_rub"], 200)

    def test_header_currency_and_invalid_file_are_rejected(self):
        self.terms["currency"] = "USD"
        with self.assertRaises(InputError):
            self.load()
        self.terms.pop("currency")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text("vessel_id,wrong\nSIM-A,1\n")
            with self.assertRaises(InputError):
                load_vessel_rates(self.cfg, self.terms, path)
            with self.assertRaises(InputError):
                load_vessel_rates(self.cfg, self.terms, Path(directory) / "missing.csv")


if __name__ == "__main__":
    unittest.main()
