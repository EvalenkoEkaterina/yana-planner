import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.experiments import summarize
from yana_planner.service import calculate
from yana_planner.web import create_app


class ComparisonTests(unittest.TestCase):
    def test_interpretation(self):
        a = dict(
            status="valid_simulation", accepted_t=10, shortfall_t=0, trip_count=1, last_receipt_h=5
        )
        c = summarize(a, a, 10, [])
        self.assertTrue(c["equal_volume"])
        self.assertFalse(c["optimal"])
        b = dict(a, accepted_t=5, shortfall_t=5)
        self.assertFalse(summarize(a, b, 10, [])["equal_volume"])
        self.assertFalse(summarize(a, b, 10, [])["rows"]["MILP"]["target_met"])
        b = dict(a, status="invalid")
        self.assertIsNone(summarize(a, b, 10, [])["rows"]["MILP"]["accepted_t"])
        b = dict(a, last_receipt_h=None)
        self.assertIsNone(summarize(a, b, 10, [])["rows"]["MILP"]["last_receipt_h"])
        self.assertTrue(summarize(a, a, 10, [{"optimal": True}])["optimal"])


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs, cls.result = calculate("r03", "328", time_limit=0.1)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = dict(TESTING=True, DATABASE=str(Path(self.tmp.name) / "runs.db"))
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_pages_and_persistence(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        with patch(
            "yana_planner.web.calculate",
            return_value=(copy.deepcopy(self.inputs), copy.deepcopy(self.result)),
        ) as solver:
            response = self.client.post("/", data=dict(scenario="r03", deadline="328"))
            self.assertEqual(response.status_code, 303)
            url = response.location
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(solver.call_count, 1)
        self.assertIn("R03", self.client.get("/history").text)
        self.assertEqual(self.client.get(url + "/report.html").status_code, 200)
        self.assertEqual(self.client.get(url + "/results.json").status_code, 200)
        self.assertEqual(create_app(self.config).test_client().get(url).status_code, 200)
        self.assertEqual(self.client.get("/runs/99999").status_code, 404)

    def test_bad_deadline_before_solver(self):
        with patch("yana_planner.service.compare") as solver:
            for value in ["", "0", "-1", "1.5", "493", "hello"]:
                self.assertEqual(
                    self.client.post("/", data=dict(scenario="r03", deadline=value)).status_code,
                    400,
                )
            solver.assert_not_called()

    def test_delay_history(self):
        inputs, result = calculate("delay", "328", time_limit=0.1)
        for key in ["revised_baseline", "revised_planner"]:
            revised = result[key]
            self.assertEqual(revised["result"]["status"], "valid_simulation")
            for trip in revised["committed"]:
                self.assertTrue(
                    any(all(t[k] == v for k, v in trip.items()) for t in revised["result"]["trips"])
                )
        self.assertIsNotNone(inputs["event"])
