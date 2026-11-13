import json
import unittest
from copy import deepcopy

import numpy as np

from yana_planner.domain import InputError
from yana_planner.forecast_model import (
    FEATURE_NAMES,
    TARGETS,
    UNITS,
    VARIABLES,
    features,
    fit_ridge,
    predict,
    predict_component,
)


class ForecastModelTests(unittest.TestCase):
    def setUp(self):
        self.times = ["2026-08-01T00:00", "2026-08-01T01:00", "2026-08-01T02:00"]
        self.values = np.array(
            [
                [2, 5, 0, 80, 1010, 60, 180],
                [3, 6, 0.1, 90, 1008, 80, 190],
                [5, 7, 1, 85, 1000, 90, 200],
            ]
        )
        self.request = dict(
            source_model="ecmwf_ifs025",
            lead_hours=24,
            timezone="UTC",
            units=deepcopy(UNITS),
            latitude=71.0,
            longitude=136.0,
            hourly=dict(
                time=self.times, **{v: self.values[:, i].tolist() for i, v in enumerate(VARIABLES)}
            ),
        )
        self.model = dict(
            schema_version="1.0",
            feature_names=FEATURE_NAMES,
            source_model="ecmwf_ifs025",
            model_id="test",
            months=[6, 7, 8, 9],
            components={t: dict(kind="raw") for t in TARGETS},
        )

    def test_training_recovers_residual_and_serializes(self):
        rng = np.random.default_rng(12)
        x = rng.normal(size=(200, 4))
        res = 2 + 3 * x[:, 0] - x[:, 2]
        c = fit_ridge(x, res, 0.001)
        c = json.loads(json.dumps(c))
        actual = predict_component(c, x, np.zeros(200), "temperature_2m")
        self.assertLess(np.max(abs(actual - res)), 0.001)

    def test_inference_does_not_change_training_statistics(self):
        x = features(self.values, self.times, 71, 136)
        c = fit_ridge(x, np.array([1, 2, 3]), 1)
        before = deepcopy(c)
        predict_component(c, x * 100, np.ones(3), "temperature_2m")
        self.assertEqual(c, before)

    def test_raw_fallback_exact_and_no_safety_claim(self):
        result = predict(self.model, self.request)
        self.assertEqual(result["hourly"]["precipitation"], [0, 0.1, 1])
        self.assertEqual(result["navigation_admissibility"], "not_evaluated")

    def test_negative_rain_and_wind_predictions_clipped(self):
        x = features(self.values, self.times, 71, 136)
        for target in ("wind_speed_10m", "precipitation"):
            actual = predict_component(dict(kind="median_bias", offset=-100), x, np.ones(3), target)
            self.assertTrue((actual == 0).all())

    def test_unknown_reference_feature_rejected(self):
        self.request["hourly"]["actual_wind"] = [1, 2, 3]
        with self.assertRaises(InputError):
            predict(self.model, self.request)

    def test_wrong_units_lead_and_model_rejected(self):
        for key, value in [
            ("lead_hours", 48),
            ("source_model", "best_match"),
            ("timezone", "local"),
            ("units", {}),
        ]:
            request = deepcopy(self.request)
            request[key] = value
            with self.assertRaises(InputError):
                predict(self.model, request)

    def test_missing_weather_not_imputed(self):
        self.request["hourly"]["wind_speed_10m"][0] = None
        with self.assertRaises(InputError):
            predict(self.model, self.request)

    def test_out_of_season_rejected(self):
        self.request["hourly"]["time"] = [
            "2026-12-01T00:00",
            "2026-12-01T01:00",
            "2026-12-01T02:00",
        ]
        with self.assertRaises(InputError):
            predict(self.model, self.request)

    def test_duplicate_hours_rejected(self):
        self.request["hourly"]["time"] = [self.times[0]] * 3
        with self.assertRaises(InputError):
            predict(self.model, self.request)

    def test_direction_wrap(self):
        a = self.values.copy()
        a[0, 6] = 0
        a[1] = a[0]
        a[1, 6] = 360
        x = features(a, [self.times[0]] * 3, 71, 136)
        np.testing.assert_allclose(x[0], x[1], atol=1e-12)
