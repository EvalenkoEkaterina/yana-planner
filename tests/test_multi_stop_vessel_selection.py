import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from yana_planner.domain import InputError
from yana_planner.multi_stop_service import calculate_multi_stop_request
from yana_planner.multi_stop_vessel_selection import calculate_vessel_selection
from yana_planner.service import ROOT
from yana_planner.web import create_app


def payload(rule="earliest_receipts"):
    cfg = json.loads((ROOT / "examples/multi_stop/two-recipients.json").read_text())
    cfg["vessel"]["travel_h"] = [cfg["vessel"].pop("leg1_h"), cfg["vessel"].pop("leg2_h")]
    candidates = []
    for ident, available, rate in [("FAST", 0, 2000), ("ECONOMY", 20, 1000)]:
        vessel = dict(cfg["vessel"], id=ident, available_h=available)
        costs = dict(
            billing_mode="all_available_time",
            currency="RUB",
            horizon_h=160,
            vessel_id=ident,
            rate_basis="scenario",
            source="Учебные ставки",
            rates=dict(
                separate_trip_operations_rub=25000,
                active_vessel_hour_rub=rate,
                waiting_vessel_hour_rub=500,
            ),
            stop_fees_excluded_from_other_rates=True,
            stop_fees_rub={s["id"]: 0 for s in cfg["stops"]},
        )
        candidates.append(dict(vessel=vessel, cost_terms=costs))
    return dict(
        scenario=cfg,
        cost_terms=None,
        vessel_candidates=candidates,
        selection_rule=rule,
        search_seconds=2,
    )


class VesselSelectionTests(unittest.TestCase):
    def test_priority_and_cost_choose_different_vessels(self):
        for rule, expected in [("earliest_receipts", "FAST"), ("lowest_cost", "ECONOMY")]:
            with self.subTest(rule=rule):
                p = payload(rule)
                original = deepcopy(p)
                inputs, r = calculate_multi_stop_request(p)
                self.assertEqual(p, original)
                self.assertTrue(r["goal_met"])
                self.assertEqual(r["vessel_selection"]["recommended_vessel_id"], expected)
                self.assertTrue(r["vessel_selection"]["selection_proven"])
                self.assertEqual(r["input"]["vessel"]["id"], expected)
                self.assertEqual(inputs["scenario"]["stops"], p["scenario"]["stops"])

    def test_capacity_excludes_candidate_without_reducing_deliveries(self):
        p = payload()
        p["vessel_candidates"][0]["vessel"]["capacity_t"] = 599
        _, r = calculate_multi_stop_request(p)
        self.assertEqual(r["vessel_selection"]["recommended_vessel_id"], "ECONOMY")
        self.assertEqual(r["plan"]["loaded_t"], 600)
        self.assertEqual(r["vessel_selection"]["candidate_results"][0]["status"], "infeasible")

    def test_missing_costs_do_not_make_unknown_candidate_free_or_most_expensive(self):
        p = payload("lowest_cost")
        p["vessel_candidates"][1]["cost_terms"]["rates"]["active_vessel_hour_rub"] = None
        _, r = calculate_multi_stop_request(p)
        self.assertTrue(r["goal_met"])
        self.assertEqual(r["vessel_selection"]["status"], "missing_cost_inputs")
        self.assertIsNone(r["vessel_selection"]["recommended_vessel_id"])
        self.assertFalse(r["vessel_selection"]["selection_proven"])

    def test_all_infeasible_and_unfinished_candidate_are_distinct(self):
        p = payload()
        for c in p["vessel_candidates"]:
            c["vessel"]["capacity_t"] = 599
        inputs, r = calculate_multi_stop_request(p)
        self.assertEqual(r["vessel_selection"]["status"], "all_infeasible")
        self.assertIsNone(r["plan"])
        unfinished = deepcopy(r["vessel_selection"]["candidate_results"][1])
        unfinished["status"] = "no_feasible_solution_found"
        with patch(
            "yana_planner.multi_stop_service.calculate_multi_stop_request",
            side_effect=[
                (inputs, r["vessel_selection"]["candidate_results"][0]),
                (inputs, unfinished),
            ],
        ):
            _, result = calculate_vessel_selection(p)
        self.assertEqual(result["status"], "no_feasible_solution_found")
        self.assertEqual(result["vessel_selection"]["status"], "no_plan_found")
        self.assertFalse(result["vessel_selection"]["selection_proven"])

    def test_invalid_candidate_contract_rejected(self):
        for change in [
            lambda p: p.update(selection_rule="unknown"),
            lambda p: p.update(vessel_candidates=[]),
            lambda p: p.update(vessel_candidates=p["vessel_candidates"] * 3),
            lambda p: p["vessel_candidates"][1]["vessel"].update(id="FAST"),
            lambda p: p["vessel_candidates"][1]["cost_terms"].update(vessel_id="OTHER"),
            lambda p: p["vessel_candidates"][1]["cost_terms"].update(horizon_h=159),
        ]:
            p = payload()
            change(p)
            with self.assertRaises(InputError):
                calculate_multi_stop_request(p)

    def test_api_saves_all_candidates_and_chosen_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(
                dict(TESTING=True, DATABASE=str(Path(tmp) / "runs.db"))
            ).test_client()
            response = client.post("/api/multi-stop/plan", json=payload("lowest_cost"))
            self.assertEqual(response.status_code, 201, response.text)
            data = response.json
            self.assertEqual(data["result"]["vessel_selection"]["recommended_vessel_id"], "ECONOMY")
            with patch(
                "yana_planner.multi_stop_web.calculate_multi_stop_with_timeout",
                side_effect=AssertionError("GET must not solve"),
            ):
                saved = client.get(data["links"]["json"]).json
                self.assertEqual(saved["result"], data["result"])
                self.assertEqual(
                    saved["inputs"]["vessel_candidates"], payload()["vessel_candidates"]
                )
