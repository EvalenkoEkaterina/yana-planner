import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from yana_planner.cli import main
from yana_planner.domain import InputError
from yana_planner.io import read_plan

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_failed_input_replaces_stale_success(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            source = p / "bad.json"
            source.write_text('{"mode":"BASE"}')
            target = p / "output"
            target.mkdir()
            (target / "results.json").write_text('{"accepted_t":1650}')
            (target / "report.html").write_text("Old successful delivery")
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(["plan", str(source), "--deadline", "328", "--out", str(target)])
            self.assertEqual(code, 2)
            r = json.loads((target / "results.json").read_text())
            self.assertIsNone(r["accepted_t"])
            self.assertEqual(r["status"], "requires_confirmation")
            self.assertNotIn("Old successful", (target / "report.html").read_text())

    def test_malformed_json_is_structured_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "bad.json").write_text("not json")
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(
                    ["plan", str(p / "bad.json"), "--deadline", "20", "--out", str(p / "out")]
                )
            self.assertEqual(code, 2)
            self.assertIsNone(json.loads((p / "out/results.json").read_text())["accepted_t"])

    def test_no_sim_permission_on_excel(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "import-excel",
                        str(ROOT / "tests/fixtures/team-r03.xlsx"),
                        "--profile",
                        "team-r03",
                        "--out",
                        str(p),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(p.read_text())["status"], "requires_confirmation")

    def test_malformed_plan_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text('{"planner":null}')
            with self.assertRaises(InputError):
                read_plan(p)
