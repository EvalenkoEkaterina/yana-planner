import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.service import ROOT, calculate_package
from yana_planner.web import create_app
from yana_planner.web_worker import CalculationTimeout


def package():
    p = json.loads((ROOT / "examples/routes/ust-kuyga-batagay.package.json").read_text())
    p["scenario"] = json.loads((ROOT / "examples/economic-choice.json").read_text())
    p["cost_terms"] = json.loads((ROOT / "examples/economic-choice.costs.json").read_text())
    p.update(deadline_h=13, strategy="cost")
    p["scenario"].update(initial_source_t=0, arrivals=[dict(h=0, t=20)])
    return p


class RouteWebIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(dict(TESTING=True, DATABASE=str(Path(self.tmp.name) / "runs.db")))
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, p):
        return self.client.post(
            "/", data=dict(route_package_json=json.dumps(p), search_seconds="2")
        )

    def test_envelope_reaches_core_and_sources_persist(self):
        p = package()
        response = self.post(p)
        self.assertEqual(response.status_code, 303, response.text)
        data = self.client.get(response.location + "/results.json").json
        self.assertEqual(data["scenario_name"], "Усть-Куйга → Батагай")
        self.assertEqual(data["inputs"]["route_package"]["sources"], p["sources"])
        self.assertEqual(data["inputs"]["route_package"]["facts"], p["facts"])
        self.assertEqual(data["result"]["planning_strategy"], "cost")
        self.assertEqual(data["result"]["costs"]["planner"]["total_rub"], 200)
        with patch("yana_planner.web.calculate_package_with_timeout") as solver:
            self.assertEqual(self.client.get(response.location).status_code, 200)
            report = self.client.get(response.location + "/report.html").text
            self.assertIn(p["sources"][0]["url"], report)
            solver.assert_not_called()

    def test_full_package_survives_server_validation_and_timeout(self):
        p = package()
        p["cost_terms"]["horizon_h"] = 1
        response = self.post(p)
        self.assertEqual(response.status_code, 400)
        restored = json.loads(
            re.search(r'id="restored-route-package">(.*?)</script>', response.text, re.S).group(1)
        )
        self.assertEqual(restored, p)
        self.assertIn("data-capabilities=", response.text)
        with patch(
            "yana_planner.web.calculate_package_with_timeout",
            side_effect=CalculationTimeout("Лимит"),
        ):
            response = self.post(p)
        self.assertEqual(response.status_code, 408)
        restored = json.loads(
            re.search(r'id="restored-route-package">(.*?)</script>', response.text, re.S).group(1)
        )
        self.assertEqual(restored["cost_terms"], p["cost_terms"])
        self.assertEqual(self.client.get("/runs/1").status_code, 404)

    def test_weather_event_survives_package_path_without_a_weather_model(self):
        p = package()
        p["event"] = dict(
            type="weather_closure",
            announced_at_h=1,
            closures=[[5, 6]],
            rule_status="scenario_assumption",
            data_kind="synthetic",
            source="Synthetic integration test",
            forecast_issued_at=None,
        )
        inputs, result = calculate_package(p, search_seconds=2)
        self.assertEqual(inputs["event"], p["event"])
        self.assertEqual(inputs["route_package"]["event"], p["event"])
        self.assertEqual(result["initial"]["planning_strategy"], "cost")
        self.assertIn("revised_planner", result)

    def test_unsorted_delay_uses_same_party_through_both_adapters(self):
        p = package()
        p["scenario"]["arrivals"] = [dict(h=6, t=10), dict(h=0, t=10)]
        p["event"] = dict(type="arrival_delay", announced_at_h=1, arrival_index=0, new_arrival_h=8)
        before = copy.deepcopy(p)
        inputs, result = calculate_package(p, search_seconds=2)
        self.assertEqual(p, before)
        self.assertEqual(inputs["event"]["arrival_index"], 1)
        self.assertEqual(inputs["scenario"]["arrivals"][1], dict(h=6, t=10))
        self.assertEqual(inputs["route_package"]["event"]["arrival_index"], 1)
        self.assertIn("revised_planner", result)
