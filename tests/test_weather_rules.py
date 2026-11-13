import json
import unittest
from copy import deepcopy
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.weather import apply_weather_event
from yana_planner.weather_rules import event_from_rule, validate_rule

ROOT = Path(__file__).resolve().parents[1]


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.rule = json.loads((ROOT / "examples/weather_rules/yana_wind.json").read_text())
        self.records = [
            json.loads((ROOT / "examples/weather" / n).read_text())
            for n in ("nizhneyansk.json", "ust_kuyga.json")
        ]

    def event(self, rule=None, records=None, route=None):
        return event_from_rule(
            self.records if records is None else records,
            self.rule if rule is None else rule,
            route_id=route or self.rule["route_id"],
            t0="2026-08-10T08:00:00+10:00",
            horizon_h=492,
            announced_at_h=60,
        )

    def test_configured_threshold_and_provenance(self):
        original = deepcopy(self.rule)
        event = self.event()
        self.assertEqual(event["threshold_ms"], 10)
        self.assertEqual(event["scope"], self.rule["scope"])
        self.assertEqual(self.rule, original)
        self.rule["threshold"] = 12
        self.assertEqual(self.event()["closures"], [[435, 449]])

    def test_other_route_rejected(self):
        with self.assertRaises(InputError):
            self.event(route="other")

    def test_missing_and_extra_points_rejected(self):
        for records in (self.records[:1], self.records + [self.records[0]]):
            with self.assertRaises(InputError):
                self.event(records=records)

    def test_unsupported_effects_and_units_rejected(self):
        for key, value in [
            ("effect", "loading_closure"),
            ("metric", "visibility"),
            ("unit", "km/h"),
            ("operator", "<"),
        ]:
            rule = deepcopy(self.rule)
            rule[key] = value
            with self.assertRaises(InputError):
                validate_rule(rule)

    def test_local_scope_not_silently_expanded(self):
        for key, value in [
            ("location", "segment-1"),
            ("vessels", ["SIM-A"]),
            ("operations", ["loaded_movement"]),
        ]:
            rule = deepcopy(self.rule)
            rule["scope"][key] = value
            with self.assertRaises(InputError):
                validate_rule(rule)

    def test_unknown_field_rejected(self):
        self.rule["threshhold"] = 5
        with self.assertRaises(InputError):
            validate_rule(self.rule)

    def test_adapter_checks_scenario_route(self):
        cfg = json.loads((ROOT / "examples/r03.json").read_text())
        cfg.update(t0="2026-08-10T08:00:00+10:00", route_id=self.rule["route_id"])
        event = self.event()
        changed = apply_weather_event(cfg, event, at_h=60)
        self.assertEqual(changed["closures"], event["closures"])
        cfg["route_id"] = "wrong"
        with self.assertRaises(InputError):
            apply_weather_event(cfg, event, at_h=60)

    def test_new_route_uses_configuration(self):
        self.rule["route_id"] = "another-route"
        self.rule["id"] = "another-rule"
        # Test portability only: reused fixtures are not evidence for another place.
        self.assertEqual(self.event()["route_id"], "another-route")
