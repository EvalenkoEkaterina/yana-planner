import json
import unittest
from pathlib import Path

from yana_planner.online import apply_vessel_unavailability, replan_unavailability
from yana_planner.planning import compare
from yana_planner.validation import check_schedule

ROOT = Path(__file__).resolve().parents[1]


class OnlineAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "examples/r03.json").read_text())
        self.event = json.loads((ROOT / "examples/vessel_unavailability.json").read_text())
        self.deadline = 328

    def test_event_is_separate_from_original_availability(self):
        changed = apply_vessel_unavailability(self.cfg, self.event)
        a = next(v for v in changed["vessels"] if v["id"] == "SIM-A")
        original = next(v for v in self.cfg["vessels"] if v["id"] == "SIM-A")
        self.assertEqual(a["available_h"], original["available_h"])
        self.assertEqual(a["unavailable_intervals"], [[120, 200]])
        self.assertNotIn("unavailable_intervals", original)

    def test_new_candidates_avoid_full_turnaround(self):
        initial = compare(self.cfg, self.deadline, time_limit=10)
        plan = initial["baseline"]["trips"]
        result = replan_unavailability(
            self.cfg, plan, self.event, self.deadline, method="ready", time_limit=10
        )
        self.assertEqual(result["result"]["status"], "valid_simulation")
        for trip in result["result"]["trips"]:
            if trip["vessel_id"] == "SIM-A":
                self.assertFalse(max(trip["start_h"], 120) < min(trip["ready_h"], 200))

    def test_checker_rejects_one_hour_intersection(self):
        changed = apply_vessel_unavailability(self.cfg, self.event)
        verdict = check_schedule(
            changed, [dict(vessel_id="SIM-A", start_h=15, quantity_t=400)], self.deadline
        )
        # This turn ends at 121 h: exactly one hour overlaps the outage.
        self.assertEqual(verdict["status"], "invalid")
        self.assertTrue(any("vessel unavailable" in e for e in verdict["errors"]))

    def test_boundary_end_at_start_is_allowed(self):
        changed = apply_vessel_unavailability(self.cfg, self.event)
        # Construct a temporary vessel with a 56h total turnaround.
        v = next(v for v in changed["vessels"] if v["id"] == "SIM-A")
        old = [v[k] for k in ["load_h", "up_h", "unload_h", "return_h", "prep_h"]]
        v.update(load_h=8, up_h=28, unload_h=4, return_h=16, prep_h=8)
        plan = [dict(vessel_id="SIM-A", start_h=56, quantity_t=400)]
        # Return/prep ends at 120 exactly; checker should accept this temporal boundary.
        verdict = check_schedule(changed, plan, self.deadline)
        self.assertFalse(any("vessel unavailable" in e for e in verdict["errors"]))

    def test_loading_and_receiving_overlap_are_rejected(self):
        c = json.loads((ROOT / "examples/control.json").read_text())
        c["vessels"][1]["available_h"] = 0
        plan = [
            dict(vessel_id="SIM-A", start_h=0, quantity_t=400),
            dict(vessel_id="SIM-B", start_h=1, quantity_t=400),
        ]
        verdict = check_schedule(c, plan, 246)
        self.assertIn("loading berth overlap", verdict["errors"])


    def test_boundary_start_after_outage_is_allowed(self):
        changed = apply_vessel_unavailability(self.cfg, self.event)
        verdict = check_schedule(
            changed, [dict(vessel_id="SIM-A", start_h=200, quantity_t=400)], self.deadline
        )
        self.assertFalse(any("vessel unavailable" in e for e in verdict["errors"]))

    def test_receiving_berth_overlap_regression(self):
        c = json.loads((ROOT / "examples/control.json").read_text())
        c["vessels"][1].update(available_h=0, up_h=28)
        plan = [
            dict(vessel_id="SIM-A", start_h=0, quantity_t=400),
            dict(vessel_id="SIM-B", start_h=8, quantity_t=400),
        ]
        verdict = check_schedule(c, plan, 246)
        self.assertIn("receiving berth overlap", verdict["errors"])

    def test_started_voyage_conflict_requires_intervention(self):
        result = replan_unavailability(
            self.cfg,
            [dict(vessel_id="SIM-A", start_h=48, quantity_t=400)],
            self.event,
            self.deadline,
            method="ready",
        )
        self.assertEqual(result["result"]["status"], "requires_intervention")


if __name__ == "__main__":
    unittest.main()
