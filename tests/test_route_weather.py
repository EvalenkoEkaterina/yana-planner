import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.river_graph import load_network, shortest_path
from yana_planner.route_weather import assess_route_weather
from yana_planner.service import ROOT
from yana_planner.weather_feed import mapped_points, normalize


class RouteWeatherTests(unittest.TestCase):
    def path(self, destination="ust-kuyga"):
        return shortest_path(load_network(), "nizhneyansk", destination)

    def test_exact_route_and_real_hours_have_no_exceedances(self):
        result = assess_route_weather(self.path(), t0="2025-07-01T00:00:00+00:00", horizon_h=120)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["closures"], [])
        self.assertEqual(len(result["hourly_max_wind_ms"]), 120)
        self.assertFalse(result["automatic_operational_use"])
        self.assertIsNone(result["forecast_issued_at"])

    def test_other_route_or_missing_hours_cannot_look_like_clear_weather(self):
        other = assess_route_weather(
            self.path("batagay"), t0="2025-07-01T00:00:00+00:00", horizon_h=120
        )
        self.assertEqual(other["status"], "unsupported_weather_route")
        self.assertIsNone(other["closures"])
        missing = assess_route_weather(self.path(), t0="2025-07-01T00:00:00+00:00", horizon_h=121)
        self.assertEqual(missing["status"], "insufficient_weather_data")
        self.assertIsNone(missing["closures"])
        blocked = assess_route_weather(
            self.path(),
            t0="2025-07-01T00:00:00+00:00",
            horizon_h=120,
            mode="as_of_decision",
            decision_at="2025-07-01T00:00:00+00:00",
        )
        self.assertEqual(blocked["status"], "insufficient_weather_data")

    def test_invalid_records_and_string_mapping_raise_input_errors(self):
        raw = json.loads((ROOT / "examples/weather_feed/input.json").read_text())
        for invalid in ([{}], None):
            with self.assertRaises(InputError):
                normalize(invalid)
        for value in (True, "bad", float("nan"), 91):
            invalid = deepcopy(raw[:1])
            invalid[0]["point"]["latitude"] = value
            with self.subTest(value=value), self.assertRaises(InputError):
                normalize(invalid)
        entry = dict(
            segments={
                "x": dict(
                    status="confirmed",
                    point_ids="nizhneyansk",
                    method="proxy",
                    limitations="scenario",
                )
            }
        )
        with self.assertRaises(InputError):
            mapped_points(entry, ["x"])

    def test_one_check_reaches_solver_and_writes_reproducible_report(self):
        spec = importlib.util.spec_from_file_location(
            "graph_weather_check", ROOT / "scripts/check_graph_weather.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            inputs, result, weather = module.run(tmp)
            self.assertEqual(inputs["route_package"]["route"]["length_km"], 324)
            self.assertIsNone(inputs["event"])
            self.assertEqual(result["baseline"]["accepted_t"], 400)
            self.assertEqual(result["planner"]["accepted_t"], 400)
            self.assertEqual(weather["closures"], [])
            self.assertIn("не оперативный опыт", json.dumps(inputs, ensure_ascii=False))
            self.assertIn("Время выпуска неизвестно", (Path(tmp) / "report.html").read_text())
