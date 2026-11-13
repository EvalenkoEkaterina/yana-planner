"""End-to-end filter matrix and bundled input validation, beyond individual unit cases."""

import copy
import itertools
import json
import tempfile
import unittest
from pathlib import Path

from yana_planner.domain import InputError, Scenario, integer
from yana_planner.multi_stop import normalize
from yana_planner.river_graph import load_network
from yana_planner.route_import import import_route_package
from yana_planner.service import ROOT, calculate_request
from yana_planner.validation import check_schedule
from yana_planner.web import create_app


class ScenarioMatrixTests(unittest.TestCase):
    def test_bundled_data_contracts(self):
        for key in ("demo", "yana"):
            with self.subTest(network=key):
                load_network(key)
        for path in (ROOT / "examples").rglob("*.json"):
            data = json.loads(path.read_text())
            if isinstance(data, dict) and data.get("mode") == "SIM_ONLY":
                with self.subTest(scenario=path.name):
                    if "stops" in data and "vessel" in data:
                        normalize(data)
                    else:
                        Scenario.from_dict(data)
        for path in (ROOT / "src/yana_planner/static/data").glob("*.package.json"):
            with self.subTest(package=path.name):
                import_route_package(json.loads(path.read_text()))

    def test_strategy_cost_loading_and_event_matrix(self):
        cfg = json.loads((ROOT / "examples/economic-choice.json").read_text())
        terms = json.loads((ROOT / "examples/economic-choice.costs.json").read_text())
        for strategy, priced, rate, event_kind, deadline in itertools.product(
            ("trips", "cost"), (False, True), (False, True), ("none", "delay", "weather"), (8, 13)
        ):
            if strategy == "cost" and not priced:
                continue
            with self.subTest(
                strategy=strategy, priced=priced, rate=rate, event=event_kind, deadline=deadline
            ):
                scenario = copy.deepcopy(cfg)
                scenario.update(initial_source_t=10, arrivals=[dict(h=4, t=10)])
                if rate:
                    for v in scenario["vessels"]:
                        v.pop("load_h")
                        v.update(load_rate_tph=10, load_setup_h=0)
                event = None
                if event_kind == "delay":
                    event = dict(
                        type="arrival_delay", announced_at_h=1, arrival_index=0, new_arrival_h=6
                    )
                elif event_kind == "weather":
                    event = dict(
                        type="weather_closure",
                        announced_at_h=1,
                        closures=[[7, 8]],
                        data_kind="synthetic",
                        rule_status="scenario_assumption",
                        source="Matrix stress test",
                        forecast_issued_at=None,
                    )
                inputs, result = calculate_request(
                    dict(
                        scenario=scenario,
                        deadline_h=deadline,
                        strategy=strategy,
                        cost_terms=terms if priced else None,
                        event=event,
                        search_seconds=2,
                    )
                )
                pairs = (
                    [(inputs["scenario"], result)]
                    if event is None
                    else [(inputs["scenario"], result["initial"])]
                )
                for c, r in pairs:
                    for method in ("baseline", "planner"):
                        verdict = check_schedule(c, r[method]["trips"], deadline)
                        self.assertEqual(verdict["status"], "valid_simulation")
                        self.assertEqual(verdict["accepted_t"], r[method]["accepted_t"])
                    self.assertGreaterEqual(r["planner"]["accepted_t"], r["baseline"]["accepted_t"])
                    if priced:
                        self.assertEqual(r["costs"]["planner"]["status"], "scenario_cost")
                if event:
                    for method in ("revised_baseline", "revised_planner"):
                        revised = result[method]
                        if revised["result"]["status"] == "valid_simulation":
                            verdict = check_schedule(
                                revised["updated_input"], revised["result"]["trips"], deadline
                            )
                            self.assertEqual(verdict["status"], "valid_simulation")
                            scheduled = [
                                {k: t[k] for k in ("vessel_id", "start_h", "quantity_t")}
                                for t in revised["result"]["trips"]
                            ]
                            for trip in revised["committed"]:
                                self.assertIn(trip, scheduled)

    def test_api_methods_missing_routes_and_extreme_integer_are_input_errors(self):
        with self.assertRaises(InputError):
            integer(10**400, "hours")
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(
                dict(TESTING=True, DATABASE=str(Path(tmp) / "runs.db"))
            ).test_client()
            for path in ("/api/no-such-route", "/api/multi-stop/runs/9999"):
                response = client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json["code"], "not_found")
            for path in ("/api/plan", "/api/river-path", "/api/multi-stop/plan"):
                response = client.get(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.json["code"], "method_not_allowed")
                response = client.post(path, data="broken", content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertIsNotNone(response.json)
