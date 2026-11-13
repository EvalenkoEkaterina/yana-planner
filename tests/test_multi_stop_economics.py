import json
import unittest
from copy import deepcopy

from yana_planner.domain import InputError
from yana_planner.multi_stop import plan_multi_stop
from yana_planner.multi_stop_economics import price_voyage
from yana_planner.service import ROOT


def fixtures():
    cfg = json.loads((ROOT / "examples/multi_stop/four-recipients.json").read_text())
    terms = json.loads((ROOT / "examples/multi_stop/four-recipients.costs.json").read_text())
    return cfg, terms


class MultiStopEconomicsTests(unittest.TestCase):
    def test_unknown_stop_fees_do_not_become_zero(self):
        cfg, terms = fixtures()
        result = plan_multi_stop(cfg, cost_terms=terms)
        c = result["costs"]
        self.assertEqual(c["status"], "missing_cost_inputs")
        self.assertIsNone(c["total_rub"])
        self.assertEqual(c["known_subtotal_rub"], 181500)
        self.assertEqual(len(c["missing"]), 4)
        self.assertEqual((c["trip_count"], c["visit_count"]), (1, 4))
        self.assertEqual(c["active_vessel_h"] + c["paid_waiting_h"], 180)
        self.assertEqual(c["active_breakdown"]["preparation_h"], 4)

    def test_explicit_stop_fees_and_non_overlapping_hours(self):
        cfg, terms = fixtures()
        terms["stop_fees_rub"] = {s["id"]: 1000 for s in cfg["stops"]}
        before = deepcopy(terms)
        plain = plan_multi_stop(cfg)
        priced = plan_multi_stop(cfg, cost_terms=terms)
        self.assertEqual(plain["plan"], priced["plan"])
        c = priced["costs"]
        self.assertEqual(c["status"], "scenario_cost")
        self.assertEqual(c["total_rub"], 185500)
        self.assertEqual((c["active_vessel_h"], c["paid_waiting_h"]), (133, 47))
        self.assertEqual(sum(r["amount_rub"] for r in c["components"]), c["total_rub"])
        self.assertEqual(sum(r["hours"] for r in c["waiting_breakdown"]), 47)
        self.assertEqual(len([r for r in c["components"] if r["kind"] == "stop_fee"]), 4)
        self.assertEqual(terms, before)
        self.assertFalse(c["optimization_includes_cost"])
        self.assertIsNone(c["actual_savings_rub"])

    def test_explicit_zero_fee_is_known_and_fractional_horizon_is_supported(self):
        cfg, terms = fixtures()
        terms["stop_fees_rub"] = {s["id"]: 0 for s in cfg["stops"]}
        terms["horizon_h"] = 180.1
        c = plan_multi_stop(cfg, cost_terms=terms)["costs"]
        self.assertEqual(c["missing"], [])
        self.assertEqual(c["total_rub"], 181550)
        self.assertEqual(c["paid_waiting_h"], 47.1)

    def test_wrong_ship_duplicate_scope_missing_rate_and_short_horizon(self):
        cfg, terms = fixtures()
        starts = plan_multi_stop(cfg)["plan"]["starts"]
        for key, value in [
            ("vessel_id", "OTHER"),
            ("stop_fees_excluded_from_other_rates", False),
            ("horizon_h", 144),
            ("horizon_h", float("inf")),
        ]:
            bad = deepcopy(terms)
            bad[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(InputError):
                price_voyage(cfg, starts, bad)
        bad = deepcopy(terms)
        bad["stop_fees_rub"]["unknown_stop"] = 0
        with self.assertRaises(InputError):
            price_voyage(cfg, starts, bad)
        bad = deepcopy(terms)
        bad["rates"]["active_vessel_hour_rub"] = None
        self.assertIn("rates.active_vessel_hour_rub", price_voyage(cfg, starts, bad)["missing"])
        bad = deepcopy(terms)
        bad["rates"]["active_vessel_hour_rub"] = True
        with self.assertRaises(InputError):
            price_voyage(cfg, starts, bad)
        with self.assertRaises(InputError):
            price_voyage(cfg, dict(starts, depart2_h=0), terms)

    def test_no_plan_has_no_cost_and_json_terms_are_supported(self):
        cfg, terms = fixtures()
        cfg["cost_terms"] = deepcopy(terms)
        self.assertEqual(plan_multi_stop(cfg)["costs"]["known_subtotal_rub"], 181500)
        cfg["vessel"]["capacity_t"] = 599
        result = plan_multi_stop(cfg)
        self.assertFalse(result["goal_met"])
        self.assertEqual(result["costs"]["status"], "no_valid_plan")
        self.assertIsNone(result["costs"]["total_rub"])
