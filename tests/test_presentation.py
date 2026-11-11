import copy
import re
import tempfile
import unittest
from pathlib import Path

from yana_planner.experiments import event_effects, summarize
from yana_planner.io import export_result, read_json
from yana_planner.presentation import event_card, schedule_sections
from yana_planner.service import ROOT


class PresentationTests(unittest.TestCase):
    def test_signed_differences(self):
        a = dict(status="valid_simulation", accepted_t=1650, trip_count=6, last_receipt_h=276)
        b = dict(a, trip_count=5, last_receipt_h=224)
        c = summarize(a, b, 1650, [])
        self.assertEqual(c["differences"], dict(trip_count=1, last_receipt_h=52))
        self.assertIn("52 ч раньше", c["conclusion"])
        reverse = summarize(b, a, 1650, [])
        self.assertIn("52 ч позже", reverse["conclusion"])
        self.assertIn("на 1 больше", reverse["conclusion"])
        self.assertEqual(summarize(a, dict(b, accepted_t=1600), 1650, [])["differences"], {})
        self.assertIsNone(
            summarize(a, dict(b, last_receipt_h=None), 1650, [])["differences"]["last_receipt_h"]
        )

    def test_saved_delay_and_svg(self):
        result = read_json(ROOT / "demos/replanning/results.json")
        cfg = read_json(ROOT / "examples/r03.json")
        card = event_card(result, cfg)
        for text in ["825 т", "120 ч", "168 ч", "60 ч"]:
            self.assertIn(text, card)
        svg = schedule_sections(result)
        self.assertEqual(svg.count('role="img"'), 4)
        self.assertIn("Начатый рейс", svg)
        self.assertIn("SIM-A", svg)
        self.assertIn("Погрузка, 0", svg)
        self.assertIn("После события — MILP", svg)

    def test_tradeoff_and_unknown_mean(self):
        result = read_json(ROOT / "demos/static/results.json")
        a, b = result["baseline"], result["planner"]
        summary = summarize(a, b, 1650, [])
        self.assertAlmostEqual(
            summary["rows"]["READY"]["weighted_mean_receipt_h"], 136.6969696969697
        )
        self.assertIn("позже", summary["tradeoff"])
        self.assertIn("не входит в цели", summary["tradeoff"])
        unknown = summarize(a, dict(b, weighted_mean_receipt_h=None), 1650, [])
        self.assertIsNone(unknown["rows"]["MILP"]["weighted_mean_receipt_h"])
        self.assertNotIn("позже", unknown["tradeoff"])
        unequal = summarize(a, dict(b, accepted_t=1000), 1650, [])
        self.assertNotIn("позже", unequal["tradeoff"])

    def test_delay_effects(self):
        result = read_json(ROOT / "demos/replanning/results.json")
        effects = event_effects(result)
        self.assertEqual(effects["READY"]["metrics"]["last_receipt_h"]["change"], 8)
        self.assertEqual(effects["MILP"]["metrics"]["last_receipt_h"]["change"], 48)
        self.assertEqual(effects["MILP"]["metrics"]["accepted_t"]["change"], 0)
        changed = copy.deepcopy(result)
        changed["revised_baseline"]["result"]["accepted_t"] = 1600
        self.assertTrue(event_effects(changed)["READY"]["warning"])
        changed["revised_baseline"]["result"]["status"] = "invalid_schedule"
        self.assertIsNone(event_effects(changed)["READY"]["metrics"]["accepted_t"]["change"])
        self.assertEqual(event_effects(result["initial"]), {})

    def test_common_scale_and_portable_legend(self):
        result = read_json(ROOT / "demos/replanning/results.json")
        result["deadline_h"] = 280
        svg = schedule_sections(result)
        # All panels share the same axis even when their return horizons differ.
        axes = re.findall(r'<text x="[^"]+" y="22"[^>]*>([^<]+)</text>', svg)
        self.assertEqual(len(axes), 24)
        self.assertTrue(all(axes[:6] == axes[i : i + 6] for i in range(0, 24, 6)))
        self.assertIn("№1 · 400 т", svg)
        with tempfile.TemporaryDirectory() as directory:
            export_result(directory, result)
            report = (Path(directory) / "report.html").read_text()
        self.assertIn('width="12" height="12" fill="#d88c22"', report)
        self.assertIn("Что изменилось после задержки", report)
        self.assertIn("Среднее время приёмки", report)
