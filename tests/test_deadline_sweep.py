import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.cli import main
from yana_planner.deadline_sweep import run_deadline_sweep
from yana_planner.domain import InputError
from yana_planner.replanning import run_replanning

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    cfg = json.loads((ROOT / "examples/control.json").read_text())
    cfg.update(
        id="SWEEP",
        target_t=20,
        destination_capacity_t=20,
        return_cutoff_h=30,
        arrivals=[dict(h=0, t=10), dict(h=8, t=10)],
        payload_calendar=[dict(from_h=0, capacity_t=10)],
    )
    cfg["vessels"] = [
        dict(
            id="A", available_h=0, capacity_t=10, load_h=1, up_h=2, unload_h=1, return_h=2, prep_h=1
        )
    ]
    event = dict(type="arrival_delay", announced_at_h=2, arrival_index=1, new_arrival_h=14)
    return cfg, event


class DeadlineSweepTests(unittest.TestCase):
    def test_full_grid_retained_first_grid_success_is_not_exact_threshold(self):
        cfg, event = inputs()
        originals = copy.deepcopy((cfg, event))
        result = run_deadline_sweep(cfg, event, [10, 16, 20], time_limit=1)
        self.assertEqual([r["deadline_h"] for r in result["rows"]], [10, 16, 20])
        self.assertEqual([r["READY_t"] for r in result["rows"]], [10, 10, 20])
        self.assertEqual([r["MILP_t"] for r in result["rows"]], [10, 10, 20])
        self.assertEqual(result["first_full_on_grid_h"], dict(READY=20, MILP=20))
        self.assertEqual(result["rows"][-1]["MILP_last_receipt_h"], 18)
        self.assertEqual((cfg, event), originals)
        self.assertTrue(all(r["delta_t"] == 0 for r in result["rows"]))

    def test_invalid_grid_rejected_before_any_run(self):
        cfg, event = inputs()
        for grid in [[], None, [20, 10], [10, 10], [2, 10], [True, 10], [10.5, 20]]:
            with (
                self.subTest(grid=grid),
                patch("yana_planner.deadline_sweep.run_replanning") as run,
            ):
                with self.assertRaises(InputError):
                    run_deadline_sweep(cfg, event, grid)
                run.assert_not_called()

    def test_failed_point_retained_as_unknown_and_later_points_continue(self):
        cfg, event = inputs()

        def execute(c, e, d, **kwargs):
            if d == 16:
                raise InputError("test failure")
            return run_replanning(c, e, d, **kwargs)

        with patch("yana_planner.deadline_sweep.run_replanning", side_effect=execute):
            result = run_deadline_sweep(cfg, event, [10, 16, 20], time_limit=1)
        failed = result["rows"][1]
        self.assertEqual(failed["experiment_status"], "failed")
        self.assertIsNone(failed["READY_t"])
        self.assertIsNone(failed["MILP_t"])
        self.assertIsNone(failed["delta_t"])
        self.assertEqual(result["rows"][2]["MILP_t"], 20)

    def test_changed_history_is_not_accepted(self):
        cfg, event = inputs()
        altered = run_replanning(cfg, event, 20, time_limit=1)
        altered["revised_baseline"]["committed"] = []
        with patch("yana_planner.deadline_sweep.run_replanning", return_value=altered):
            result = run_deadline_sweep(cfg, event, [20], time_limit=1)
        self.assertEqual(result["rows"][0]["experiment_status"], "failed")
        self.assertIsNone(result["rows"][0]["READY_t"])

    def test_protocol_is_saved_before_points_and_callbacks_do_not_mutate_results(self):
        cfg, event = inputs()
        started, points = [], []

        def start(protocol):
            started.append(protocol)
            protocol["deadlines_h"].clear()

        def point(row, result):
            self.assertTrue(started)
            points.append(row["deadline_h"])
            row["MILP_t"] = 999
            result.clear()

        result = run_deadline_sweep(
            cfg, event, [10, 20], time_limit=1, on_start=start, on_result=point
        )
        self.assertEqual(points, [10, 20])
        self.assertEqual(result["protocol"]["deadlines_h"], [10, 20])
        self.assertEqual(result["rows"][-1]["MILP_t"], 20)

    def test_time_limit_validation(self):
        cfg, event = inputs()
        for value in [None, False, 0, -1, float("nan"), float("inf")]:
            with self.subTest(value=value), self.assertRaises(InputError):
                run_deadline_sweep(cfg, event, [20], time_limit=value)

    def test_cli_exports_each_point_and_full_summary_without_plot_dependency(self):
        cfg, event = inputs()
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "input.json").write_text(json.dumps(cfg))
            (folder / "event.json").write_text(json.dumps(event))
            out = folder / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "sweep-deadlines",
                        str(folder / "input.json"),
                        "--event",
                        str(folder / "event.json"),
                        "--start",
                        "10",
                        "--stop",
                        "20",
                        "--step",
                        "5",
                        "--time-limit",
                        "1",
                        "--out",
                        str(out),
                    ]
                )
            self.assertEqual(code, 0)
            protocol = json.loads((out / "protocol.json").read_text())
            self.assertEqual(protocol["deadlines_h"], [10, 15, 20])
            for deadline in [10, 15, 20]:
                self.assertTrue((out / f"runs/{deadline}/results.json").is_file())
            page = (out / "report.html").read_text()
            self.assertIn("не точная минимальная граница", page)
            self.assertIn("runs/15/report.html", page)
            self.assertEqual(len(json.loads((out / "sweep.json").read_text())["rows"]), 3)

    def test_failed_cli_run_replaces_stale_success_and_plot(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "sweep.json").write_text('{"rows":[{"MILP_t":1650}]}')
            (folder / "deadline_sweep.svg").write_text("old successful plot")
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "sweep-deadlines",
                        "missing.json",
                        "--event",
                        "missing.json",
                        "--step",
                        "0",
                        "--out",
                        str(folder),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIsNone(json.loads((folder / "sweep.json").read_text())["accepted_t"])
            self.assertFalse((folder / "deadline_sweep.svg").exists())
