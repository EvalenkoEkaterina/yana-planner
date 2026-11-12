"""Render contract states without publishing placeholder amounts as real results."""

import unittest
from types import SimpleNamespace

from flask import render_template

from yana_planner.web import create_app


class DispatcherTemplateTests(unittest.TestCase):
    def render_cost(self, result, event=None):
        app = create_app({"TESTING": True})
        with app.test_request_context("/"):
            return render_template(
                "cost_result.html", run=SimpleNamespace(result=result, inputs={"event": event})
            )

    def test_absent_and_missing_costs_are_not_zero(self):
        absent = self.render_cost({})
        self.assertIn("нет экономического результата", absent)
        missing = self.render_cost(
            {
                "costs": {
                    "planner": {
                        "status": "missing_cost_inputs",
                        "total_rub": None,
                        "missing": ["vessel_rates.A.active_vessel_hour_rub"],
                    }
                }
            }
        )
        self.assertIn("vessel_rates.A.active_vessel_hour_rub", missing)
        self.assertNotIn("0.00 ₽", missing)

    def test_zero_and_negative_difference_are_preserved(self):
        result = {
            "planning_strategy": "cost",
            "costs": {
                "planner": {
                    "status": "scenario_cost",
                    "total_rub": 0,
                    "horizon_h": 492,
                    "components": {
                        "separate_trip_operations_rub": 0,
                        "active_time_rub": 0,
                        "waiting_time_rub": 0,
                    },
                },
                "equal_volume": True,
                "scenario_difference_rub": -100,
            },
        }
        html = self.render_cost(result)
        self.assertIn("0.00 ₽", html)
        self.assertIn("-100.00 ₽", html)
        self.assertIn("Минимум затрат", html)
        result["costs"]["equal_volume"] = False
        self.assertNotIn("-100.00 ₽", self.render_cost(result))

    def test_weather_source_is_escaped(self):
        event = {
            "type": "weather_closure",
            "data_kind": "synthetic",
            "announced_at_h": 60,
            "closures": [[120, 132]],
            "source": "<script>alert(1)</script>",
        }
        html = self.render_cost({}, event)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("Синтетический опыт", html)


if __name__ == "__main__":
    unittest.main()
