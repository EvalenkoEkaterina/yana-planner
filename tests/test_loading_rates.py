"""Cargo-dependent loading: exact hourly bins and independent execution checks."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import MultiDict

from yana_planner.domain import InputError, Scenario, loading_hours
from yana_planner.economics import cost
from yana_planner.planning import candidates, compare, optimize
from yana_planner.replanning import run_replanning
from yana_planner.service import custom_conditions, form_defaults
from yana_planner.validation import check_schedule
from yana_planner.web import create_app

ROOT = Path(__file__).resolve().parents[1]


def loading_scenario(target=500):
    cfg = json.loads((ROOT / "examples/economic-choice.json").read_text())
    cfg.update(
        id="LOADING-RATE-TEST",
        target_t=target,
        initial_source_t=target,
        destination_capacity_t=target,
        return_cutoff_h=25,
        delivery_deadline_h=20,
        payload_calendar=[dict(from_h=0, capacity_t=500)],
    )
    cfg["vessels"] = [
        dict(
            id="A",
            available_h=0,
            capacity_t=500,
            load_rate_tph=50,
            load_setup_h=1,
            up_h=1,
            unload_h=1,
            return_h=1,
            prep_h=1,
        )
    ]
    return cfg


class LoadingRateTests(unittest.TestCase):
    def test_20_and_500_tonnes_have_different_loading_and_cost(self):
        cfg = loading_scenario()
        terms = dict(
            billing_mode="all_available_time",
            horizon_h=25,
            separate_trip_operations_rub=0,
            active_vessel_hour_rub=100,
            waiting_vessel_hour_rub=0,
        )
        for quantity, load_h, receipt, active, money in [
            (20, 2, 4, 6, 600),
            (500, 11, 13, 15, 1500),
        ]:
            with self.subTest(quantity=quantity):
                plan = [dict(vessel_id="A", start_h=0, quantity_t=quantity)]
                verdict = check_schedule(cfg, plan, 20)
                self.assertEqual(verdict["status"], "valid_simulation")
                self.assertEqual(verdict["trips"][0]["load_end_h"], load_h)
                self.assertEqual(verdict["last_receipt_h"], receipt)
                self.assertEqual(verdict["busy_vessel_h"], active)
                self.assertEqual(cost(cfg, plan, 20, terms)["total_rub"], money)

    def test_both_planners_use_the_actual_tonnage(self):
        for quantity, load_h in [(20, 2), (500, 11)]:
            with self.subTest(quantity=quantity):
                result = compare(loading_scenario(quantity), 20, time_limit=2)
                for name in ["baseline", "planner"]:
                    row = result[name]
                    self.assertEqual(row["accepted_t"], quantity)
                    self.assertEqual(row["trip_count"], 1)
                    trip = row["trips"][0]
                    self.assertEqual(trip["load_end_h"] - trip["start_h"], load_h)
                self.assertTrue(all(stage["optimal"] for stage in result["solver"]))

    def test_exact_rate_boundary_and_fractional_speed(self):
        vessel = dict(load_rate_tph=2.5, load_setup_h=1)
        expected = [2, 2, 3, 3, 3, 4, 4, 5, 5, 5]
        self.assertEqual([loading_hours(vessel, q) for q in range(1, 11)], expected)
        cfg = loading_scenario(10)
        cfg["vessels"][0].update(load_rate_tph=2.5, load_setup_h=1)
        result = compare(cfg, 12, time_limit=2)
        self.assertEqual(result["planner"]["trips"][0]["load_end_h"], 5)

    def test_all_candidate_quantities_match_independent_checker(self):
        cfg = loading_scenario(5)
        cfg["vessels"][0].update(capacity_t=5, load_rate_tph=2)
        cfg.update(
            return_cutoff_h=12,
            closures=[[6, 7]],
            payload_calendar=[dict(from_h=0, capacity_t=5), dict(from_h=4, capacity_t=3)],
        )
        expected = set()
        for start in range(11):
            for quantity in range(1, 6):
                plan = [dict(vessel_id="A", start_h=start, quantity_t=quantity)]
                if check_schedule(cfg, plan, 10)["status"] == "valid_simulation":
                    expected.add((start, quantity))
        generated = [
            (c["start_h"], q)
            for c in candidates(Scenario.from_dict(cfg).to_dict(), 10)
            for q in range(c["min_t"], c["cap"] + 1)
        ]
        self.assertEqual(set(generated), expected)
        self.assertEqual(len(generated), len(set(generated)))

    def test_deadline_limits_cargo_in_the_selected_duration_bin(self):
        cfg = loading_scenario(500)
        # One setup hour, four transfer hours, one travel, one receipt.
        cfg["vessels"][0]["prep_h"] = 20  # Prevent a second turn within this deadline.
        result = compare(cfg, 7, time_limit=2)
        self.assertEqual(result["planner"]["accepted_t"], 200)
        self.assertEqual(result["planner"]["last_receipt_h"], 7)
        trip = result["planner"]["trips"][0]
        self.assertEqual(trip["load_end_h"] - trip["start_h"], 5)

    def test_cost_objective_uses_candidate_duration_not_old_fixed_time(self):
        cfg = loading_scenario(5)
        cfg["vessels"][0].update(load_rate_tph=2, load_setup_h=0, load_h=99)
        cfg["vessels"].append(dict(cfg["vessels"][0], id="B", load_rate_tph=10))
        terms = dict(
            billing_mode="all_available_time",
            horizon_h=25,
            separate_trip_operations_rub=0,
            active_vessel_hour_rub=100,
            waiting_vessel_hour_rub=0,
        )
        result = compare(cfg, 12, strategy="cost", cost_terms=terms, time_limit=2, adaptive=True)
        self.assertEqual(result["planner"]["trip_count"], 1)
        self.assertEqual(result["planner"]["trips"][0]["vessel_id"], "B")
        self.assertEqual(result["costs"]["planner"]["total_rub"], 500)
        self.assertTrue(all(stage["optimal"] for stage in result["solver"]))

    def test_fixed_history_matches_correct_loading_bin(self):
        cfg = loading_scenario(100)
        fixed = [dict(vessel_id="A", start_h=0, quantity_t=20)]
        plan, logs = optimize(cfg, 20, fixed=fixed, decision_h=1, time_limit=2)
        self.assertIn(fixed[0], plan)
        verdict = check_schedule(cfg, plan, 20)
        self.assertEqual(verdict["accepted_t"], 100)
        self.assertEqual(verdict["trips"][0]["load_end_h"], 2)
        self.assertTrue(all(stage["optimal"] for stage in logs))

    def test_replanning_preserves_loading_of_started_trip(self):
        cfg = loading_scenario(100)
        cfg.update(initial_source_t=0, arrivals=[dict(h=0, t=20), dict(h=4, t=80)])
        event = dict(type="arrival_delay", announced_at_h=1, arrival_index=1, new_arrival_h=8)
        result = run_replanning(cfg, event, 20, time_limit=2)
        for key in ["revised_baseline", "revised_planner"]:
            revised = result[key]
            self.assertEqual(revised["result"]["status"], "valid_simulation")
            self.assertEqual(revised["result"]["accepted_t"], 100)
            for committed in revised["committed"]:
                self.assertIn(
                    committed, [{k: t[k] for k in committed} for t in revised["result"]["trips"]]
                )

    def test_invalid_rate_and_setup_are_rejected(self):
        for change in [
            dict(load_rate_tph=0),
            dict(load_rate_tph=-1),
            dict(load_rate_tph=True),
            dict(load_rate_tph=None),
            dict(load_rate_tph=float("nan")),
            dict(load_rate_tph=float("inf")),
            dict(load_setup_h=-1),
            dict(load_setup_h=0.5),
        ]:
            cfg = loading_scenario()
            cfg["vessels"][0].update(change)
            with self.subTest(change=change), self.assertRaises(InputError):
                Scenario.from_dict(cfg)

    def test_fixed_mode_and_explicit_zero_setup_are_supported(self):
        cfg = loading_scenario(20)
        vessel = cfg["vessels"][0]
        vessel["load_setup_h"] = 0
        self.assertEqual(loading_hours(Scenario.from_dict(cfg).to_dict()["vessels"][0], 20), 1)
        del vessel["load_rate_tph"]
        del vessel["load_setup_h"]
        vessel["load_h"] = 8
        self.assertEqual(loading_hours(Scenario.from_dict(cfg).to_dict()["vessels"][0], 20), 8)

    def test_large_loading_model_is_rejected_before_matrix_build(self):
        cfg = Scenario.from_dict(loading_scenario()).to_dict()
        with patch("yana_planner.planning.MAX_LOADING_CANDIDATES", 2):
            with self.assertRaisesRegex(InputError, "Слишком много"):
                candidates(cfg, 20)

    def test_web_parses_optional_fields_without_changing_old_presets(self):
        original = json.loads((ROOT / "examples/r03.json").read_text())
        cfg, _ = custom_conditions(form_defaults(), with_delay=False)
        self.assertEqual(cfg, Scenario.from_dict(original).to_dict())
        values = form_defaults()
        values.update(vessel_load_rate_tph=["50", "25,5"], vessel_load_setup_h=["1", "0"])
        cfg, _ = custom_conditions(values, with_delay=False)
        self.assertEqual(cfg["vessels"][0]["load_rate_tph"], 50)
        self.assertEqual(cfg["vessels"][1]["load_rate_tph"], 25.5)
        self.assertEqual(cfg["vessels"][0]["load_setup_h"], 1)
        values["vessel_load_h"] = ["", ""]
        cfg, _ = custom_conditions(values, with_delay=False)
        self.assertNotIn("load_h", cfg["vessels"][0])
        values.pop("vessel_load_setup_h")
        cfg, _ = custom_conditions(values, with_delay=False)
        self.assertEqual(cfg["vessels"][0]["load_setup_h"], 0)

    def test_bad_web_loading_fields_stop_before_solver(self):
        for rates, setup in [
            (["0", ""], ["0", "0"]),
            (["nan", ""], ["0", "0"]),
            (["50"], ["0", "0"]),
            (["", ""], ["2", "0"]),
        ]:
            values = form_defaults()
            values.update(vessel_load_rate_tph=rates, vessel_load_setup_h=setup)
            with self.subTest(rates=rates, setup=setup), self.assertRaises(InputError):
                custom_conditions(values, with_delay=False)

    def test_web_report_saves_speed_and_calculated_duration(self):
        values = form_defaults()
        values.update(
            customize=["1"],
            target_t=["20"],
            vessel_load_rate_tph=["50", ""],
            vessel_load_setup_h=["1", "0"],
            search_seconds=["2"],
        )
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(
                dict(TESTING=True, DATABASE=str(Path(directory) / "runs.db"))
            ).test_client()
            page = client.get("/").text
            self.assertIn('name="vessel_load_rate_tph"', page)
            response = client.post(
                "/", data=MultiDict((k, v) for k, vs in values.items() for v in vs)
            )
            self.assertEqual(response.status_code, 303)
            saved = client.get(response.location + "/results.json").json
            self.assertEqual(saved["inputs"]["scenario"]["vessels"][0]["load_rate_tph"], 50)
            trip = saved["result"]["planner"]["trips"][0]
            self.assertEqual(trip["load_end_h"] - trip["start_h"], 2)
            self.assertIn("объём / 50", client.get(response.location).text)
            self.assertIn("округление вверх", client.get(response.location + "/report.html").text)

    def test_inputs_remain_unchanged(self):
        cfg = loading_scenario(20)
        before = copy.deepcopy(cfg)
        compare(cfg, 20, time_limit=2)
        self.assertEqual(cfg, before)


if __name__ == "__main__":
    unittest.main()
