"""Compatibility checks against the integrator's existing planners, not a copied UI."""

import json
import unittest
from copy import deepcopy
from pathlib import Path

from yana_planner.domain import InputError, Scenario
from yana_planner.navigation import SECTION
from yana_planner.planning import compare
from yana_planner.replanning import run_replanning
from yana_planner.season_constraint import apply_season_constraint, attach_season_trip_checks

ROOT = Path(__file__).resolve().parents[1]


class SeasonHandoffTests(unittest.TestCase):
    def cfg(self, *, rate=False):
        cfg = json.loads((ROOT / "examples/r03.json").read_text())
        cfg.update(
            target_t=100,
            initial_source_t=100,
            destination_capacity_t=1000,
            return_cutoff_h=50,
            arrivals=[],
            withdrawals=[],
            closures=[],
            payload_calendar=[{"from_h": 0, "capacity_t": 100}],
        )
        vessel = dict(
            id="TEST",
            available_h=0,
            capacity_t=100,
            load_h=2,
            up_h=4,
            unload_h=2,
            return_h=4,
            prep_h=2,
        )
        if rate:
            vessel.pop("load_h")
            vessel.update(load_rate_tph=50, load_setup_h=0)
        cfg["vessels"] = [vessel]
        return Scenario.from_dict(cfg).to_dict()

    def spec(self, **changes):
        spec = dict(
            section=SECTION,
            year="2026",
            start="2026-09-29T09:00",
            reserve_h="3",
            mode="manual",
            manual_boundary="2026-09-30",
            event_type="navigation_aids_removed",
            csv_text="",
        )
        spec.update(changes)
        return spec

    def test_existing_ready_milp_and_variable_loading_fit_exact_boundary(self):
        for rate in (False, True):
            with self.subTest(rate=rate):
                cfg = self.cfg(rate=rate)
                identity = cfg["id"]
                deadline = apply_season_constraint(cfg, 30, self.spec())
                self.assertEqual(cfg["id"], identity)
                self.assertEqual(deadline, 12)
                result = compare(cfg, deadline, time_limit=0.2)
                attach_season_trip_checks(cfg, result)
                for method, key in (("READY", "baseline"), ("MILP", "planner")):
                    self.assertEqual(result[key]["accepted_t"], 100)
                    trip = cfg["season_assessment"]["trip_checks"][method][0]
                    self.assertTrue(trip["fits"])
                    self.assertEqual(trip["with_reserve_at"], "2026-09-30T00:00:00+10:00")
                    self.assertEqual(trip["latest_loading_start"], "2026-09-29T09:00:00+10:00")

    def test_late_zero_and_fractional_boundaries_are_conservative(self):
        for start, boundary, expected in (
            ("2026-09-29T10:00", "2026-09-30", 11),
            ("2026-10-01T00:00", "2026-09-30", 0),
            ("2026-09-29T09:00", "2026-09-30T00:30", 12),
        ):
            cfg = self.cfg()
            deadline = apply_season_constraint(
                cfg, 30, self.spec(start=start, manual_boundary=boundary)
            )
            self.assertEqual(cfg["return_cutoff_h"], expected)
            result = compare(cfg, deadline, time_limit=0.1)
            if expected < 12:
                for key in ("baseline", "planner"):
                    self.assertEqual(result[key]["accepted_t"], 0)

    def test_same_paid_horizon_and_cost_strategy_are_preserved(self):
        cfg = self.cfg()
        terms = {
            "billing_mode": "all_available_time",
            "horizon_h": 50,
            "separate_trip_operations_rub": 25,
            "active_vessel_hour_rub": 10,
            "waiting_vessel_hour_rub": 5,
        }
        before = deepcopy(terms)
        deadline = apply_season_constraint(cfg, 30, self.spec())
        result = compare(cfg, deadline, time_limit=0.2, strategy="cost", cost_terms=terms)
        attach_season_trip_checks(cfg, result)
        self.assertEqual(terms, before)
        self.assertEqual(result["planner"]["accepted_t"], 100)
        self.assertEqual(result["planning_strategy"], "cost")

    def test_history_filters_future_and_wrong_section_is_blocked(self):
        csv = f"year,date,section,event_type,source\n2025,2025-09-30,{SECTION},navigation_aids_removed,test source\n2026,2026-10-30,{SECTION},navigation_aids_removed,excluded\n"
        cfg = self.cfg()
        apply_season_constraint(cfg, 30, self.spec(mode="p20", csv_text=csv))
        self.assertEqual(cfg["season_assessment"]["years"], [2025])
        for spec in (
            self.spec(section="Другой участок"),
            self.spec(section=""),
            self.spec(mode="p20"),
        ):
            with self.assertRaises(InputError):
                apply_season_constraint(self.cfg(), 30, spec)

    def test_arrival_delay_keeps_integrator_history_and_calendar_checks(self):
        cfg = self.cfg()
        cfg.update(initial_source_t=0, arrivals=[{"h": 5, "t": 100}])
        event = dict(type="arrival_delay", announced_at_h=1, arrival_index=0, new_arrival_h=6)
        deadline = apply_season_constraint(cfg, 30, self.spec(start="2026-09-28T00:00"))
        result = run_replanning(cfg, event, deadline, time_limit=0.2)
        attach_season_trip_checks(cfg, result)
        for method in ("READY", "MILP"):
            self.assertTrue(cfg["season_assessment"]["trip_checks"][method][0]["fits"])
