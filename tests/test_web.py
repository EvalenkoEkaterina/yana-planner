import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.experiments import summarize
from yana_planner.service import calculate
from yana_planner.web import create_app


class ComparisonTests(unittest.TestCase):
    def test_interpretation(self):
        a = dict(
            status="valid_simulation", accepted_t=10, shortfall_t=0, trip_count=1, last_receipt_h=5
        )
        c = summarize(a, a, 10, [])
        self.assertTrue(c["equal_volume"])
        self.assertFalse(c["optimal"])
        b = dict(a, accepted_t=5, shortfall_t=5)
        self.assertFalse(summarize(a, b, 10, [])["equal_volume"])
        self.assertFalse(summarize(a, b, 10, [])["rows"]["MILP"]["target_met"])
        b = dict(a, status="invalid")
        self.assertIsNone(summarize(a, b, 10, [])["rows"]["MILP"]["accepted_t"])
        b = dict(a, last_receipt_h=None)
        self.assertIsNone(summarize(a, b, 10, [])["rows"]["MILP"]["last_receipt_h"])
        self.assertTrue(summarize(a, a, 10, [{"optimal": True}])["optimal"])


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs, cls.result = calculate("r03", "328", time_limit=0.1)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = dict(TESTING=True, DATABASE=str(Path(self.tmp.name) / "runs.db"))
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_pages_and_persistence(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        with patch(
            "yana_planner.web.calculate",
            return_value=(copy.deepcopy(self.inputs), copy.deepcopy(self.result)),
        ) as solver:
            response = self.client.post("/", data=dict(scenario="r03", deadline="328"))
            self.assertEqual(response.status_code, 303)
            url = response.location
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(solver.call_count, 1)
        self.assertIn("R03", self.client.get("/history").text)
        self.assertEqual(self.client.get(url + "/report.html").status_code, 200)
        self.assertEqual(self.client.get(url + "/results.json").status_code, 200)
        self.assertEqual(create_app(self.config).test_client().get(url).status_code, 200)
        self.assertEqual(self.client.get("/runs/99999").status_code, 404)

    def test_bad_deadline_before_solver(self):
        with patch("yana_planner.service.compare") as solver:
            for value in ["", "0", "-1", "1.5", "493", "hello"]:
                self.assertEqual(
                    self.client.post("/", data=dict(scenario="r03", deadline=value)).status_code,
                    400,
                )
            solver.assert_not_called()

    def test_delay_history(self):
        inputs, result = calculate("delay", "328", time_limit=0.1)
        for key in ["revised_baseline", "revised_planner"]:
            revised = result[key]
            self.assertEqual(revised["result"]["status"], "valid_simulation")
            for trip in revised["committed"]:
                self.assertTrue(
                    any(all(t[k] == v for k, v in trip.items()) for t in revised["result"]["trips"])
                )
        self.assertIsNotNone(inputs["event"])


class CustomInputTests(unittest.TestCase):
    def conditions(self):
        from yana_planner.service import form_defaults

        v = form_defaults()
        v.update(
            customize=["1"],
            target_t=["600"],
            destination_capacity_t=["600"],
            return_cutoff_h=["50"],
            deadline=["30"],
            vessel_id=["TEST-600"],
            vessel_available_h=["0"],
            vessel_capacity_t=["600"],
            vessel_load_h=["2"],
            vessel_up_h=["4"],
            vessel_unload_h=["2"],
            vessel_return_h=["4"],
            vessel_prep_h=["2"],
            arrival_h=["0"],
            arrival_t=["600"],
        )
        return v

    def test_custom_capacity_not_capped_at_400(self):
        inputs, result = calculate("r03", "30", custom=self.conditions(), time_limit=0.1)
        self.assertEqual(inputs["scenario"]["id"], "CUSTOM")
        for key in ["baseline", "planner"]:
            self.assertEqual(result[key]["status"], "valid_simulation")
            self.assertEqual(result[key]["accepted_t"], 600)
            self.assertEqual(result[key]["trip_count"], 1)
            self.assertEqual(result[key]["trips"][0]["vessel_id"], "TEST-600")

    def test_delay_follows_selected_row_after_sorting(self):
        from yana_planner.service import custom_conditions

        v = self.conditions()
        v.update(
            arrival_h=["10", "0"],
            arrival_t=["300", "300"],
            delayed_arrival=["1"],
            announced_at_h=["5"],
            new_arrival_h=["16"],
        )
        cfg, event = custom_conditions(v, with_delay=True)
        self.assertEqual(event["arrival_index"], 1)
        self.assertEqual(cfg["arrivals"][event["arrival_index"]]["h"], 10)
        inputs, result = calculate("delay", "30", custom=v, time_limit=0.1)
        self.assertEqual(inputs["event"]["new_arrival_h"], 16)
        for key in ["revised_baseline", "revised_planner"]:
            revised = result[key]
            self.assertEqual(revised["result"]["status"], "valid_simulation")
            for trip in revised["committed"]:
                self.assertTrue(
                    any(all(t[k] == x for k, x in trip.items()) for t in revised["result"]["trips"])
                )

    def test_invalid_fields_rejected_before_search(self):
        from yana_planner.domain import InputError

        invalid = [
            dict(vessel_capacity_t=["0"]),
            dict(vessel_up_h=["1.5"]),
            dict(vessel_id=["A", "A"]),
            dict(arrival_t=[]),
            dict(target_t=["1000001"]),
            dict(return_cutoff_h=["20"]),
        ]
        with patch("yana_planner.service.compare") as solver:
            for change in invalid:
                v = self.conditions()
                v.update(change)
                with self.subTest(change=change), self.assertRaises(InputError):
                    calculate("r03", "30", custom=v)
            solver.assert_not_called()

    def test_invalid_delay_rejected_before_search(self):
        from yana_planner.domain import InputError

        v = self.conditions()
        v.update(
            arrival_h=["10"], announced_at_h=["10"], delayed_arrival=["1"], new_arrival_h=["16"]
        )
        with patch("yana_planner.service.run_replanning") as solver:
            with self.assertRaises(InputError):
                calculate("delay", "30", custom=v)
            solver.assert_not_called()

    def test_default_form_preserves_reference_and_mixed_fleet(self):
        from yana_planner.service import custom_conditions, form_defaults

        cfg, event = custom_conditions(form_defaults(), with_delay=False)
        self.assertEqual(cfg["id"], "R03")
        self.assertIsNone(event)
        v = form_defaults()
        v["vessel_capacity_t"] = ["200", "600"]
        cfg, _ = custom_conditions(v, with_delay=False)
        self.assertEqual([a["capacity_t"] for a in cfg["vessels"]], [200, 600])
        self.assertEqual(cfg["payload_calendar"][0]["capacity_t"], 600)

    def test_web_saves_custom_inputs_and_report(self):
        from werkzeug.datastructures import MultiDict

        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(
                dict(TESTING=True, DATABASE=str(Path(tmp) / "runs.db"), SOLVER_TIME_LIMIT=0.1)
            ).test_client()
            data = MultiDict((k, x) for k, xs in self.conditions().items() for x in xs)
            response = client.post("/", data=data)
            self.assertEqual(response.status_code, 303)
            url = response.location
            page = client.get(url).text
            self.assertIn("TEST-600", page)
            self.assertIn("Свои условия", page)
            saved = client.get(url + "/results.json").json
            self.assertEqual(saved["inputs"]["scenario"]["vessels"][0]["capacity_t"], 600)
            self.assertEqual(saved["inputs"]["scenario"]["return_cutoff_h"], 50)
            self.assertIn("TEST-600", client.get(url + "/report.html").text)
            data.setlist("vessel_capacity_t", ["-1"])
            bad = client.post("/", data=data)
            self.assertEqual(bad.status_code, 400)
            self.assertIn('value="TEST-600"', bad.text)
            self.assertIn('value="-1"', bad.text)
