import copy
import json
import unittest
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.pilot_economics import illustrative_payback, order_value, pilot_budget

ROOT = Path(__file__).resolve().parents[1]


class PilotEconomicsTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((ROOT / "examples/ushanova/pilot_budget.json").read_text())

    def test_volunteer_work_is_valued_despite_no_cash_payments(self):
        budget = pilot_budget(self.spec, mode="student")
        self.assertEqual(budget["cash_total_rub"], 0)
        self.assertEqual(budget["imputed_volunteer_setup_rub"], 90000)
        self.assertEqual(budget["valued_resources_total_rub"], 162000)
        self.assertTrue(budget["unpriced_resources"])
        result = illustrative_payback(budget, 78000, cases=1)
        self.assertIsNone(result["cash_roi_percent"])
        self.assertEqual(result["net_resource_effect_rub"], -84000)

    def test_paid_year_accounts_for_reserve_and_maintenance_once(self):
        budget = pilot_budget(self.spec, mode="paid")
        self.assertEqual(budget["cash_setup_rub"], 108000)
        self.assertEqual(budget["cash_monthly_rub"], 6000)
        self.assertEqual(budget["cash_total_rub"], 180000)
        self.assertEqual(budget["imputed_volunteer_setup_rub"], 0)
        self.assertEqual(
            illustrative_payback(budget, 78000, cases=3)["net_resource_effect_rub"], 54000
        )

    def test_negative_and_missing_benefits_are_preserved(self):
        budget = pilot_budget(self.spec, mode="paid")
        loss = illustrative_payback(budget, -28000, cases=3)
        self.assertEqual(loss["net_resource_effect_rub"], -264000)
        self.assertIsNone(loss["actual_savings_rub"])
        self.assertEqual(illustrative_payback(budget, None, cases=3)["status"], "missing_benefit")

    def test_additional_cash_is_not_hidden_in_student_mode(self):
        self.spec.update(incremental_setup_cash_rub=1000, incremental_monthly_cash_rub=100)
        budget = pilot_budget(self.spec, mode="student", operating_months=2)
        self.assertEqual(budget["cash_total_rub"], 1400)
        self.assertEqual(budget["valued_resources_total_rub"], 103400)

    def test_order_value_exact_decimal_and_missing_not_zero(self):
        self.assertEqual(order_value(900, 7156.52)["order_value_rub"], 6440868)
        self.assertEqual(order_value(8850, 12605.04)["order_value_rub"], 111554604)
        self.assertEqual(order_value(3, 0.1)["order_value_rub"], 0.3)
        self.assertIsNone(order_value(900, None)["order_value_rub"])
        self.assertEqual(order_value(900, 0)["order_value_rub"], 0)

    def test_invalid_inputs_and_no_mutation(self):
        before = copy.deepcopy(self.spec)
        pilot_budget(self.spec, mode="student")
        self.assertEqual(before, self.spec)
        for value in [-1, True, float("nan"), float("inf")]:
            with self.subTest(value=value), self.assertRaises(InputError):
                pilot_budget(self.spec, mode="paid", operating_months=value)
            with self.subTest(value=value), self.assertRaises(InputError):
                order_value(value, None)
        with self.assertRaises(InputError):
            pilot_budget(self.spec, mode="unknown")
        with self.assertRaises(InputError):
            illustrative_payback(pilot_budget(self.spec, mode="student"), 100, cases=True)


if __name__ == "__main__":
    unittest.main()
