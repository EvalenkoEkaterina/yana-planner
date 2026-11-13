import importlib.util
import unittest
from copy import deepcopy
from tempfile import TemporaryDirectory

from yana_planner.cost_sensitivity import price_fixed_plan, variant_terms
from yana_planner.domain import InputError
from yana_planner.service import ROOT


class CostSensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "prepare_sensitivity_fixture", ROOT / "scripts/check_graph_weather.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.prepare = staticmethod(module.run)

    def test_final_pair_stays_equal_under_all_six_tariff_hypotheses(self):
        with TemporaryDirectory() as tmp:
            inputs, result, _ = self.prepare(tmp, example="consolidation")
        cfg, terms = inputs["scenario"], inputs["cost_terms"]
        before = deepcopy(terms)
        expected = dict(S0=255000, S1=425000, S2=290000, S3=290000, S4=305000, S5=255000)
        for variant, total in expected.items():
            for method, volume in [("baseline", 500), ("planner", 800)]:
                priced = price_fixed_plan(cfg, result[method]["trips"], 100, terms, variant)
                self.assertEqual(priced["total_rub"], total)
                self.assertEqual(priced["accepted_t"], volume)
                self.assertEqual(priced["rub_per_delivered_t"], total / volume)
                self.assertFalse(priced["reoptimized"])
        self.assertEqual(terms, before)

    def test_optional_engagement_removes_only_never_used_vessel_cost(self):
        with TemporaryDirectory() as tmp:
            inputs, result, _ = self.prepare(tmp)
        cfg, terms = inputs["scenario"], inputs["cost_terms"]
        full = price_fixed_plan(cfg, result["planner"]["trips"], 100, terms, "S0")
        optional = price_fixed_plan(cfg, result["planner"]["trips"], 100, terms, "S5")
        self.assertEqual(full["total_rub"], 174000)
        self.assertEqual(optional["total_rub"], 138000)
        self.assertEqual(optional["excluded_vessels"], ["SIM-B"])
        active = optional["per_vessel"][0]
        self.assertEqual(active["available_vessel_h"], 120)
        self.assertEqual(active["paid_waiting_h"], 14)
        self.assertEqual(active["total_rub"], 138000)
        self.assertIsNone(optional["actual_savings_rub"])

    def test_missing_rates_are_rejected_and_zero_volume_has_no_unit_cost(self):
        with TemporaryDirectory() as tmp:
            inputs, _, _ = self.prepare(tmp)
        cfg, terms = inputs["scenario"], inputs["cost_terms"]
        empty = price_fixed_plan(cfg, [], 100, terms, "S5")
        self.assertIsNone(empty["rub_per_delivered_t"])
        self.assertEqual(empty["total_rub"], 0)
        bad = deepcopy(terms)
        bad["vessel_rates"]["SIM-A"]["active_vessel_hour_rub"] = None
        with self.assertRaises(InputError):
            variant_terms(bad, "S1")
        with self.assertRaises(InputError):
            variant_terms(terms, "S6")
