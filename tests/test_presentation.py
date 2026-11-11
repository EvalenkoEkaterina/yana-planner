import unittest

from yana_planner.experiments import summarize
from yana_planner.io import read_json
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
        self.assertEqual(svg.count("<svg "), 4)
        self.assertIn("Начатый рейс", svg)
        self.assertIn("SIM-A", svg)
        self.assertIn("Погрузка, 0", svg)
        self.assertIn("После события — MILP", svg)
