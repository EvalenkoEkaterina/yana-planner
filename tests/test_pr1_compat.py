import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from yana_planner.cli import main
from yana_planner.compat import normalize_event, normalize_result
from yana_planner.deadline_sweep import run_deadline_sweep
from yana_planner.domain import InputError
from yana_planner.experiments import summarize_experiment
from yana_planner.io import export_result
from yana_planner.online import (
    apply_vessel_unavailability,
    replan_unavailability,
    run_unavailability_experiment,
)
from yana_planner.replanning import apply_event

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    cfg = json.loads((ROOT / "examples/control.json").read_text())
    cfg.update(
        id="COMPAT",
        target_t=20,
        destination_capacity_t=20,
        return_cutoff_h=30,
        arrivals=[dict(h=0, t=20)],
        payload_calendar=[dict(from_h=0, capacity_t=10)],
    )
    cfg["vessels"] = [
        dict(
            id="A", available_h=0, capacity_t=10, load_h=1, up_h=2, unload_h=1, return_h=2, prep_h=1
        )
    ]
    event = dict(type="vessel_unavailability", announced_at_h=2, vessel_id="A", interval=[8, 14])
    return cfg, event


class PR1CompatibilityTests(unittest.TestCase):
    def test_event_conversion_is_nonmutating_and_idempotent(self):
        _, event = inputs()
        original = copy.deepcopy(event)
        normalized = normalize_event(event)
        self.assertEqual(normalized["type"], "vessel_unavailable")
        self.assertEqual((normalized["from_h"], normalized["to_h"]), (8, 14))
        self.assertNotIn("interval", normalized)
        self.assertEqual(normalize_event(normalized), normalized)
        self.assertEqual(event, original)

    def test_conflicting_event_bounds_are_not_silently_selected(self):
        cfg, event = inputs()
        event["from_h"] = 7
        with self.assertRaises(InputError):
            apply_event(cfg, event)

    def test_malformed_and_retroactive_legacy_events_are_rejected(self):
        cfg, original = inputs()
        for interval in [None, [8], [8, 8], [14, 8], [1, 14], [True, 14], [8.5, 14]]:
            with self.subTest(interval=interval), self.assertRaises(InputError):
                event = copy.deepcopy(original)
                event["interval"] = interval
                apply_vessel_unavailability(cfg, event)
        for event in [None, [], "outage"]:
            with self.subTest(event=event), self.assertRaises(InputError):
                replan_unavailability(cfg, [], event, 20)

    def test_legacy_and_current_event_apply_same_operating_change(self):
        cfg, event = inputs()
        self.assertEqual(apply_event(cfg, event), apply_event(cfg, normalize_event(event)))

    def test_participant_api_returns_shared_and_legacy_result_keys(self):
        cfg, event = inputs()
        result = run_unavailability_experiment(cfg, event, 20, time_limit=1)
        self.assertEqual(result["source_event"], event)
        self.assertEqual(result["event"]["type"], "vessel_unavailable")
        self.assertEqual(result["revised_ready"], result["revised_baseline"])
        self.assertEqual(result["revised_milp"], result["revised_planner"])
        rows = summarize_experiment(result)
        self.assertEqual(rows[1]["READY_t"], 20)
        self.assertEqual(rows[1]["MILP_t"], 20)
        self.assertEqual(rows[1]["MILP_last_receipt_h"], 18)

    def test_original_saved_pr_packet_now_summarizes_without_losing_results(self):
        original = json.loads((ROOT / "outputs_online/unavailability_experiment.json").read_text())
        before = copy.deepcopy(original)
        result = normalize_result(original)
        self.assertEqual(result["scenario_id"], "R03")
        self.assertNotIn("initial_input", result)
        self.assertEqual(normalize_result(result), result)
        after = summarize_experiment(original)[1]
        self.assertEqual((after["READY_t"], after["MILP_t"]), (1650, 1650))
        self.assertEqual(after["trips_saved"], 1)
        self.assertEqual(after["hours_saved"], 16)
        self.assertEqual(original, before)

    def test_disagreeing_result_aliases_raise_error(self):
        original = json.loads((ROOT / "outputs_online/unavailability_experiment.json").read_text())
        result = normalize_result(original)
        result["revised_milp"]["result"]["accepted_t"] = 1
        with self.assertRaises(InputError):
            summarize_experiment(result)

    def test_invalid_old_result_remains_unknown(self):
        original = json.loads((ROOT / "outputs_online/unavailability_experiment.json").read_text())
        original["revised_milp"]["result"]["status"] = "invalid"
        after = summarize_experiment(original)[1]
        self.assertIsNone(after["MILP_t"])
        self.assertIsNone(after["hours_saved"])

    def test_report_command_imports_old_pr_file_and_writes_real_after_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "report",
                        str(ROOT / "outputs_online/unavailability_experiment.json"),
                        "--out",
                        folder,
                    ]
                )
            self.assertEqual(code, 0)
            path = Path(folder)
            result = json.loads((path / "results.json").read_text())
            summary = json.loads((path / "summary.json").read_text())
            self.assertEqual(result["source_event"]["type"], "vessel_unavailability")
            self.assertEqual(summary[1]["MILP_t"], 1650)
            self.assertIn("120-го", (path / "report.html").read_text())

    def test_legacy_cli_event_runs_through_report_and_summary(self):
        cfg, event = inputs()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "input.json").write_text(json.dumps(cfg))
            (folder / "event.json").write_text(json.dumps(event))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "replan",
                        str(folder / "input.json"),
                        "--event",
                        str(folder / "event.json"),
                        "--deadline",
                        "20",
                        "--time-limit",
                        "1",
                        "--out",
                        str(folder / "run"),
                    ]
                )
            self.assertEqual(code, 0)
            result = json.loads((folder / "run/results.json").read_text())
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "summarize",
                        str(folder / "run/results.json"),
                        "--out",
                        str(folder / "summary.json"),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads((folder / "summary.json").read_text())[1]["MILP_t"], 20)
            self.assertEqual(result["source_event"], event)

    def test_sweep_accepts_old_event_and_retains_original_message(self):
        cfg, event = inputs()
        result = run_deadline_sweep(cfg, event, [20], time_limit=1)
        self.assertEqual(result["protocol"]["source_event"], event)
        self.assertEqual(result["rows"][0]["experiment_status"], "completed")
        self.assertEqual(result["rows"][0]["MILP_t"], 20)

    def test_failed_report_replaces_stale_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            out = folder / "out"
            out.mkdir()
            (out / "summary.json").write_text('[{"MILP_t":1650}]')
            (folder / "bad.json").write_text('{"deadline_h":328}')
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(["report", str(folder / "bad.json"), "--out", str(out)])
            self.assertEqual(code, 2)
            self.assertIsNone(json.loads((out / "summary.json").read_text())["accepted_t"])

    def test_report_refuses_to_overwrite_source_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / "results.json"
            original = (ROOT / "outputs_online/unavailability_experiment.json").read_bytes()
            source.write_bytes(original)
            (folder / "comparison.csv").write_text("original comparison")
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(["report", str(source), "--out", str(folder)])
            self.assertEqual(code, 2)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual((folder / "comparison.csv").read_text(), "original comparison")

    def test_export_accepts_old_shape_without_modifying_source(self):
        original = json.loads((ROOT / "outputs_online/unavailability_experiment.json").read_text())
        before = copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as folder:
            export_result(folder, original)
            self.assertIn("После события, MILP", (Path(folder) / "report.html").read_text())
        self.assertEqual(original, before)
