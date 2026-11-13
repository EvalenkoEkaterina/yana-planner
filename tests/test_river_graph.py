import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.domain import InputError
from yana_planner.river_graph import load_network, shortest_path, validate_network
from yana_planner.route_import import import_route_package
from yana_planner.service import ROOT
from yana_planner.web import create_app


def graph_package():
    package = json.loads((ROOT / "examples/routes/r03.package.json").read_text())
    path = shortest_path(load_network("demo"), "a", "c")
    package["route"] = dict(
        id="graph-a-c",
        origin=path["origin"],
        destination=path["destination"],
        length_km=path["length_km"],
        graph_path=path,
    )
    return package


class RiverGraphTests(unittest.TestCase):
    def test_shortest_path_selects_shorter_of_two_routes_without_mutation(self):
        network = load_network("demo")
        before = copy.deepcopy(network)
        path = shortest_path(network, "a", "c")
        self.assertEqual(path["length_km"], 100)
        self.assertEqual([e["id"] for e in path["segments"]], ["ab", "bc"])
        self.assertEqual(path["distance_status"], "scenario_assumption")
        self.assertEqual(network, before)

    def test_reverse_path_respects_directionality(self):
        network = load_network("demo")
        reverse = shortest_path(network, "c", "a")
        self.assertEqual(reverse["segments"][0]["from"], "c")
        self.assertEqual(reverse["length_km"], 100)
        for edge in network["edges"]:
            edge["bidirectional"] = False
        self.assertEqual(shortest_path(network, "c", "a")["status"], "no_path")

    def test_disconnected_same_and_unknown_nodes(self):
        network = load_network("demo")
        self.assertEqual(shortest_path(network, "a", "isolated")["status"], "no_path")
        for destination in ("a", "missing", None, []):
            with self.subTest(destination=destination), self.assertRaises(InputError):
                shortest_path(network, "a", destination)

    def test_network_rejects_invalid_weights_references_and_missing_provenance(self):
        for value in (0, -1, True, float("nan"), float("inf"), None):
            network = load_network("demo")
            network["edges"][0]["length_km"] = value
            with self.subTest(value=value), self.assertRaises(InputError):
                validate_network(network)
        for field, value in [("from", "missing"), ("bidirectional", None), ("source", "")]:
            network = load_network("demo")
            network["edges"][0][field] = value
            with self.subTest(field=field), self.assertRaises(InputError):
                validate_network(network)

    def test_package_preserves_times_and_validates_saved_path(self):
        package = graph_package()
        vessels = copy.deepcopy(package["scenario"]["vessels"])
        normalized = import_route_package(package)
        self.assertEqual(normalized["scenario"]["vessels"], vessels)
        self.assertEqual(normalized["route"], package["route"])
        for field, value in [("length_km", 1), ("origin", "Other")]:
            invalid = copy.deepcopy(package)
            invalid["route"][field] = value
            with self.subTest(field=field), self.assertRaises(InputError):
                import_route_package(invalid)
        invalid = copy.deepcopy(package)
        invalid["route"]["graph_path"]["segments"][0]["length_km"] = 1
        with self.assertRaises(InputError):
            import_route_package(invalid)

    def test_network_api_does_not_run_solver(self):
        client = create_app({"TESTING": True}).test_client()
        with patch("yana_planner.web.calculate_package_with_timeout") as solver:
            self.assertEqual(
                client.get("/api/river-network?network=demo").json["network_version"],
                "yana-river/1",
            )
            result = client.post(
                "/api/river-path", json={"network": "demo", "origin_id": "a", "destination_id": "c"}
            )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["length_km"], 100)
            disconnected = client.post(
                "/api/river-path",
                json={"network": "demo", "origin_id": "a", "destination_id": "isolated"},
            )
            self.assertEqual(disconnected.json["status"], "no_path")
            for body in (None, [], {"origin_id": "a", "destination_id": "a"}):
                self.assertEqual(client.post("/api/river-path", json=body).status_code, 400)
            solver.assert_not_called()

    def test_graph_snapshot_is_saved_with_calculation_and_in_offline_report(self):
        # Use the existing tiny economic fixture to exercise persistence without a slow search.
        from test_route_web_integration import package as tiny_package

        package = tiny_package()
        package["route"] = graph_package()["route"]
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(
                {"TESTING": True, "DATABASE": str(Path(tmp) / "runs.db")}
            ).test_client()
            response = client.post(
                "/", data={"route_package_json": json.dumps(package), "search_seconds": "2"}
            )
            self.assertEqual(response.status_code, 303, response.text)
            saved = client.get(response.location + "/results.json").json
            self.assertEqual(saved["inputs"]["route_package"]["route"], package["route"])
            report = client.get(response.location + "/report.html").text
            self.assertIn("Путь по графу:", report)
            self.assertIn("Тестовый причал Б", report)
            self.assertIn("Время движения задано отдельно", report)
