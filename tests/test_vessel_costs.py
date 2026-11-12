"""Hand-calculated economic checks, without optimizing the test schedules."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from yana_planner.cli import main
from yana_planner.domain import InputError
from yana_planner.economics import RATE_FIELDS, compare_cost, cost

ROOT = Path(__file__).resolve().parents[1]


class VesselCostTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "examples/urgent-stock.json").read_text())
        self.cfg.update(withdrawals=[], return_cutoff_h=20)
        self.cfg["vessels"] = [
            dict(
                id="A",
                available_h=0,
                capacity_t=10,
                load_h=1,
                up_h=2,
                unload_h=1,
                return_h=2,
                prep_h=1,
            ),
            dict(
                id="B",
                available_h=2,
                capacity_t=10,
                load_h=1,
                up_h=1,
                unload_h=1,
                return_h=1,
                prep_h=1,
            ),
        ]
        self.plan = [
            dict(vessel_id="A", start_h=0, quantity_t=10),
            dict(vessel_id="B", start_h=2, quantity_t=10),
        ]
        self.terms = dict(
            billing_mode="all_available_time",
            horizon_h=20,
            currency="RUB",
            vessel_rates={
                "A": dict(
                    separate_trip_operations_rub=100,
                    active_vessel_hour_rub=10,
                    waiting_vessel_hour_rub=1,
                    rate_basis="scenario",
                    source="Hand-calculated unit test, not actual rates",
                ),
                "B": dict(
                    separate_trip_operations_rub=200,
                    active_vessel_hour_rub=20,
                    waiting_vessel_hour_rub=2,
                    rate_basis="scenario",
                ),
            },
        )

    def test_different_rates_match_manual_costs(self):
        report = cost(self.cfg, self.plan, 10, self.terms)
        # A: 1*100 + 7*10 + 13*1 = 183.
        # B: 1*200 + 5*20 + 13*2 = 326.
        self.assertEqual(report["total_rub"], 509)
        self.assertEqual(report["active_vessel_h"], 12)
        self.assertEqual(report["paid_waiting_h"], 26)
        self.assertEqual(
            report["components"],
            dict(
                separate_trip_operations_rub=300,
                active_time_rub=170,
                waiting_time_rub=39,
            ),
        )
        a, b = report["per_vessel"]
        self.assertEqual((a["vessel_id"], a["total_rub"]), ("A", 183))
        self.assertEqual((b["vessel_id"], b["total_rub"]), ("B", 326))
        self.assertEqual(b["available_vessel_h"], 18)
        self.assertEqual(report["rates_format"], "per_vessel")
        self.assertIsNone(report["actual_savings_rub"])
        json.dumps(report, allow_nan=False)

    def test_unused_vessel_still_has_paid_waiting(self):
        c = dict(self.cfg["vessels"][0], id="C", available_h=5)
        self.cfg["vessels"].append(c)
        self.terms["vessel_rates"]["C"] = dict(
            separate_trip_operations_rub=999,
            active_vessel_hour_rub=999,
            waiting_vessel_hour_rub=3,
        )
        report = cost(self.cfg, self.plan, 10, self.terms)
        self.assertEqual(report["total_rub"], 554)
        unused = report["per_vessel"][2]
        self.assertEqual(unused["trip_count"], 0)
        self.assertEqual(unused["active_vessel_h"], 0)
        self.assertEqual(unused["paid_waiting_h"], 15)
        self.assertEqual(unused["total_rub"], 45)

    def test_vessel_available_after_horizon_has_no_paid_time(self):
        self.cfg["vessels"].append(dict(self.cfg["vessels"][0], id="C", available_h=30))
        self.terms["vessel_rates"]["C"] = self.terms["vessel_rates"]["A"]
        report = cost(self.cfg, self.plan, 10, self.terms)
        self.assertEqual(report["total_rub"], 509)
        self.assertEqual(report["per_vessel"][2]["total_rub"], 0)

    def test_empty_plan_pays_only_waiting(self):
        report = cost(self.cfg, [], 10, self.terms)
        self.assertEqual(report["total_rub"], 56)  # 20*1 + 18*2.
        self.assertEqual(report["accepted_t"], 0)
        self.assertEqual(report["active_vessel_h"], 0)

    def test_equal_volume_comparison_includes_unused_fleet(self):
        comparison = compare_cost(self.cfg, self.plan[:1], self.plan[1:], 10, self.terms)
        self.assertEqual(comparison["baseline"]["total_rub"], 219)
        self.assertEqual(comparison["alternative"]["total_rub"], 346)
        self.assertEqual(comparison["scenario_difference_rub"], -127)
        self.assertIsNone(comparison["actual_savings_rub"])

    def test_different_volume_cannot_be_reported_as_savings(self):
        with self.assertRaisesRegex(InputError, "одинаковым"):
            compare_cost(self.cfg, self.plan, self.plan[:1], 10, self.terms)

    def test_invalid_schedule_is_not_priced(self):
        invalid = [dict(vessel_id="B", start_h=0, quantity_t=10)]
        with self.assertRaisesRegex(InputError, "допустимое"):
            cost(self.cfg, invalid, 10, self.terms)

    def test_missing_unused_vessel_rates_do_not_become_zero(self):
        del self.terms["vessel_rates"]["B"]
        report = cost(self.cfg, self.plan[:1], 10, self.terms)
        self.assertEqual(report["status"], "missing_cost_inputs")
        self.assertIsNone(report["total_rub"])
        self.assertEqual(set(report["missing"]), {f"vessel_rates.B.{k}" for k in RATE_FIELDS})

    def test_missing_single_rate_is_named(self):
        self.terms["vessel_rates"]["B"]["waiting_vessel_hour_rub"] = None
        report = cost(self.cfg, self.plan, 10, self.terms)
        self.assertEqual(report["missing"], ["vessel_rates.B.waiting_vessel_hour_rub"])

    def test_explicit_zero_rates_are_valid(self):
        for rates in self.terms["vessel_rates"].values():
            rates.update(dict.fromkeys(RATE_FIELDS, 0))
        self.assertEqual(cost(self.cfg, self.plan, 10, self.terms)["total_rub"], 0)

    def test_bad_rates_are_rejected(self):
        for value in [-1, True, "12.00", float("nan"), float("inf"), 0.001]:
            with self.subTest(value=value):
                terms = copy.deepcopy(self.terms)
                terms["vessel_rates"]["A"]["active_vessel_hour_rub"] = value
                with self.assertRaises(InputError):
                    cost(self.cfg, self.plan, 10, terms)

    def test_kopecks_do_not_accumulate_float_error(self):
        for rates in self.terms["vessel_rates"].values():
            rates.update(dict.fromkeys(RATE_FIELDS, 0.1))
        report = cost(self.cfg, self.plan, 10, self.terms)
        self.assertEqual(report["total_rub"], 4)  # Two trips plus 38 paid hours.
        self.assertEqual(report["per_vessel"][0]["total_rub"], 2.1)
        self.assertEqual(report["per_vessel"][1]["total_rub"], 1.9)
        comparison = compare_cost(self.cfg, self.plan[:1], self.plan[1:], 10, self.terms)
        self.assertEqual(comparison["scenario_difference_rub"], 0)

    def test_fractional_waiting_is_rounded_per_vessel_component(self):
        self.terms["horizon_h"] = 20.5
        for rates in self.terms["vessel_rates"].values():
            rates.update(dict.fromkeys(RATE_FIELDS, 0))
            rates["waiting_vessel_hour_rub"] = 0.01
        # 13.5 hours * 1 kopeck rounds to 14 kopecks for each vessel.
        self.assertEqual(cost(self.cfg, self.plan, 10, self.terms)["total_rub"], 0.28)

    def test_horizon_covers_preparation_not_only_return(self):
        self.terms["horizon_h"] = 6
        with self.assertRaisesRegex(InputError, "полный оборот"):
            cost(self.cfg, self.plan[:1], 10, self.terms)

    def test_unknown_vessel_and_mixed_formats_are_rejected(self):
        terms = copy.deepcopy(self.terms)
        terms["vessel_rates"]["TYPO"] = terms["vessel_rates"].pop("B")
        with self.assertRaisesRegex(InputError, "неизвестное"):
            cost(self.cfg, self.plan, 10, terms)
        terms = dict(self.terms, active_vessel_hour_rub=10)
        with self.assertRaisesRegex(InputError, "смешивать"):
            cost(self.cfg, self.plan, 10, terms)

    def test_invalid_terms_shapes_are_rejected(self):
        for terms in [
            None,
            [],
            dict(self.terms, vessel_rates=[]),
            dict(self.terms, vessel_rates={"A": None}),
            dict(self.terms, currency="USD"),
            dict(self.terms, horizon_h=True),
            dict(self.terms, billing_mode="per_trip"),
        ]:
            with self.subTest(terms=terms), self.assertRaises(InputError):
                cost(self.cfg, self.plan, 10, terms)

    def test_documented_rate_requires_source_but_does_not_prove_actual_savings(self):
        rates = self.terms["vessel_rates"]["B"]
        rates["rate_basis"] = "documented"
        report = cost(self.cfg, self.plan, 10, self.terms)
        self.assertEqual(report["missing"], ["vessel_rates.B.source"])
        rates["source"] = "Example document supplied by user, not verified by application"
        report = cost(self.cfg, self.plan, 10, self.terms)
        self.assertEqual(report["per_vessel"][1]["source"], rates["source"])
        self.assertEqual(report["status"], "scenario_cost")
        self.assertIsNone(report["actual_savings_rub"])

    def test_uniform_rates_remain_compatible_with_per_vessel_form(self):
        uniform = dict(
            billing_mode="all_available_time",
            horizon_h=20,
            separate_trip_operations_rub=100,
            active_vessel_hour_rub=10,
            waiting_vessel_hour_rub=1,
        )
        terms = copy.deepcopy(self.terms)
        for rates in terms["vessel_rates"].values():
            for field in RATE_FIELDS:
                rates[field] = uniform[field]
        old = cost(self.cfg, self.plan, 10, uniform)
        new = cost(self.cfg, self.plan, 10, terms)
        self.assertEqual(old["total_rub"], 346)  # 200 + 120 + 26.
        self.assertEqual(old["total_rub"], new["total_rub"])
        self.assertEqual(old["components"], new["components"])
        self.assertEqual(old["rates_format"], "uniform")

    def test_inputs_are_not_mutated(self):
        before = copy.deepcopy((self.cfg, self.plan, self.terms))
        cost(self.cfg, self.plan, 10, self.terms)
        self.assertEqual((self.cfg, self.plan, self.terms), before)

    def test_existing_cli_exports_per_vessel_costs_for_saved_r03(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cost.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "cost",
                        str(ROOT / "examples/r03.json"),
                        str(ROOT / "demos/static/results.json"),
                        "--deadline",
                        "328",
                        "--terms",
                        str(ROOT / "examples/costs.per-vessel.demo.json"),
                        "--out",
                        str(output),
                    ]
                )
            self.assertEqual(code, 0)
            report = json.loads(output.read_text())
            self.assertEqual(report["baseline"]["total_rub"], 84480)
            self.assertEqual(report["alternative"]["total_rub"], 73940)
            self.assertEqual(report["scenario_difference_rub"], 10540)
            self.assertEqual(len(report["alternative"]["per_vessel"]), 2)
            self.assertIsNone(report["actual_savings_rub"])


if __name__ == "__main__":
    unittest.main()
