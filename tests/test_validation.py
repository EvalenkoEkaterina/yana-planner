"""Boundary and error tests; expectations fixed independently of optimizer."""

import json
import unittest
from pathlib import Path

from yana_planner.validation import check_schedule as check


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.c = json.loads(
            (Path(__file__).resolve().parents[1] / "examples/control.json").read_text()
        )
        self.p = [dict(vessel_id="SIM-A", start_h=0, quantity_t=400)]

    def valid(self, p=None, d=246):
        return check(self.c, self.p if p is None else p, d)["status"] == "valid_simulation"

    def test_receipt52(self):
        self.assertEqual(check(self.c, self.p, 52)["accepted_t"], 400)

    def test_one_hour_early(self):
        self.assertFalse(self.valid(d=51))

    def test_no_source(self):
        self.c["arrivals"][0]["h"] = 1
        self.assertFalse(self.valid())

    def test_supply_exact(self):
        self.assertTrue(self.valid())

    def test_payload401(self):
        self.p[0]["quantity_t"] = 401
        self.assertFalse(self.valid())

    def test_vessel_overlap(self):
        self.assertFalse(self.valid(self.p + [dict(vessel_id="SIM-A", start_h=81, quantity_t=400)]))

    def test_vessel_exact_ready(self):
        self.assertTrue(self.valid(self.p + [dict(vessel_id="SIM-A", start_h=82, quantity_t=400)]))

    def test_loading_berth(self):
        self.c["vessels"][1]["available_h"] = 0
        self.assertFalse(self.valid(self.p + [dict(vessel_id="SIM-B", start_h=1, quantity_t=400)]))

    def test_receiving_berth(self):
        self.c["vessels"][1].update(available_h=0, up_h=28)
        self.assertFalse(self.valid(self.p + [dict(vessel_id="SIM-B", start_h=8, quantity_t=400)]))

    def test_storage(self):
        self.c["destination_capacity_t"] = 399
        self.assertFalse(self.valid())

    def test_storage_exact(self):
        self.c["destination_capacity_t"] = 400
        self.assertTrue(self.valid())

    def test_empty(self):
        self.assertEqual(check(self.c, [], 164)["accepted_t"], 0)

    def test_closure_loaded(self):
        self.c["closures"] = [[43, 45]]
        self.assertFalse(self.valid())

    def test_closure_return(self):
        self.c["closures"] = [[75, 77]]
        self.assertFalse(self.valid())

    def test_closure_boundary(self):
        self.c["closures"] = [[44, 52]]
        self.assertTrue(self.valid())

    def test_return_cutoff(self):
        self.c["return_cutoff_h"] = 75
        self.assertFalse(self.valid())

    def test_return_exact(self):
        self.c["return_cutoff_h"] = 76
        self.assertTrue(self.valid())

    def test_unknown_gate(self):
        self.c["navigation_assumed"] = None
        self.assertIsNone(check(self.c, self.p, 246)["accepted_t"])

    def test_base_blocked(self):
        self.c["mode"] = "BASE"
        self.assertEqual(check(self.c, self.p, 246)["status"], "requires_confirmation")

    def test_missing_capacity(self):
        self.c["destination_capacity_t"] = None
        self.assertFalse(self.valid())

    def test_negative_quantity(self):
        self.p[0]["quantity_t"] = -1
        self.assertFalse(self.valid())

    def test_target(self):
        self.c["target_t"] = 399
        self.assertFalse(self.valid())

    def test_cap_during_leg(self):
        self.c["payload_calendar"].append(dict(from_h=43, capacity_t=200))
        self.assertFalse(self.valid())

    def test_cap_after_leg(self):
        self.c["payload_calendar"].append(dict(from_h=44, capacity_t=200))
        self.assertTrue(self.valid())

    def test_unconfirmed_withdrawal(self):
        self.c["withdrawals"] = [dict(h=80, t=400, confirmed_in_scenario=False)]
        self.assertIsNone(check(self.c, self.p, 246)["accepted_t"])

    def test_confirmed_withdrawal_frees(self):
        self.c["destination_capacity_t"] = 400
        self.c["withdrawals"] = [dict(h=80, t=400, confirmed_in_scenario=True)]
        self.assertTrue(self.valid(self.p + [dict(vessel_id="SIM-A", start_h=82, quantity_t=400)]))

    def test_unload_does_not_free(self):
        self.c["destination_capacity_t"] = 400
        self.assertFalse(self.valid(self.p + [dict(vessel_id="SIM-A", start_h=82, quantity_t=400)]))

    def test_reserve_before_unload(self):
        self.assertEqual(
            next(
                e["free_t"] for e in check(self.c, self.p, 246)["ledger"] if e["event"] == "reserve"
            ),
            1250,
        )


if __name__ == "__main__":
    unittest.main()
