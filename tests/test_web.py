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


class SearchBudgetTests(unittest.TestCase):
    def test_requested_budget_reaches_solver(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(
                dict(TESTING=True, DATABASE=str(Path(tmp) / "runs.db"))
            ).test_client()
            with patch(
                "yana_planner.web.calculate",
                return_value=(*calculate("r03", "328", time_limit=0.1),),
            ) as solver:
                response = client.post(
                    "/", data=dict(scenario="r03", deadline="328", search_seconds="10")
                )
                self.assertEqual(response.status_code, 303)
                self.assertEqual(solver.call_args.kwargs["time_limit"], 10)

    def test_invalid_budget_does_not_start_solver(self):
        client = create_app(dict(TESTING=True)).test_client()
        with patch("yana_planner.web.calculate") as solver:
            for value in ["", "0", "-1", "3", "100000", "nan"]:
                self.assertEqual(
                    client.post(
                        "/", data=dict(scenario="r03", deadline="328", search_seconds=value)
                    ).status_code,
                    400,
                )
            solver.assert_not_called()


class AutoSearchWebTests(unittest.TestCase):
    def test_auto_mode_passes_budget_and_is_saved_in_report(self):
        from werkzeug.datastructures import MultiDict

        values = CustomInputTests().conditions()
        values["search_seconds"] = ["auto"]
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(
                dict(TESTING=True, DATABASE=str(Path(tmp) / "runs.db"))
            ).test_client()
            response = client.post(
                "/", data=MultiDict((k, v) for k, xs in values.items() for v in xs)
            )
            self.assertEqual(response.status_code, 303)
            saved = client.get(response.location + "/results.json").json
            self.assertTrue(saved["inputs"]["settings"]["adaptive_search"])
            self.assertEqual(saved["inputs"]["settings"]["time_limit_per_stage_s"], 30)
            self.assertEqual(saved["result"]["planner"]["accepted_t"], 600)
            self.assertIn("Автоматический поиск", client.get(response.location).text)
            self.assertIn("попыток 1", client.get(response.location + "/report.html").text)


class WholeCalculationTimeoutTests(unittest.TestCase):
    def test_timeout_preserves_fields_and_does_not_save_result(self):
        from yana_planner.web_worker import CalculationTimeout

        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(
                dict(TESTING=True, DATABASE=str(Path(tmp) / "runs.db"))
            ).test_client()
            with patch(
                "yana_planner.web.calculate",
                side_effect=CalculationTimeout(
                    "Расчёт остановлен: общий лимит 30 с исчерпан. Условия сохранены в форме."
                ),
            ):
                response = client.post(
                    "/",
                    data=dict(
                        scenario="r03",
                        deadline="246",
                        target_t="1700",
                        vessel_id="Моё судно",
                        search_seconds="auto",
                    ),
                )
            self.assertEqual(response.status_code, 408)
            self.assertIn('value="1700"', response.text)
            self.assertIn('value="Моё судно"', response.text)
            self.assertIn("общий лимит 30 с", response.text)
            self.assertNotIn("СОХРАНЁННЫЙ РАСЧЁТ", response.text)

    def test_real_child_process_is_stopped_on_wall_timeout(self):
        import subprocess
        import sys
        import time

        from yana_planner.web_worker import CalculationTimeout, calculate_with_timeout

        real_run = subprocess.run

        def slow_child(command, **kwargs):
            return real_run([sys.executable, "-c", "import time; time.sleep(5)"], **kwargs)

        started = time.monotonic()
        with patch("yana_planner.web_worker.subprocess.run", side_effect=slow_child):
            with self.assertRaises(CalculationTimeout):
                calculate_with_timeout("r03", "328", total_timeout=0.05)
        self.assertLess(time.monotonic() - started, 1.5)


class DemoPresetTests(unittest.TestCase):
    @staticmethod
    def fields(page):
        from html.parser import HTMLParser

        class Fields(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = {}
                self.select = None

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "input" and a.get("name"):
                    self.values.setdefault(a["name"], []).append(a.get("value", ""))
                elif tag == "select":
                    self.select = a["name"]
                elif tag == "option" and self.select:
                    if self.select not in self.values or "selected" in a:
                        self.values[self.select] = [a["value"]]

            def handle_endtag(self, tag):
                if tag == "select":
                    self.select = None

        parser = Fields()
        parser.feed(page)
        return parser.values

    def test_both_presets_restore_complete_original_conditions_without_solving(self):
        import json

        from yana_planner.service import ROOT, custom_conditions

        original = json.loads((ROOT / "examples/r03.json").read_text())
        client = create_app(dict(TESTING=True)).test_client()
        with patch("yana_planner.web.calculate") as solver:
            for preset, deadline in [("r03", "328"), ("delay", "280")]:
                response = client.get("/?preset=" + preset)
                self.assertEqual(response.status_code, 200)
                fields = self.fields(response.text)
                self.assertEqual(fields["scenario"], [preset])
                self.assertEqual(fields["deadline"], [deadline])
                self.assertEqual(fields["search_seconds"], ["auto"])
                cfg, event = custom_conditions(fields, with_delay=preset == "delay")
                for key in [
                    "target_t",
                    "initial_source_t",
                    "destination_capacity_t",
                    "return_cutoff_h",
                    "vessels",
                    "arrivals",
                ]:
                    self.assertEqual(cfg[key], original[key], key)
                if event:
                    self.assertEqual(event["announced_at_h"], 60)
                    self.assertEqual(event["arrival_index"], 1)
                    self.assertEqual(event["new_arrival_h"], 168)
            solver.assert_not_called()

    def test_edited_form_is_not_overwritten_by_preset_query_on_submit(self):
        from werkzeug.datastructures import MultiDict

        from yana_planner.service import form_defaults

        values = form_defaults("delay")
        values.update(customize=["1"], target_t=["1600"], deadline=["250"])
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(
                dict(TESTING=True, DATABASE=str(Path(tmp) / "runs.db"))
            ).test_client()
            with patch(
                "yana_planner.web.calculate", return_value=calculate("r03", "328", time_limit=0.1)
            ) as solver:
                response = client.post(
                    "/?preset=delay", data=MultiDict((k, x) for k, xs in values.items() for x in xs)
                )
            self.assertEqual(response.status_code, 303)
            self.assertEqual(solver.call_args.args, ("delay", "250"))
            self.assertEqual(solver.call_args.kwargs["custom"]["target_t"], ["1600"])

    def test_unknown_preset_is_rejected_without_solver(self):
        client = create_app(dict(TESTING=True)).test_client()
        with patch("yana_planner.web.calculate") as solver:
            response = client.get("/?preset=unknown")
            self.assertEqual(response.status_code, 400)
            self.assertIn("Неизвестный пример", response.text)
            solver.assert_not_called()
