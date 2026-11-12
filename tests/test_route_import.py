"""Import failures must preserve unknowns and never change the caller's data."""

import copy
import json
import unittest
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.route_import import RATE_FIELDS, import_route_package

ROOT = Path(__file__).resolve().parents[1]


class RouteImportTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads((ROOT / "examples/routes/ust-kuyga-batagay.package.json").read_text())

    def test_package_and_independent_ids(self):
        before = copy.deepcopy(self.raw)
        got = import_route_package(self.raw)
        self.assertEqual(got["route"]["length_km"], 383)
        self.assertEqual(self.raw, before)
        got["scenario"]["vessels"][0]["id"] = "CHANGED"
        self.assertNotEqual(got, self.raw)
        self.assertTrue(all(v["id"].startswith("UKB-") for v in self.raw["scenario"]["vessels"]))

    def test_unknown_stays_null_and_zero_is_explicit(self):
        rates = self.raw["cost_terms"]["vessel_rates"]
        for rate in rates.values():
            for f in RATE_FIELDS:
                rate[f] = 0
        self.raw["strategy"] = "cost"
        self.assertEqual(import_route_package(self.raw)["cost_terms"]["vessel_rates"], rates)
        self.raw["facts"][-1]["value"] = 0
        with self.assertRaisesRegex(InputError, "null"):
            import_route_package(self.raw)

    def test_missing_rate_allowed_for_trips_but_not_cost(self):
        self.raw["cost_terms"]["vessel_rates"]["UKB-SIM-A"][RATE_FIELDS[0]] = None
        self.assertIsNone(
            import_route_package(self.raw)["cost_terms"]["vessel_rates"]["UKB-SIM-A"][
                RATE_FIELDS[0]
            ]
        )
        self.raw["strategy"] = "cost"
        with self.assertRaisesRegex(InputError, "все ставки"):
            import_route_package(self.raw)

    def test_documented_needs_source(self):
        rate = self.raw["cost_terms"]["vessel_rates"]["UKB-SIM-A"]
        rate.update(rate_basis="documented", source="")
        with self.assertRaisesRegex(InputError, "source"):
            import_route_package(self.raw)

    def test_money_types_and_precision(self):
        for value in [-1, True, float("nan"), float("inf"), "100", 0.001]:
            with self.subTest(value=value):
                raw = copy.deepcopy(self.raw)
                raw["cost_terms"]["vessel_rates"]["UKB-SIM-A"][RATE_FIELDS[0]] = value
                with self.assertRaises(InputError):
                    import_route_package(raw)

    def test_unknown_vessel_and_source_are_rejected(self):
        self.raw["cost_terms"]["vessel_rates"]["UNKNOWN"] = {}
        with self.assertRaisesRegex(InputError, "ID"):
            import_route_package(self.raw)
        self.raw["cost_terms"]["vessel_rates"].pop("UNKNOWN")
        self.raw["facts"][0]["source_id"] = "missing"
        with self.assertRaisesRegex(InputError, "source_id"):
            import_route_package(self.raw)

    def test_weather_history_and_forecast_release(self):
        event = json.loads((ROOT / "examples/routes/weather.synthetic.json").read_text())
        self.raw["event"] = event
        import_route_package(self.raw)
        event["closures"] = [[59, 70]]
        with self.assertRaisesRegex(InputError, "closures"):
            import_route_package(self.raw)
        event.update(
            closures=[[120, 132]],
            data_kind="forecast",
            forecast_issued_at="2026-08-13T21:00:00+09:00",
        )
        with self.assertRaisesRegex(InputError, "после объявления"):
            import_route_package(self.raw)
        event["forecast_issued_at"] = "2026-08-11T08:00:00+09:00"
        import_route_package(self.raw)

    def test_bad_shapes_and_deadline(self):
        for key, value in [("route", []), ("scenario", None), ("sources", []), ("facts", {})]:
            with self.subTest(key=key):
                raw = copy.deepcopy(self.raw)
                raw[key] = value
                with self.assertRaises(InputError):
                    import_route_package(raw)
        for bad in [None, 5, [None], {}]:
            raw = copy.deepcopy(self.raw)
            raw["scenario"]["vessels"] = bad
            with self.assertRaises(InputError):
                import_route_package(raw)
        self.raw["deadline_h"] = 493
        with self.assertRaisesRegex(InputError, "deadline_h"):
            import_route_package(self.raw)

    def test_delay_index_follows_original_arrival_after_sorting(self):
        self.raw["scenario"]["arrivals"] = [{"h": 120, "t": 400}, {"h": 0, "t": 200}]
        self.raw["event"] = {
            "type": "arrival_delay",
            "announced_at_h": 60,
            "arrival_index": 0,
            "new_arrival_h": 168,
        }
        before = copy.deepcopy(self.raw)
        got = import_route_package(self.raw)
        self.assertEqual(got["event"]["arrival_index"], 1)
        selected = got["scenario"]["arrivals"][got["event"]["arrival_index"]]
        self.assertEqual(selected, {"h": 120, "t": 400})
        self.assertEqual(self.raw, before)
        self.assertEqual(import_route_package(got), got)

    def test_delay_index_sort_is_stable_for_equal_arrival_times(self):
        self.raw["scenario"]["arrivals"] = [
            {"h": 120, "t": 100},
            {"h": 120, "t": 200},
            {"h": 0, "t": 300},
        ]
        self.raw["event"] = {
            "type": "arrival_delay",
            "announced_at_h": 60,
            "arrival_index": 1,
            "new_arrival_h": 168,
        }
        got = import_route_package(self.raw)
        self.assertEqual(got["event"]["arrival_index"], 2)
        self.assertEqual(got["scenario"]["arrivals"][2]["t"], 200)

    def test_setup_without_rate_is_omitted_or_rejected(self):
        v = self.raw["scenario"]["vessels"][0]
        v["load_setup_h"] = 0
        got = import_route_package(self.raw)["scenario"]["vessels"][0]
        self.assertNotIn("load_setup_h", got)
        v["load_setup_h"] = 2
        with self.assertRaisesRegex(InputError, "только вместе со скоростью"):
            import_route_package(self.raw)

    def test_blank_fixed_load_is_omitted_when_rate_is_set(self):
        v = self.raw["scenario"]["vessels"][0]
        v.update(load_rate_tph=50, load_setup_h=1, load_h=None)
        got = import_route_package(self.raw)["scenario"]["vessels"][0]
        self.assertNotIn("load_h", got)
        self.assertEqual(got["load_setup_h"], 1)

    def test_loading_rate_and_distinct_setup(self):
        v = self.raw["scenario"]["vessels"][0]
        v.update(load_rate_tph=2.5, load_setup_h=1)
        imported = import_route_package(self.raw)["scenario"]["vessels"][0]
        self.assertEqual(imported["load_setup_h"], 1)
        self.assertEqual(imported["prep_h"], 6)
        v.pop("load_h")
        self.assertNotIn("load_h", import_route_package(self.raw)["scenario"]["vessels"][0])
        v["load_rate_tph"] = 0
        with self.assertRaises(InputError):
            import_route_package(self.raw)


if __name__ == "__main__":
    unittest.main()
