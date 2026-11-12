import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.domain import InputError
from yana_planner.service import ROOT, calculate_request
from yana_planner.web import create_app
from yana_planner.web_worker import CalculationTimeout


def request_data():
    return dict(
        scenario=json.loads((ROOT / "examples/economic-choice.json").read_text()),
        deadline_h=13,
        strategy="cost",
        cost_terms=json.loads((ROOT / "examples/economic-choice.costs.json").read_text()),
        search_seconds=2,
    )


class PlanApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(dict(TESTING=True, DATABASE=str(Path(self.tmp.name) / "runs.db")))
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_cost_request_runs_in_worker_and_persists_without_recalculation(self):
        response = self.client.post("/api/plan", json=request_data())
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json
        result = data["result"]
        self.assertEqual(result["planning_strategy"], "cost")
        self.assertEqual(result["planner"]["accepted_t"], 20)
        self.assertEqual(result["planner"]["trip_count"], 2)
        self.assertEqual(result["costs"]["planner"]["total_rub"], 200)
        self.assertIsNone(result["actual_savings_rub"])
        self.assertEqual(data["inputs"]["cost_terms"], request_data()["cost_terms"])
        with patch("yana_planner.web.calculate_request_with_timeout") as solver:
            for link in data["links"].values():
                self.assertEqual(self.client.get(link).status_code, 200)
            saved = self.client.get(data["links"]["json"]).json
            self.assertEqual(saved["result"]["input_sha256"], result["input_sha256"])
            self.assertEqual(saved["inputs"]["settings"]["strategy"], "cost")
            solver.assert_not_called()

    def test_weather_request_preserves_event_and_cost_strategy(self):
        payload = request_data()
        payload["event"] = dict(
            type="weather_closure",
            announced_at_h=1,
            closures=[[5, 6]],
            data_kind="synthetic",
            rule_status="scenario_assumption",
            forecast_issued_at=None,
        )
        response = self.client.post("/api/plan", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json
        self.assertEqual(data["inputs"]["event"], payload["event"])
        self.assertEqual(data["result"]["initial"]["planning_strategy"], "cost")
        self.assertIn("revised_planner", data["result"])
        self.assertEqual(self.client.get(data["links"]["page"]).status_code, 200)
        self.assertIn("Погод", self.client.get(data["links"]["report"]).text)

    def test_invalid_contract_is_rejected_before_planning(self):
        bad = [
            None,
            [],
            {},
            dict(request_data(), deadline_h=True),
            dict(request_data(), deadline_h=493),
            dict(request_data(), strategy="ml"),
            dict(request_data(), search_seconds=True),
            dict(request_data(), search_seconds="10"),
            dict(request_data(), cost_terms=None),
            dict(request_data(), ignored_field=1),
        ]
        missing = copy.deepcopy(request_data())
        del missing["scenario"]["vessels"][0]["available_h"]
        bad.append(missing)
        for payload in bad:
            with self.subTest(payload=payload), patch("yana_planner.service.compare") as solver:
                with self.assertRaises(InputError):
                    calculate_request(payload)
                solver.assert_not_called()
        response = self.client.post("/api/plan", data="broken", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["code"], "invalid_input")

    def test_wall_timeout_and_oversized_request_do_not_save_a_run(self):
        with patch(
            "yana_planner.web.calculate_request_with_timeout",
            side_effect=CalculationTimeout("Лимит исчерпан"),
        ):
            response = self.client.post("/api/plan", json=request_data())
        self.assertEqual(response.status_code, 408)
        self.assertIsNone(response.json["result"])
        self.assertEqual(self.client.get("/runs/1").status_code, 404)
        response = self.client.post(
            "/api/plan", data=" " * 1_048_577, content_type="application/json"
        )
        self.assertEqual(response.status_code, 413)

    def test_non_finite_json_is_an_input_error(self):
        from yana_planner.web_worker import calculate_request_with_timeout

        with patch("yana_planner.web_worker.subprocess.run") as process:
            with self.assertRaises(InputError):
                calculate_request_with_timeout(dict(request_data(), deadline_h=float("nan")))
            process.assert_not_called()
