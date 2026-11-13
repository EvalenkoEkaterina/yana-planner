"""Learned fixed-lead meteorological calibration, independent of navigation rules.

Only external forecast variables and known calendar/location enter features.
ERA5 targets must never be supplied as operational predictor variables.
"""

from datetime import datetime

import numpy as np

from .domain import InputError

VARIABLES = [
    "wind_speed_10m",
    "temperature_2m",
    "precipitation",
    "relative_humidity_2m",
    "pressure_msl",
    "cloud_cover",
    "wind_direction_10m",
]
TARGETS = VARIABLES[:3]
UNITS = dict(
    wind_speed_10m="m/s",
    temperature_2m="°C",
    precipitation="mm",
    relative_humidity_2m="%",
    pressure_msl="hPa",
    cloud_cover="%",
    wind_direction_10m="°",
)
FEATURE_NAMES = [
    "wind",
    "temperature",
    "precipitation",
    "humidity",
    "pressure",
    "cloud",
    "direction_sin",
    "direction_cos",
    "wind_squared",
    "temperature_squared",
    "log_precipitation",
    "wind_humidity",
    "wind_cloud",
    "temperature_humidity",
    "wind_u",
    "wind_v",
    "latitude",
    "longitude",
    "season_sin",
    "season_cos",
    "local_hour_sin",
    "local_hour_cos",
    "wind_latitude",
    "temperature_latitude",
]


def features(values, valid_times, latitude, longitude):
    """Rows contain seven variables in VARIABLES order, all valid at the same UTC hour."""
    try:
        a = np.asarray(values, dtype=float)
    except (ValueError, TypeError) as exc:
        raise InputError("Прогноз должен содержать числовые значения") from exc
    if a.ndim != 2 or a.shape[1] != len(VARIABLES) or len(valid_times) != len(a) or not len(a):
        raise InputError("Неверный размер погодной матрицы")
    if not np.isfinite(a).all():
        raise InputError("Пропуски прогноза не заменяются нулями")
    w, t, p, rh, pressure, cloud, direction = a.T
    if (
        (w < 0)
        | (p < 0)
        | (rh < 0)
        | (rh > 100)
        | (cloud < 0)
        | (cloud > 100)
        | (pressure <= 0)
        | (direction < 0)
        | (direction > 360)
    ).any():
        raise InputError("Показатель вне физического диапазона")
    try:
        lat = np.broadcast_to(np.asarray(latitude, dtype=float), len(a))
        lon = np.broadcast_to(np.asarray(longitude, dtype=float), len(a))
        dates = [datetime.fromisoformat(x) for x in valid_times]
    except (ValueError, TypeError) as exc:
        raise InputError("Некорректные даты или координаты") from exc
    if (
        not np.isfinite(lat).all()
        or not np.isfinite(lon).all()
        or (abs(lat) > 90).any()
        or (abs(lon) > 180).any()
    ):
        raise InputError("Некорректные координаты")
    if any(d.tzinfo is not None or d.minute or d.second or d.microsecond for d in dates):
        raise InputError(
            "Ожидаются целые часы UTC без суффикса часового пояса, как в API timezone=UTC"
        )
    season = np.array([d.timetuple().tm_yday for d in dates]) * 2 * np.pi / 365.25
    hour = (np.array([d.hour for d in dates]) + lon / 15) * 2 * np.pi / 24
    angle = np.deg2rad(direction)
    return np.column_stack(
        [
            w,
            t,
            p,
            rh,
            pressure,
            cloud,
            np.sin(angle),
            np.cos(angle),
            w * w,
            t * t,
            np.log1p(p),
            w * rh,
            w * cloud,
            t * rh,
            w * np.sin(angle),
            w * np.cos(angle),
            lat,
            lon,
            np.sin(season),
            np.cos(season),
            np.sin(hour),
            np.cos(hour),
            w * lat,
            t * lat,
        ]
    )


def fit_ridge(x, residual, alpha):
    """Ridge residual regression, statistics fitted only on the training subset."""
    if not np.isfinite(alpha) or alpha <= 0:
        raise InputError("alpha должен быть положительным")
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1
    z = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    penalty = np.eye(z.shape[1]) * alpha
    penalty[0, 0] = 0
    coef = np.linalg.solve(z.T @ z + penalty, z.T @ residual)
    return dict(
        kind="ridge_residual",
        alpha=alpha,
        mean=mean.tolist(),
        scale=scale.tolist(),
        coefficients=coef.tolist(),
    )


def predict_component(component, x, raw, target):
    kind = component["kind"]
    if kind == "raw":
        result = raw.copy()
    elif kind == "median_bias":
        result = raw + component["offset"]
    elif kind == "ridge_residual":
        z = (x - np.asarray(component["mean"])) / np.asarray(component["scale"])
        coef = np.asarray(component["coefficients"])
        result = raw + coef[0] + z @ coef[1:]
    else:
        raise InputError("Неизвестный тип модели")
    return np.maximum(result, 0) if target != "temperature_2m" else result


def predict(model, request):
    """Portable inference contract. No reanalysis or future observations are accepted."""
    if model.get("schema_version") != "1.0" or model.get("feature_names") != FEATURE_NAMES:
        raise InputError("Несовместимая версия модели")
    if request.get("source_model") != model["source_model"] or request.get("lead_hours") != 24:
        raise InputError("Нужен ECMWF IFS 0.25 и упреждение 24 часа")
    if request.get("timezone") != "UTC" or request.get("units") != UNITS:
        raise InputError("Нужны UTC и точные единицы модели")
    hourly = request.get("hourly", {})
    times = hourly.get("time", [])
    if set(hourly) != {"time", *VARIABLES}:
        raise InputError("Нужны только семь прогнозных показателей и time")
    if any(len(hourly[v]) != len(times) for v in VARIABLES):
        raise InputError("Несогласованные длины прогноза")
    if len(set(times)) != len(times):
        raise InputError("Повторные часы прогноза")
    values = np.column_stack([hourly[v] for v in VARIABLES])
    x = features(values, times, request["latitude"], request["longitude"])
    if any(datetime.fromisoformat(t).month not in model["months"] for t in times):
        raise InputError("Модель проверялась только на сезоне июнь–сентябрь")
    result = {
        t: predict_component(model["components"][t], x, values[:, VARIABLES.index(t)], t).tolist()
        for t in TARGETS
    }
    return dict(
        schema_version="1.0",
        model_id=model["model_id"],
        lead_hours=24,
        timezone="UTC",
        hourly=dict(time=times, **result),
        units={t: UNITS[t] for t in TARGETS},
        status="research_calibration_against_era5",
        correction_applied={t: model["components"][t]["kind"] != "raw" for t in TARGETS},
        navigation_admissibility="not_evaluated",
        scope="Tested on selected northern river locations; new routes require evaluation",
    )
