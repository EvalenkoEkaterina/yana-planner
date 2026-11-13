import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.service import ROOT
from yana_planner.web import create_app
from yana_planner.web_worker import CalculationTimeout


def payload():
    return dict(
        schema_version="yana-multi-stop-request/1",
        scenario=json.loads((ROOT / "examples/multi_stop/four-recipients.json").read_text()),
        cost_terms=json.loads(
            (ROOT / "examples/multi_stop/four-recipients.costs.json").read_text()
        ),
        search_seconds=2,
    )


class MultiStopApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = dict(TESTING=True, DATABASE=str(Path(self.tmp.name) / "runs.db"))
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def test_real_worker_and_saved_snapshot_survive_refresh_and_restart(self):
        p = payload()
        response = self.client.post("/api/multi-stop/plan", json=p)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json
        self.assertEqual(
            data["result"]["input_sha256"],
            hashlib.sha256(
                json.dumps(
                    data["inputs"], sort_keys=True, ensure_ascii=False, allow_nan=False
                ).encode()
            ).hexdigest(),
        )
        self.assertTrue(data["result"]["goal_met"])
        self.assertEqual(data["result"]["costs"]["status"], "missing_cost_inputs")
        self.assertIsNone(data["result"]["costs"]["total_rub"])
        self.assertEqual(data["inputs"]["scenario"]["stops"], p["scenario"]["stops"])
        with patch(
            "yana_planner.multi_stop_web.calculate_multi_stop_with_timeout",
            side_effect=AssertionError("GET must not solve"),
        ):
            for _ in range(2):
                saved = self.client.get(data["links"]["api"]).json
                self.assertEqual(saved["result"], data["result"])
                self.assertEqual(self.client.get(data["links"]["page"]).status_code, 200)
            download = self.client.get(data["links"]["json"])
            self.assertIn("attachment", download.headers["Content-Disposition"])
            self.assertEqual(download.json["inputs"], data["inputs"])
            history = self.client.get("/api/multi-stop/runs").json["items"]
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["id"], data["id"])
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.get(data["links"]["api"]).json["result"], data["result"])
        self.assertEqual(self.client.get("/history").status_code, 200)
        self.assertEqual(self.client.get("/multi-stop/history").status_code, 200)

    def test_infeasibility_is_saved_result_not_bad_input(self):
        p = payload()
        p["scenario"]["vessel"]["capacity_t"] = 599
        r = self.client.post("/api/multi-stop/plan", json=p)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json["result"]["status"], "infeasible")
        self.assertFalse(r.json["result"]["goal_met"])
        self.assertIsNone(r.json["result"]["plan"])
        self.assertEqual(r.json["result"]["costs"]["status"], "no_valid_plan")

    def test_bad_input_is_echoed_without_saving(self):
        p = payload()
        p["scenario"]["receipt_priority"] = ["missing"]
        r = self.client.post("/api/multi-stop/plan", json=p)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json["submitted_request"], p)
        self.assertEqual(self.client.get("/api/multi-stop/runs").json["items"], [])
        for changed in [dict(p, unknown=True), dict(p, search_seconds=True)]:
            self.assertEqual(
                self.client.post("/api/multi-stop/plan", json=changed).status_code, 400
            )
        p = payload()
        p["scenario"]["event"] = {"type": "unsupported"}
        self.assertEqual(self.client.post("/api/multi-stop/plan", json=p).status_code, 400)
        p = payload()
        p["scenario"]["vessel"]["capacity_t"] = 599
        p["cost_terms"]["billing_mode"] = "invalid"
        self.assertEqual(self.client.post("/api/multi-stop/plan", json=p).status_code, 400)

    def test_timeout_preserves_request_and_does_not_claim_infeasibility(self):
        p = payload()
        with patch(
            "yana_planner.multi_stop_web.calculate_multi_stop_with_timeout",
            side_effect=CalculationTimeout("Лимит времени"),
        ):
            r = self.client.post("/api/multi-stop/plan", json=p)
        self.assertEqual(r.status_code, 408)
        self.assertEqual(r.json["code"], "calculation_timeout")
        self.assertEqual(r.json["submitted_request"], p)
        self.assertIsNone(r.json["result"])
        self.assertEqual(self.client.get("/api/multi-stop/runs").json["items"], [])
        # Exercise the actual process timeout as well, rather than only the API mock.
        self.app.config["CALCULATION_TIMEOUT_S"] = 0.001
        r = self.client.post("/api/multi-stop/plan", json=p)
        self.assertEqual(r.status_code, 408, r.text)

    def test_limits_missing_runs_and_nonfinite_json(self):
        self.assertEqual(self.client.get("/api/multi-stop/runs/999").status_code, 404)
        self.assertEqual(self.client.get("/api/multi-stop/runs/999").json["code"], "not_found")
        for limit in ("0", "101", "bad"):
            self.assertEqual(
                self.client.get("/api/multi-stop/runs?limit=" + limit).status_code, 400
            )
        self.assertEqual(
            self.client.post(
                "/api/multi-stop/plan", data="broken", content_type="application/json"
            ).status_code,
            400,
        )
        p = payload()
        p["scenario"]["initial_source_t"] = float("nan")
        r = self.client.post("/api/multi-stop/plan", json=p)
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(r.json["submitted_request"])
        self.app.config["MAX_CONTENT_LENGTH"] = 10
        self.assertEqual(self.client.post("/api/multi-stop/plan", json=payload()).status_code, 413)

    def test_fallback_report_escapes_user_names(self):
        p = payload()
        old = p["scenario"]["stops"][0]["id"]
        new = "<script>alert(1)</script>"
        p["scenario"]["stops"][0]["id"] = new
        p["cost_terms"]["stop_fees_rub"][new] = p["cost_terms"]["stop_fees_rub"].pop(old)
        r = self.client.post("/api/multi-stop/plan", json=p)
        self.assertEqual(r.status_code, 201, r.text)
        page = self.client.get(r.json["links"]["page"]).text
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
