"""Temporary loading limits: information timing, calendar boundaries and execution."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_vessel_events import small

from yana_planner.cli import main
from yana_planner.comparison import select_economic_comparison
from yana_planner.domain import ConfirmationRequired, InputError
from yana_planner.economics import compare_cost
from yana_planner.experiments import summarize_experiment
from yana_planner.io import export_result
from yana_planner.planning import candidates, compare
from yana_planner.replanning import apply_event, apply_payload_restriction, replan, run_replanning
from yana_planner.validation import check_schedule


def restriction(**changes):
    event = dict(type="payload_restriction", announced_at_h=2, from_h=8, to_h=14, capacity_t=5)
    event.update(changes)
    return event


def capacity_at(cfg, hour):
    return next(
        point["capacity_t"]
        for point in reversed(cfg["payload_calendar"])
        if point["from_h"] <= hour
    )


class PayloadCalendarTests(unittest.TestCase):
    def test_input_and_event_remain_unchanged(self):
        cfg, event = small(), restriction()
        originals = copy.deepcopy((cfg, event))
        changed = apply_payload_restriction(cfg, event)
        self.assertEqual((cfg, event), originals)
        self.assertEqual(
            changed["payload_calendar"],
            [
                dict(from_h=0, capacity_t=10),
                dict(from_h=8, capacity_t=5),
                dict(from_h=14, capacity_t=10),
            ],
        )
        self.assertEqual(changed["provenance"]["update"], event)

    def test_stricter_original_limit_and_later_changes_are_restored(self):
        cfg = small()
        cfg["payload_calendar"] = [
            dict(from_h=0, capacity_t=10),
            dict(from_h=10, capacity_t=4),
            dict(from_h=18, capacity_t=8),
        ]
        changed = apply_event(cfg, restriction())
        for hour, expected in [(7, 10), (8, 5), (9, 5), (10, 4), (13, 4), (14, 4), (18, 8)]:
            with self.subTest(hour=hour):
                self.assertEqual(capacity_at(changed, hour), expected)

    def test_restriction_cannot_relax_existing_limit(self):
        changed = apply_event(small(), restriction(capacity_t=20))
        self.assertEqual(changed["payload_calendar"], [dict(from_h=0, capacity_t=10)])

    def test_overlapping_restrictions_keep_tightest_limit(self):
        changed = apply_event(small(), restriction())
        changed = apply_event(
            changed, restriction(announced_at_h=3, from_h=10, to_h=16, capacity_t=3)
        )
        for hour, expected in [(7, 10), (8, 5), (10, 3), (14, 3), (15, 3), (16, 10)]:
            self.assertEqual(capacity_at(changed, hour), expected)

    def test_zero_is_known_loaded_movement_limit(self):
        changed = apply_event(small(), restriction(capacity_t=0))
        self.assertEqual(capacity_at(changed, 8), 0)
        self.assertEqual(capacity_at(changed, 14), 10)
        self.assertTrue(
            all(t["arrival_h"] <= 8 or t["load_end_h"] >= 14 for t in candidates(changed, 20))
        )

    def test_interval_can_start_at_announcement_including_zero(self):
        changed = apply_event(small(), restriction(announced_at_h=0, from_h=0, to_h=4))
        self.assertEqual(capacity_at(changed, 0), 5)
        self.assertEqual(capacity_at(changed, 4), 10)

    def test_missing_values_require_confirmation(self):
        for field in ["announced_at_h", "from_h", "to_h", "capacity_t"]:
            with self.subTest(field=field), self.assertRaises(ConfirmationRequired):
                apply_event(small(), restriction(**{field: None}))

    def test_invalid_and_retroactive_values_rejected(self):
        for changes in [
            dict(from_h=1),
            dict(to_h=8),
            dict(to_h=7),
            dict(capacity_t=-1),
            dict(capacity_t=0.5),
            dict(capacity_t=True),
            dict(from_h=False),
            dict(from_h=8.5),
            dict(to_h=float("inf")),
            dict(announced_at_h=-1),
            dict(capacity_t="5"),
        ]:
            with self.subTest(changes=changes), self.assertRaises(InputError):
                apply_event(small(), restriction(**changes))

    def test_direct_adapter_rejects_wrong_event_type(self):
        with self.assertRaises(InputError):
            apply_payload_restriction(small(), dict(type="arrival_delay"))

    def test_pointwise_calendar_semantics_on_all_boundaries(self):
        cfg = small()
        cfg["payload_calendar"] = [
            dict(from_h=0, capacity_t=10),
            dict(from_h=9, capacity_t=4),
            dict(from_h=15, capacity_t=8),
        ]
        for start, end, limit in [(2, 9, 5), (9, 15, 0), (8, 16, 3), (15, 18, 20)]:
            changed = apply_event(cfg, restriction(from_h=start, to_h=end, capacity_t=limit))
            for hour in range(21):
                expected = (
                    min(capacity_at(cfg, hour), limit)
                    if start <= hour < end
                    else capacity_at(cfg, hour)
                )
                with self.subTest(start=start, end=end, limit=limit, hour=hour):
                    self.assertEqual(capacity_at(changed, hour), expected)


class PayloadExecutionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = small()
        self.plan = [
            dict(vessel_id="A", start_h=0, quantity_t=10),
            dict(vessel_id="A", start_h=7, quantity_t=10),
        ]

    def test_old_plan_is_rechecked_under_updated_calendar(self):
        self.assertEqual(check_schedule(self.cfg, self.plan, 20)["status"], "valid_simulation")
        changed = apply_event(self.cfg, restriction())
        self.assertEqual(check_schedule(changed, self.plan, 20)["status"], "invalid")

    def test_both_methods_replan_preserve_history_and_mass_balance(self):
        result = run_replanning(self.cfg, restriction(), 20, time_limit=1)
        self.assertEqual(result["experiment"], "single_payload_restriction")
        self.assertEqual(result["revised_baseline"]["result"]["trip_count"], 3)
        self.assertEqual(result["revised_planner"]["result"]["trip_count"], 2)
        for key in ["revised_baseline", "revised_planner"]:
            section = result[key]
            verdict = section["result"]
            self.assertEqual(verdict["status"], "valid_simulation")
            self.assertEqual(verdict["accepted_t"], 20)
            simplified = [
                {k: t[k] for k in ["vessel_id", "start_h", "quantity_t"]} for t in verdict["trips"]
            ]
            self.assertIn(self.plan[0], simplified)
            self.assertTrue(all(t in section["committed"] or t["start_h"] > 2 for t in simplified))
            self.assertEqual(
                check_schedule(section["updated_input"], simplified, 20)["status"],
                "valid_simulation",
            )
            for row in verdict["ledger"]:
                self.assertEqual(
                    row["source_t"]
                    + row["in_transit_t"]
                    + row["destination_t"]
                    + row["withdrawn_t"],
                    20,
                )

    def test_initial_solver_does_not_receive_future_restriction(self):
        seen = []

        def capture(cfg, *args, **kwargs):
            seen.append(copy.deepcopy(cfg))
            return compare(cfg, *args, **kwargs)

        with patch("yana_planner.replanning.compare", side_effect=capture):
            run_replanning(self.cfg, restriction(), 20, time_limit=1)
        self.assertEqual(seen[0]["payload_calendar"], self.cfg["payload_calendar"])
        self.assertNotIn("update", seen[0]["provenance"])

    def test_started_overload_requires_intervention_with_exact_details(self):
        event = restriction(from_h=2, to_h=3)
        result = run_replanning(self.cfg, event, 20, time_limit=1)
        for key in ["revised_baseline", "revised_planner"]:
            section = result[key]
            self.assertEqual(section["result"]["status"], "requires_intervention")
            self.assertIsNone(section["result"]["accepted_t"])
            self.assertIsNone(section["result"]["shortfall_t"])
            self.assertEqual(section["committed"], [self.plan[0]])
            conflict = section["result"]["conflicts"][0]
            self.assertEqual(conflict["vessel_id"], "A")
            self.assertEqual(conflict["quantity_t"], 10)
            self.assertEqual(conflict["allowed_capacity_t"], 5)
            self.assertEqual(conflict["limiting_intervals"], [dict(from_h=2, to_h=3, capacity_t=5)])
        summary = summarize_experiment(result)[1]
        self.assertIsNone(summary["delta_t"])
        self.assertIsNone(summary["hours_saved"])
        with self.assertRaises(InputError):
            select_economic_comparison(self.cfg, result, 20)

    def test_start_at_announcement_is_committed(self):
        event = restriction(announced_at_h=7, from_h=8, to_h=14)
        section = replan(self.cfg, self.plan, event, 20, method="ready")
        self.assertEqual(section["committed"], self.plan)
        self.assertEqual(section["result"]["status"], "requires_intervention")

    def test_limit_start_at_loaded_leg_end_does_not_conflict(self):
        section = replan(self.cfg, self.plan, restriction(from_h=3, to_h=4), 20)
        self.assertEqual(section["result"]["status"], "valid_simulation")
        self.assertEqual(section["result"]["accepted_t"], 20)

    def test_limit_end_at_loaded_leg_start_does_not_conflict(self):
        changed = apply_event(self.cfg, restriction(announced_at_h=0, from_h=0, to_h=1))
        self.assertEqual(check_schedule(changed, self.plan, 20)["status"], "valid_simulation")

    def test_zero_limit_during_return_does_not_close_empty_movement(self):
        section = replan(self.cfg, self.plan, restriction(from_h=4, to_h=6, capacity_t=0), 20)
        self.assertEqual(section["result"]["status"], "valid_simulation")

    def test_loading_already_started_cannot_reduce_its_party(self):
        cfg = small()
        cfg["vessels"][0]["load_h"] = 3
        plan = [dict(vessel_id="A", start_h=0, quantity_t=10)]
        section = replan(cfg, plan, restriction(from_h=3, to_h=5), 20)
        self.assertEqual(section["result"]["status"], "requires_intervention")
        self.assertEqual(section["committed"][0]["quantity_t"], 10)

    def test_economics_uses_new_calendar_and_recomputed_plans(self):
        result = run_replanning(self.cfg, restriction(), 20, time_limit=1)
        effective, left, right, context = select_economic_comparison(self.cfg, result, 20)
        self.assertEqual(capacity_at(effective, 8), 5)
        self.assertEqual(context["phase"], "after")
        costs = compare_cost(
            effective,
            left,
            right,
            20,
            dict(
                billing_mode="inter_voyage_gaps",
                separate_trip_operations_rub=10,
                active_vessel_hour_rub=1,
                waiting_vessel_hour_rub=1,
            ),
        )
        self.assertEqual(costs["baseline"]["accepted_t"], 20)
        self.assertEqual(costs["alternative"]["accepted_t"], 20)
        altered = copy.deepcopy(result)
        altered["revised_planner"]["updated_input"]["payload_calendar"] = self.cfg[
            "payload_calendar"
        ]
        with self.assertRaises(InputError):
            select_economic_comparison(self.cfg, altered, 20)

    def test_report_and_cli_for_normal_update(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "input.json").write_text(json.dumps(self.cfg))
            (folder / "event.json").write_text(json.dumps(restriction()))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "replan",
                        str(folder / "input.json"),
                        "--event",
                        str(folder / "event.json"),
                        "--deadline",
                        "20",
                        "--out",
                        str(folder / "out"),
                    ]
                )
            self.assertEqual(code, 0)
            page = (folder / "out/report.html").read_text()
            self.assertIn("временный предел", page)
            self.assertIn("8-го", page)
            self.assertIn("14-го часа", page)
            self.assertIn("погодный прогноз не рассчитывается", page)

    def test_conflict_report_and_cli_keep_unknown_totals(self):
        result = run_replanning(self.cfg, restriction(from_h=2, to_h=3), 20, time_limit=1)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            export_result(folder, result)
            page = (folder / "report.html").read_text()
            self.assertIn("Требуется вмешательство диспетчера", page)
            self.assertIn("[2, 3)", page)
            self.assertIn("не определено", page)
            (folder / "input.json").write_text(json.dumps(self.cfg))
            (folder / "event.json").write_text(json.dumps(restriction(from_h=2, to_h=3)))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "replan",
                        str(folder / "input.json"),
                        "--event",
                        str(folder / "event.json"),
                        "--deadline",
                        "20",
                        "--out",
                        str(folder / "out"),
                    ]
                )
            self.assertEqual(code, 2)
