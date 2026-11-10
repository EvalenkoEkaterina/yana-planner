import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.cli import main
from yana_planner.domain import ConfirmationRequired, InputError, Scenario
from yana_planner.experiments import summarize_experiment
from yana_planner.io import export_result
from yana_planner.planning import candidates, compare, optimize
from yana_planner.replanning import apply_event, apply_vessel_unavailable, replan, run_replanning
from yana_planner.validation import check_schedule

ROOT = Path(__file__).resolve().parents[1]


def small():
    cfg = json.loads((ROOT / "examples/control.json").read_text())
    cfg.update(
        id="OUTAGE",
        target_t=20,
        destination_capacity_t=20,
        return_cutoff_h=30,
        arrivals=[dict(h=0, t=20)],
        payload_calendar=[dict(from_h=0, capacity_t=10)],
    )
    cfg["vessels"] = [
        dict(
            id="A",
            available_h=0,
            capacity_t=10,
            load_h=1,
            up_h=2,
            unload_h=1,
            return_h=2,
            prep_h=1,
        )
    ]
    return cfg


def outage():
    return dict(type="vessel_unavailable", announced_at_h=2, vessel_id="A", from_h=8, to_h=14)


class AvailabilityContractTests(unittest.TestCase):
    def test_normalization_preserves_original_without_injecting_optional_field(self):
        cfg = small()
        before = copy.deepcopy(cfg)
        self.assertNotIn("unavailable_intervals", Scenario.from_dict(cfg).to_dict()["vessels"][0])
        cfg["vessels"][0]["unavailable_intervals"] = [[14.0, 18.0], (8, 12)]
        normalized = Scenario.from_dict(cfg).to_dict()
        self.assertEqual(normalized["vessels"][0]["unavailable_intervals"], [[8, 12], [14, 18]])
        self.assertEqual(cfg["vessels"][0]["unavailable_intervals"][0], [14.0, 18.0])
        self.assertNotIn("unavailable_intervals", before["vessels"][0])

    def test_invalid_interval_rejected_by_planner_input(self):
        for value in ["8-14", [[8]], [[14, 8]], [[8, 8]], [[False, 14]], [[8.5, 14]]]:
            with self.subTest(value=value), self.assertRaises(InputError):
                cfg = small()
                cfg["vessels"][0]["unavailable_intervals"] = value
                compare(cfg, 20)

    def test_unknown_intervals_require_confirmation(self):
        cfg = small()
        cfg["vessels"][0]["unavailable_intervals"] = None
        with self.assertRaises(ConfirmationRequired):
            compare(cfg, 20)

    def test_candidates_obey_full_cycle_and_half_open_boundaries(self):
        cfg = small()
        cfg["vessels"][0]["unavailable_intervals"] = [[8, 14]]
        rows = candidates(cfg, 20)
        starts = {t["start_h"] for t in rows}
        self.assertIn(1, starts)  # cycle [1,8) ends at outage start
        self.assertIn(14, starts)
        self.assertTrue(all(t["ready_h"] <= 8 or t["start_h"] >= 14 for t in rows))

    def test_actual_solver_handles_outage_without_invalid_candidate_fallback(self):
        cfg = small()
        cfg["vessels"][0]["unavailable_intervals"] = [[8, 14]]
        plan, logs = optimize(cfg, 20, baseline=[], time_limit=1)
        result = check_schedule(cfg, plan, 20)
        self.assertEqual(result["accepted_t"], 20)
        self.assertEqual(result["last_receipt_h"], 18)
        self.assertTrue(all(x["optimal"] for x in logs))
        self.assertTrue(all("checker_errors" not in x for x in logs))

    def test_entire_horizon_unavailable_has_zero_feasible_delivery(self):
        cfg = small()
        cfg["vessels"][0]["unavailable_intervals"] = [[0, 31]]
        result = compare(cfg, 20, time_limit=1)
        for method in ["baseline", "planner"]:
            self.assertEqual(result[method]["status"], "valid_simulation")
            self.assertEqual(result[method]["accepted_t"], 0)
            self.assertEqual(result[method]["shortfall_t"], 20)


class VesselEventTests(unittest.TestCase):
    def test_event_copies_inputs_preserves_availability_and_existing_intervals(self):
        cfg, event = small(), outage()
        cfg["vessels"][0]["unavailable_intervals"] = [[22, 25]]
        original_cfg, original_event = copy.deepcopy(cfg), copy.deepcopy(event)
        changed = apply_vessel_unavailable(cfg, event)
        self.assertEqual(changed["vessels"][0]["available_h"], 0)
        self.assertEqual(changed["vessels"][0]["unavailable_intervals"], [[8, 14], [22, 25]])
        self.assertEqual(cfg, original_cfg)
        self.assertEqual(event, original_event)

    def test_unknown_vessel_past_reversed_and_invalid_events_rejected(self):
        for changes in [
            dict(vessel_id="missing"),
            dict(from_h=1),
            dict(to_h=8),
            dict(vessel_id=None),
            dict(announced_at_h=True),
            dict(from_h=2.5),
        ]:
            with self.subTest(changes=changes), self.assertRaises(InputError):
                event = outage()
                event.update(changes)
                apply_event(small(), event)
        for event in [[], None, "outage", {}, dict(type="unknown")]:
            with self.subTest(event=event), self.assertRaises(InputError):
                apply_event(small(), event)

    def test_both_methods_replan_and_preserve_own_history(self):
        cfg, event = small(), outage()
        original = copy.deepcopy(cfg)
        result = run_replanning(cfg, event, 20, time_limit=1)
        self.assertEqual(result["experiment"], "single_vessel_unavailability")
        self.assertEqual(result["initial_input"], Scenario.from_dict(cfg).to_dict())
        for key in ["revised_baseline", "revised_planner"]:
            item = result[key]
            self.assertEqual(item["result"]["accepted_t"], 20)
            self.assertEqual(item["result"]["last_receipt_h"], 18)
            simplified = [
                {k: t[k] for k in ["vessel_id", "start_h", "quantity_t"]}
                for t in item["result"]["trips"]
            ]
            self.assertTrue(all(t in simplified for t in item["committed"]))
            self.assertTrue(all(t in item["committed"] or t["start_h"] > 2 for t in simplified))
            self.assertEqual(
                check_schedule(item["updated_input"], simplified, 20)["status"], "valid_simulation"
            )
        self.assertEqual(cfg, original)

    def test_initial_plan_does_not_know_future_outage(self):
        seen = []

        def capture(cfg, *args, **kwargs):
            seen.append(copy.deepcopy(cfg))
            return compare(cfg, *args, **kwargs)

        with patch("yana_planner.replanning.compare", side_effect=capture):
            run_replanning(small(), outage(), 20, time_limit=1)
        self.assertNotIn("unavailable_intervals", seen[0]["vessels"][0])

    def test_outage_conflicting_with_started_trip_requires_intervention(self):
        event = outage()
        event.update(from_h=3, to_h=6)
        result = run_replanning(small(), event, 20, time_limit=1)
        for key in ["revised_baseline", "revised_planner"]:
            item = result[key]
            self.assertEqual(item["result"]["status"], "requires_intervention")
            self.assertIsNone(item["result"]["accepted_t"])
            self.assertIsNone(item["result"]["shortfall_t"])
            self.assertEqual(item["committed"][0]["start_h"], 0)
        summary = summarize_experiment(result)[1]
        self.assertIsNone(summary["delta_t"])
        self.assertIsNone(summary["hours_saved"])

    def test_departure_at_announcement_is_already_committed(self):
        event = outage()
        event.update(from_h=3, to_h=6)
        plan = [
            dict(vessel_id="A", start_h=2, quantity_t=10),
            dict(vessel_id="A", start_h=9, quantity_t=10),
        ]
        result = replan(small(), plan, event, 20, method="ready")
        self.assertEqual(result["result"]["status"], "requires_intervention")
        self.assertEqual(result["committed"], [plan[0]])

    def test_cli_writes_human_readable_outage_and_full_result(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source, event = folder / "cfg.json", folder / "event.json"
            source.write_text(json.dumps(small()))
            event.write_text(json.dumps(outage()))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "replan",
                        str(source),
                        "--event",
                        str(event),
                        "--deadline",
                        "20",
                        "--out",
                        str(folder / "out"),
                    ]
                )
            self.assertEqual(code, 0)
            page = (folder / "out/report.html").read_text()
            self.assertIn('id="event-change"', page)
            self.assertIn("8-го", page)
            self.assertIn("14-го часа", page)
            self.assertIn("Начатые рейсы", page)

    def test_arrival_report_shows_old_and_new_times_from_input(self):
        cfg = small()
        cfg["arrivals"] = [dict(h=0, t=10), dict(h=8, t=10)]
        event = dict(type="arrival_delay", announced_at_h=2, arrival_index=1, new_arrival_h=14)
        result = run_replanning(cfg, event, 20, time_limit=1)
        with tempfile.TemporaryDirectory() as folder:
            export_result(folder, result)
            self.assertIn("8 ч → 14 ч", (Path(folder) / "report.html").read_text())
            del result["initial_input"]
            export_result(folder, result)
            page = (Path(folder) / "report.html").read_text()
            self.assertNotIn("8 ч → 14 ч", page)
            self.assertIn("14 ч", page)
