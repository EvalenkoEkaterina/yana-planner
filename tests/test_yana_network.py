import copy
import json
import tempfile
import unittest
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.network_import import import_yana_network
from yana_planner.river_graph import load_network, shortest_path, validate_route_graph
from yana_planner.service import ROOT
from yana_planner.web import create_app


class YanaNetworkTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads((ROOT / "data/network/yana-network.source.json").read_text())

    def test_adapter_preserves_source_unknowns_and_physical_direction(self):
        before = copy.deepcopy(self.raw)
        network = import_yana_network(self.raw)
        self.assertEqual(self.raw, before)
        self.assertEqual(network, load_network())
        self.assertEqual(len(network["nodes"]), 5)
        self.assertEqual(len(network["edges"]), 4)
        for edge in network["edges"]:
            self.assertTrue(edge["bidirectional"])
            self.assertIsNone(edge["reference"]["travel_time_h"])
            self.assertIsNone(edge["reference"]["payload_limit_t"])
            self.assertIsNone(edge["reference"]["operational_passage_confirmed"])
            self.assertEqual(edge["source_id"], "S1")

    def test_all_pairs_and_published_sums(self):
        network = load_network()
        for node in network["nodes"]:
            for other in network["nodes"]:
                if node["id"] != other["id"]:
                    self.assertEqual(
                        shortest_path(network, node["id"], other["id"])["status"], "found"
                    )
        for expected in self.raw["derived_paths"]:
            path = shortest_path(network, expected["from_id"], expected["to_id"])
            reverse = shortest_path(network, expected["to_id"], expected["from_id"])
            self.assertEqual(path["length_km"], expected["distance_km"])
            self.assertEqual(reverse["length_km"], expected["distance_km"])
            self.assertEqual([e["id"] for e in path["segments"]], expected["edge_ids"])

    def test_waypoints_are_not_delivery_terminals(self):
        network = load_network()
        path = shortest_path(network, "nizhneyansk", "adycha-mouth")
        route = dict(
            origin=path["origin"],
            destination=path["destination"],
            length_km=path["length_km"],
            graph_path=path,
        )
        with self.assertRaisesRegex(InputError, "Промежуточный"):
            validate_route_graph(route)
        path = shortest_path(network, "nizhneyansk", "batagay")
        route = dict(
            origin=path["origin"],
            destination=path["destination"],
            length_km=path["length_km"],
            graph_path=path,
        )
        validate_route_graph(route)

    def test_unknown_distances_or_unreferenced_edges_are_rejected(self):
        for field, value in [
            ("distance_km", None),
            ("source_id", "missing"),
            ("topology", "one_way"),
        ]:
            raw = copy.deepcopy(self.raw)
            raw["edges"][0][field] = value
            with self.subTest(field=field), self.assertRaises(InputError):
                import_yana_network(raw)

    def test_weather_aliases_are_explicit_and_not_permission_for_automatic_use(self):
        bindings = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "src/yana_planner/static/data/weather-point-bindings.json"
            ).read_text()
        )
        by_id = {b["node_id"]: b for b in bindings["bindings"]}
        self.assertEqual(by_id["ust-kuyga"]["forecast_point_id"], "ust_kuyga")
        self.assertIsNone(by_id["batagay"]["forecast_point_id"])
        self.assertTrue(all(b["automatic_use"] is False for b in by_id.values()))
        self.assertTrue(
            all(n["weather_coordinate_usable"] is False for n in load_network()["nodes"])
        )

    def test_api_defaults_to_reference_network_and_rejects_unknown_catalog(self):
        client = create_app({"TESTING": True}).test_client()
        self.assertEqual(client.get("/api/river-network").json["id"], "yana-reference-2026-v1")
        response = client.post(
            "/api/river-path", json=dict(origin_id="nizhneyansk", destination_id="batagay")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["length_km"], 707)
        self.assertEqual(response.json["distance_status"], "documented")
        self.assertEqual(client.get("/api/river-network?network=missing").status_code, 400)
        self.assertEqual(
            client.post(
                "/api/river-path", json=dict(network=[], origin_id="a", destination_id="c")
            ).status_code,
            400,
        )

    def test_reference_path_survives_calculation_with_sources_and_manual_times(self):
        from test_route_web_integration import package as tiny_package

        package = tiny_package()
        graph = shortest_path(load_network(), "nizhneyansk", "batagay")
        package["route"] = dict(
            id="graph-yana",
            origin=graph["origin"],
            destination=graph["destination"],
            length_km=707,
            graph_path=graph,
        )
        package["sources"] = graph["network_snapshot"]["sources"]
        package["facts"] = [
            dict(field="route.length_km", value=707, status="documented", source_id="S1")
        ]
        before = copy.deepcopy(package["scenario"]["vessels"])
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(
                dict(TESTING=True, DATABASE=str(Path(tmp) / "runs.db"))
            ).test_client()
            result = client.post(
                "/", data=dict(route_package_json=json.dumps(package), search_seconds="2")
            )
            self.assertEqual(result.status_code, 303, result.text)
            saved = client.get(result.location + "/results.json").json
            self.assertEqual(saved["inputs"]["route_package"]["scenario"]["vessels"], before)
            self.assertEqual(saved["inputs"]["route_package"]["route"]["graph_path"], graph)
            report = client.get(result.location + "/report.html").text
            self.assertIn("Северный", report)
            self.assertIn("Устье Адычи", report)
            self.assertIn("707", report)
            summary = client.get(result.location + "/summary.html?download=1").text
            self.assertIn("707", summary)
            self.assertIn("Время движения задано вручную", summary)
            self.assertIn("Устье Адычи", summary)
