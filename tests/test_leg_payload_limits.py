import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from yana_planner.domain import InputError
from yana_planner.multi_stop import check_voyage, normalize, plan_multi_stop
from yana_planner.service import ROOT
from yana_planner.web import create_app


def scenario():
    return json.loads((ROOT / "examples/multi_stop/two-recipients.json").read_text())


def limit(leg="leg1", cap=600):
    return dict(leg_id=leg, max_onboard_t=cap, status="scenario_assumption", source="Учебный лимит")


class LegPayloadLimitsTests(unittest.TestCase):
    def test_boundary_and_zero_block_before_solver_without_reducing_deliveries(self):
        for cap in (599, 0):
            with self.subTest(cap=cap):
                cfg = scenario()
                cfg["leg_payload_limits"] = [limit(cap=cap)]
                with patch(
                    "yana_planner.multi_stop.milp", side_effect=AssertionError("Must precheck")
                ):
                    r = plan_multi_stop(cfg)
                self.assertEqual(r["reason_code"], "leg_payload_limit_exceeded")
                self.assertFalse(r["goal_met"])
                self.assertIsNone(r["plan"])
                self.assertEqual(r["solver"], [])
                self.assertEqual(sum(s["required_t"] for s in r["input"]["stops"]), 600)
        cfg["leg_payload_limits"] = [limit(cap=600)]
        self.assertTrue(plan_multi_stop(cfg)["goal_met"])

    def test_second_leg_uses_remaining_cargo_and_no_limit_keeps_old_plan(self):
        base = plan_multi_stop(scenario())
        self.assertTrue(base["goal_met"])
        self.assertEqual(base["departure_checks"], [])
        cfg = scenario()
        cfg["leg_payload_limits"] = [limit("leg2", 400)]
        r = plan_multi_stop(cfg)
        self.assertTrue(r["goal_met"])
        self.assertEqual(r["plan"]["deliveries"], base["plan"]["deliveries"])
        self.assertEqual(r["departure_checks"][0]["onboard_t"], 400)
        cfg["leg_payload_limits"][0]["max_onboard_t"] = 399
        self.assertFalse(plan_multi_stop(cfg)["goal_met"])

    def test_independent_checker_rejects_existing_schedule_under_tighter_limit(self):
        cfg = scenario()
        starts = plan_multi_stop(cfg)["plan"]["starts"]
        cfg["leg_payload_limits"] = [limit("leg2", 399)]
        verdict = check_voyage(cfg, starts)
        self.assertEqual(verdict["status"], "invalid_schedule")
        self.assertTrue(any("400" in e and "399" in e for e in verdict["errors"]))

    def test_malformed_limits_rejected(self):
        invalid = [None, {}, [None], [limit(), limit()], [limit("return")], [limit("leg3")]]
        for key, value in [
            ("max_onboard_t", None),
            ("max_onboard_t", True),
            ("max_onboard_t", -1),
            ("max_onboard_t", 1.5),
            ("status", "verified"),
            ("source", ""),
            ("source", None),
        ]:
            row = limit()
            row[key] = value
            invalid.append([row])
        invalid.append([dict(limit(), extra=True)])
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(InputError):
                cfg = scenario()
                cfg["leg_payload_limits"] = value
                normalize(cfg)

    def test_api_preserves_failed_and_passed_checks_in_history_and_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(
                dict(TESTING=True, DATABASE=str(Path(tmp) / "runs.db"))
            ).test_client()
            for cap, passed in [(500, False), (600, True)]:
                cfg = scenario()
                cfg["leg_payload_limits"] = [limit(cap=cap)]
                p = dict(scenario=cfg, search_seconds=2)
                response = client.post("/api/multi-stop/plan", json=p)
                self.assertEqual(response.status_code, 201, response.text)
                data = response.json
                self.assertEqual(data["result"]["goal_met"], passed)
                self.assertEqual(
                    data["inputs"]["scenario"]["leg_payload_limits"], cfg["leg_payload_limits"]
                )
                self.assertEqual(data["result"]["departure_checks"][0]["passed"], passed)
                with patch(
                    "yana_planner.multi_stop_web.calculate_multi_stop_with_timeout",
                    side_effect=AssertionError("GET must not solve"),
                ):
                    saved = client.get(data["links"]["json"]).json
                    self.assertEqual(saved["result"], data["result"])
                    page = client.get(data["links"]["page"]).text
                    self.assertIn("Проверка груза перед отправлением", page)
                    self.assertIn("600 т", page)
                bad = deepcopy(p)
                bad["scenario"]["leg_payload_limits"][0]["max_onboard_t"] = None
                self.assertEqual(client.post("/api/multi-stop/plan", json=bad).status_code, 400)
