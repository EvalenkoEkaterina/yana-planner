import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.planning import ready
from yana_planner.weather import apply_weather_event, assess_weather, build_weather_event

ROOT = Path(__file__).resolve().parents[1]


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "examples/r03.json").read_text())
        self.event = dict(
            type="weather_closure",
            announced_at_h=60,
            closures=[[120, 132]],
            rule_status="scenario_assumption",
        )

    def record(self):
        start = datetime(2026, 8, 10)
        return dict(
            kind="reanalysis",
            url="https://example.test/weather",
            response=dict(
                utc_offset_seconds=0,
                hourly_units={"wind_speed_10m": "m/s"},
                hourly=dict(
                    time=[
                        (start + timedelta(hours=h)).isoformat(timespec="minutes") for h in range(5)
                    ],
                    wind_speed_10m=[10, 2, 10, 12, 2],
                ),
            ),
        )

    def build(self, record, **kwargs):
        return build_weather_event(
            [record],
            t0="2026-08-10T00:00:00+00:00",
            horizon_h=5,
            announced_at_h=0,
            threshold_ms=10,
            **kwargs,
        )

    def test_threshold_and_half_open_intervals(self):
        self.assertEqual(self.build(self.record())["closures"], [[2, 4]])

    def test_evidence_and_forecast_label(self):
        event = self.build(self.record())
        self.assertIsNone(event["forecast_issued_at"])
        self.assertEqual(event["evidence"][0]["peak_wind_ms"], 12)
        self.assertEqual(event["evidence"][0]["start_utc"], "2026-08-10T02:00:00+00:00")

    def test_different_t0_rejected(self):
        self.cfg["t0"] = "2026-08-10T08:00:00+10:00"
        self.event["t0"] = "2026-08-11T08:00:00+10:00"
        with self.assertRaises(InputError):
            apply_weather_event(self.cfg, self.event, at_h=60)

    def test_equivalent_timezone_offsets(self):
        self.cfg["t0"] = "2026-08-10T08:00:00+10:00"
        self.event["t0"] = "2026-08-09T22:00:00+00:00"
        self.assertEqual(
            apply_weather_event(self.cfg, self.event, at_h=60)["closures"], [[120, 132]]
        )

    def test_missing_is_not_calm(self):
        record = self.record()
        record["response"]["hourly"]["wind_speed_10m"][2] = None
        with self.assertRaises(InputError):
            self.build(record)

    def test_wrong_units_and_duplicate_time(self):
        for mutate in ("units", "time"):
            record = self.record()
            if mutate == "units":
                record["response"]["hourly_units"]["wind_speed_10m"] = "km/h"
            else:
                record["response"]["hourly"]["time"][1] = record["response"]["hourly"]["time"][0]
            with self.assertRaises(InputError):
                self.build(record)

    def test_timezone_required(self):
        with self.assertRaises(InputError):
            build_weather_event(
                [self.record()],
                t0="2026-08-10T00:00",
                horizon_h=5,
                announced_at_h=0,
                threshold_ms=10,
            )

    def test_no_future_leak_and_no_mutation(self):
        original = deepcopy(self.cfg)
        self.assertEqual(apply_weather_event(self.cfg, self.event, at_h=59), self.cfg)
        self.assertEqual(
            apply_weather_event(self.cfg, self.event, at_h=60)["closures"], [[120, 132]]
        )
        self.assertEqual(original, self.cfg)

    def test_merges_existing_closures(self):
        self.cfg["closures"] = [[110, 120], [130, 140]]
        self.assertEqual(
            apply_weather_event(self.cfg, self.event, at_h=60)["closures"], [[110, 140]]
        )

    def test_late_retroactive_closure_rejected(self):
        self.event["closures"] = [[60, 80]]
        with self.assertRaises(InputError):
            apply_weather_event(self.cfg, self.event, at_h=60)

    def test_started_voyage_requires_intervention(self):
        plan = ready(self.cfg, 328)
        self.event["closures"] = [[61, 62]]  # SIM-B already started at 48, moving 56..80
        result = assess_weather(self.cfg, plan, self.event, 328)
        self.assertEqual(result["action"], "requires_intervention")
        self.assertIsNone(result["result"]["accepted_t"])

    def test_unaffected_plan_kept(self):
        plan = ready(self.cfg, 328)
        self.event["closures"] = [[480, 490]]
        result = assess_weather(self.cfg, plan, self.event, 328)
        self.assertEqual(result["action"], "no_replan_required")

    def test_replan_preserves_history(self):
        plan = ready(self.cfg, 328)
        result = assess_weather(self.cfg, plan, self.event, 328)
        self.assertEqual(result["action"], "replanned")
        self.assertEqual(result["result"]["status"], "valid_simulation")
        actual = result["result"]["trips"]
        for fixed in result["committed"]:
            self.assertTrue(any(all(t[k] == v for k, v in fixed.items()) for t in actual))

    def test_saved_weather_covers_full_turnaround(self):
        records = [json.loads(p.read_text()) for p in (ROOT / "examples/weather").glob("*.json")]
        event = build_weather_event(
            records,
            t0="2026-08-10T08:00:00+10:00",
            horizon_h=492,
            announced_at_h=60,
            threshold_ms=10,
        )
        self.assertEqual(event["data_kind"], "reanalysis")
        self.assertTrue(event["closures"])


if __name__ == "__main__":
    unittest.main()
