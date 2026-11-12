import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner import storage
from yana_planner.experiments import comparisons
from yana_planner.io import export_result, read_json
from yana_planner.service import ROOT
from yana_planner.visual_report import summary_page, visual_summary
from yana_planner.web import create_app


class VisualReportTests(unittest.TestCase):
    def setUp(self):
        self.result = read_json(ROOT / "demos/static/results.json")

    def test_same_volume_has_numeric_differences_and_common_zero_scales(self):
        view = visual_summary(self.result)
        self.assertIn("На 1 меньше", view)
        self.assertIn("На 52 ч раньше", view)
        self.assertIn("Меньше рейсов, приёмка завершена раньше", view)
        self.assertIn("Общая шкала: 0–276 ч", view)
        self.assertEqual(view.count('role="img"'), 3)
        self.assertIn('width="520.000"', view)
        self.assertIn("Стоимость не рассчитана", view)

    def test_different_volumes_do_not_make_fewer_trips_or_lower_cost_a_gain(self):
        self.result["planner"].update(accepted_t=1000, shortfall_t=650, trip_count=1)
        self.result["costs"] = dict(
            baseline=dict(total_rub=1000),
            planner=dict(total_rub=10),
            equal_volume=False,
            scenario_difference_rub=990,
        )
        view = visual_summary(self.result)
        self.assertIn("Цель пока не выполнена", view)
        self.assertIn("Разный объём — прямого выигрыша не заявляем", view)
        self.assertNotIn("дешевле", view)
        self.assertNotIn("На 5 меньше", view)

    def test_cost_gain_does_not_hide_later_completion(self):
        self.result["planning_strategy"] = "cost"
        self.result["planner"]["last_receipt_h"] = 300
        self.result["costs"] = dict(
            baseline=dict(total_rub=1000),
            planner=dict(total_rub=900),
            equal_volume=True,
            scenario_difference_rub=100,
        )
        view = visual_summary(self.result)
        self.assertIn("Сценарные затраты ниже на 100 ₽", view)
        self.assertIn("На 24 ч позже", view)
        self.assertIn("vr-delta vr-caution", view)
        self.assertIn("Фактическая экономия не установлена", view)

    def test_unknown_or_invalid_results_are_not_drawn_as_zero(self):
        self.result["planner"]["status"] = "requires_intervention"
        view = visual_summary(self.result)
        self.assertIn("Сначала требуется подтвердить", view)
        self.assertIn("Нет подтверждённых данных", view)
        self.assertNotIn("<title>MILP: 0", view)
        self.assertNotIn("На 1 меньше", view)
        self.result["planner"]["status"] = "valid_simulation"
        self.result["costs"] = dict(
            baseline=dict(total_rub=0),
            planner=dict(total_rub=0),
            equal_volume=True,
            scenario_difference_rub=0,
        )
        self.assertIn("<title>MILP: 0 ₽</title>", visual_summary(self.result))

    def test_event_comparison_distinguishes_methods_from_condition_change(self):
        result = read_json(ROOT / "demos/replanning/results.json")
        view = visual_summary(result)
        self.assertIn("После события · READY и MILP", view)
        self.assertIn("Как событие повлияло на план MILP", view)
        self.assertIn("Сравнение методов до события", view)
        self.assertIn("Последняя приёмка: 224 ч → 272 ч", view)

    def test_portable_export_embeds_charts_without_external_dependencies(self):
        before = copy.deepcopy(self.result)
        with tempfile.TemporaryDirectory() as directory:
            export_result(directory, self.result)
            page = (Path(directory) / "report.html").read_text()
        self.assertEqual(self.result, before)
        self.assertIn('data-visual-summary="1"', page)
        self.assertIn(".vr-grid{", page)
        self.assertNotIn("<script src=", page)
        self.assertIn("Подробное сравнение и проверки", page)
        self.assertIn("&lt;script&gt;", summary_page(self.result, "<script>"))

    def test_old_saved_run_gets_visual_report_without_recalculating(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "runs.db")
            result = dict(self.result, comparisons=comparisons(self.result), report_html="old")
            ident = storage.save(
                db,
                "Сохранённый маршрут",
                328,
                dict(
                    scenario=read_json(ROOT / "examples/r03.json"),
                    event=None,
                    deadline_h=328,
                    settings=dict(time_limit_per_stage_s=2, adaptive_search=False),
                ),
                result,
                "old",
            )
            client = create_app(dict(TESTING=True, DATABASE=db)).test_client()
            with patch("yana_planner.service.compare") as solver:
                page = client.get(f"/runs/{ident}")
                report = client.get(f"/runs/{ident}/summary.html?download=1")
                self.assertEqual(page.status_code, 200)
                self.assertIn('data-visual-summary="1"', page.text)
                self.assertEqual(report.status_code, 200)
                self.assertIn("attachment", report.headers["Content-Disposition"])
                self.assertIn("Печать / сохранить PDF", report.text)
                solver.assert_not_called()
