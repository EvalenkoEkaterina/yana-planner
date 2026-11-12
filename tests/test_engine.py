import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yana_planner.domain import ConfirmationRequired, InputError, InterventionRequired, Scenario
from yana_planner.economics import compare_cost, cost
from yana_planner.excel_import import import_excel
from yana_planner.planning import compare, optimize, ready
from yana_planner.replanning import apply_arrival_delay, run_replanning
from yana_planner.validation import check_schedule

ROOT = Path(__file__).resolve().parents[1]


def tiny():
    c = json.loads((ROOT / "examples/control.json").read_text())
    c.update(
        id="TINY",
        target_t=20,
        destination_capacity_t=20,
        return_cutoff_h=30,
        arrivals=[dict(h=0, t=10), dict(h=8, t=10)],
        payload_calendar=[dict(from_h=0, capacity_t=10)],
    )
    c["vessels"] = [
        dict(
            id="A", available_h=0, capacity_t=10, load_h=1, up_h=2, unload_h=1, return_h=2, prep_h=1
        )
    ]
    return c


class ContractTests(unittest.TestCase):
    def test_duplicate_vessel_id(self):
        c = tiny()
        c["vessels"].append(copy.deepcopy(c["vessels"][0]))
        with self.assertRaises(InputError):
            Scenario.from_dict(c)

    def test_missing_value_does_not_become_zero(self):
        c = tiny()
        c["initial_source_t"] = None
        with self.assertRaises(ConfirmationRequired):
            Scenario.from_dict(c)

    def test_nan_rejected(self):
        c = tiny()
        c["target_t"] = float("nan")
        with self.assertRaises(InputError):
            Scenario.from_dict(c)

    def test_bool_not_tonnage(self):
        c = tiny()
        c["target_t"] = True
        with self.assertRaises(InputError):
            Scenario.from_dict(c)

    def test_fractional_grid_explicitly_rejected(self):
        c = tiny()
        c["vessels"][0]["up_h"] = 2.5
        with self.assertRaises(InputError):
            Scenario.from_dict(c)

    def test_calendar_gap_at_start_rejected(self):
        c = tiny()
        c["payload_calendar"][0]["from_h"] = 1
        with self.assertRaises(InputError):
            Scenario.from_dict(c)

    def test_duplicate_calendar_boundary_rejected(self):
        c = tiny()
        c["payload_calendar"].append(dict(from_h=0, capacity_t=5))
        with self.assertRaises(InputError):
            Scenario.from_dict(c)

    def test_reversed_closure_rejected(self):
        c = tiny()
        c["closures"] = [[8, 4]]
        with self.assertRaises(InputError):
            Scenario.from_dict(c)

    def test_copies_prevent_hidden_edits(self):
        c = tiny()
        s = Scenario.from_dict(c)
        h = s.fingerprint
        c["target_t"] = 100
        s.to_dict()["target_t"] = 100
        self.assertEqual(s.fingerprint, h)


class SolverTests(unittest.TestCase):
    def test_control_tiny_full_delivery(self):
        c = tiny()
        r = compare(c, 20, time_limit=1)
        self.assertEqual(r["planner"]["accepted_t"], 20)
        self.assertEqual(r["planner"]["trip_count"], 2)
        self.assertTrue(r["solver"][0]["optimal"])

    def test_confirmed_offtake_releases_capacity(self):
        c = tiny()
        c["destination_capacity_t"] = 10
        c["arrivals"] = [dict(h=0, t=20)]
        c["withdrawals"] = [dict(h=5, t=10, confirmed_in_scenario=True)]
        r = compare(c, 20, time_limit=1)
        self.assertEqual(r["planner"]["accepted_t"], 20)
        self.assertEqual(r["baseline"]["accepted_t"], 20)
        ledger = r["planner"]["ledger"]
        self.assertEqual(next(e["free_t"] for e in ledger if e["event"] == "withdrawal"), 10)

    def test_without_offtake_capacity_is_not_recycled(self):
        c = tiny()
        c["destination_capacity_t"] = 10
        r = compare(c, 20, time_limit=1)
        self.assertEqual(r["planner"]["accepted_t"], 10)

    def test_impossible_offtake_is_not_zero_delivery(self):
        c = tiny()
        c["withdrawals"] = [dict(h=0, t=10, confirmed_in_scenario=True)]
        r = compare(c, 20, time_limit=1)
        self.assertEqual(r["planner"]["status"], "infeasible")
        self.assertIsNone(r["planner"]["accepted_t"])

    def test_time_limit_keeps_baseline(self):
        c = tiny()
        b = ready(c, 20)
        with patch(
            "yana_planner.planning.milp",
            return_value=SimpleNamespace(status=1, message="time limit", x=None),
        ):
            p, logs = optimize(c, 20, b)
        self.assertEqual(p, b)
        self.assertTrue(all(not x["optimal"] for x in logs))

    def test_bad_incumbent_is_not_used(self):
        c = tiny()
        b = ready(c, 20)
        with patch(
            "yana_planner.planning.milp",
            return_value=SimpleNamespace(status=1, message="time limit", x=None),
        ):
            p, _ = optimize(c, 20, b, incumbent=[dict(vessel_id="A", start_h=0, quantity_t=20)])
        self.assertEqual(p, b)

    def test_committed_overload_requires_intervention(self):
        c = tiny()
        c["payload_calendar"][0]["capacity_t"] = 5
        with self.assertRaises(InterventionRequired):
            optimize(c, 20, fixed=[dict(vessel_id="A", start_h=0, quantity_t=10)], decision_h=2)

    def test_storage_greedy_maximal_smaller_last_party(self):
        c = tiny()
        c["destination_capacity_t"] = 15
        p = ready(c, 20)
        self.assertEqual(check_schedule(c, p, 20)["accepted_t"], 15)


class ReplanningTests(unittest.TestCase):
    def setUp(self):
        self.c = tiny()
        self.e = dict(type="arrival_delay", announced_at_h=2, arrival_index=1, new_arrival_h=15)

    def test_history_fixed_new_supply_respected(self):
        r = run_replanning(self.c, self.e, 20, time_limit=1)
        for key in ["revised_baseline", "revised_planner"]:
            revised = r[key]
            self.assertEqual(revised["result"]["status"], "valid_simulation")
            self.assertEqual(revised["result"]["accepted_t"], 20)
            for fixed in revised["committed"]:
                self.assertIn(
                    fixed,
                    [
                        {k: t[k] for k in ["vessel_id", "start_h", "quantity_t"]}
                        for t in revised["result"]["trips"]
                    ],
                )
            self.assertTrue(
                all(t["start_h"] >= 15 for t in revised["result"]["trips"] if t["start_h"] > 2)
            )
        self.assertEqual(self.c["arrivals"][1]["h"], 8)

    def test_initial_planner_has_no_revealed_future(self):
        seen = []

        def initial(cfg, *args, **kwargs):
            seen.append(copy.deepcopy(cfg["arrivals"]))
            return compare(cfg, *args, **kwargs)

        with patch("yana_planner.replanning.compare", side_effect=initial):
            run_replanning(self.c, self.e, 20, time_limit=1)
        self.assertEqual(seen[0][1]["h"], 8)

    def test_past_supply_cannot_be_changed(self):
        self.e["arrival_index"] = 0
        with self.assertRaises(InputError):
            apply_arrival_delay(self.c, self.e)

    def test_delay_cannot_be_a_past_or_earlier_date(self):
        self.e["new_arrival_h"] = 1
        with self.assertRaises(InputError):
            apply_arrival_delay(self.c, self.e)


class EconomyTests(unittest.TestCase):
    def setUp(self):
        self.c = tiny()
        self.plan = ready(self.c, 20)
        self.terms = dict(
            billing_mode="all_available_time",
            horizon_h=30,
            separate_trip_operations_rub=0,
            active_vessel_hour_rub=0,
            waiting_vessel_hour_rub=0,
        )

    def test_missing_is_not_zero(self):
        self.terms["active_vessel_hour_rub"] = None
        self.assertIsNone(cost(self.c, self.plan, 20, self.terms)["total_rub"])

    def test_explicit_zero_is_valid(self):
        self.assertEqual(cost(self.c, self.plan, 20, self.terms)["total_rub"], 0)

    def test_cost_components_and_common_horizon(self):
        self.terms.update(
            separate_trip_operations_rub=1000,
            active_vessel_hour_rub=100,
            waiting_vessel_hour_rub=10,
        )
        r = cost(self.c, self.plan, 20, self.terms)
        self.assertEqual(r["active_vessel_h"], 14)
        self.assertEqual(r["paid_waiting_h"], 16)
        self.assertEqual(r["total_rub"], 3560)

    def test_unequal_volume_cannot_look_like_saving(self):
        with self.assertRaises(InputError):
            compare_cost(self.c, self.plan, self.plan[:1], 20, self.terms)

    def test_negative_rates_rejected(self):
        self.terms["waiting_vessel_hour_rub"] = -1
        with self.assertRaises(InputError):
            cost(self.c, self.plan, 20, self.terms)


class ExcelTests(unittest.TestCase):
    def test_r03_roundtrip_inputs(self):
        c = import_excel(
            str(ROOT / "tests/fixtures/team-r03.xlsx"), profile="team-r03", sim_only=True
        )
        self.assertEqual(c["initial_source_t"], 0)
        self.assertEqual(c["arrivals"][1]["h"], 120)
        self.assertEqual(c["vessels"][0]["up_h"], 48)
        self.assertEqual(c["delivery_deadline_h"], 328)
        self.assertIsNone(c["timezone"])

    def test_no_implicit_sim_permission(self):
        with self.assertRaises(ConfirmationRequired):
            import_excel(
                str(ROOT / "tests/fixtures/team-r03.xlsx"), profile="team-r03", sim_only=False
            )


if __name__ == "__main__":
    unittest.main()


class UrgentStockExampleTests(unittest.TestCase):
    def test_milp_coordinates_required_stock_when_ready_has_no_valid_plan(self):
        cfg = json.loads((ROOT / "examples/urgent-stock.json").read_text())
        result = compare(cfg, 8, time_limit=1, adaptive=True)
        self.assertEqual(result["baseline"]["status"], "invalid")
        self.assertIsNone(result["baseline"]["accepted_t"])
        self.assertEqual(result["planner"]["status"], "valid_simulation")
        self.assertEqual(result["planner"]["accepted_t"], 20)
        self.assertEqual(result["planner"]["last_receipt_h"], 5)
        self.assertTrue(all(s["optimal"] for s in result["solver"]))
        self.assertEqual(
            check_schedule(cfg, result["planner"]["trips"], 8)["status"], "valid_simulation"
        )


class LoadingOrderExampleTests(unittest.TestCase):
    def test_joint_loading_order_delivers_full_target(self):
        cfg = json.loads((ROOT / "examples/loading-order.json").read_text())
        result = compare(cfg, 8, time_limit=1, adaptive=True)
        self.assertEqual(result["baseline"]["status"], "valid_simulation")
        self.assertEqual(result["baseline"]["accepted_t"], 10)
        self.assertEqual(result["planner"]["status"], "valid_simulation")
        self.assertEqual(result["planner"]["accepted_t"], 20)
        self.assertEqual(result["planner"]["last_receipt_h"], 8)
        self.assertEqual(result["planner"]["trips"][0]["vessel_id"], "SIM-B")
        self.assertTrue(all(s["optimal"] for s in result["solver"]))
