"""One explicit historical weather profile; no inference of river-wide coverage."""

import json
from datetime import timedelta
from pathlib import Path

from .domain import InputError, integer
from .weather_feed import instant, mapped_points, normalize, restrictions

ROOT = Path(__file__).resolve().parents[2]


def assess_route_weather(path, *, t0, horizon_h, mode="historical_demo", decision_at=None):
    """Match exact path to a prepared profile, then check every point and hour."""
    if not isinstance(path, dict) or path.get("status") != "found":
        raise InputError("Сначала найдите путь по графу")
    mapping = json.loads((ROOT / "examples/weather_feed/segment_mapping.json").read_text())
    segments = [edge["id"] for edge in path["segments"]]
    if (
        path.get("network_id") != "yana-reference-2026-v1"
        or set(segments) != {"yana-1400", "yana-1399"}
        or {path["origin_id"], path["destination_id"]} != {"nizhneyansk", "ust-kuyga"}
    ):
        return dict(
            status="unsupported_weather_route",
            closures=None,
            message="Для выбранного пути погодный профиль не подготовлен",
        )
    points = mapped_points(mapping, segments)
    folder = ROOT / "examples/weather_feed"
    records = json.loads((folder / "input.json").read_text())
    values = normalize(records)
    start = instant(t0)
    horizon = integer(horizon_h, "horizon_h", minimum=1)
    if horizon > 492:
        raise InputError("Проверка поддерживает горизонт до 492 часов")
    end = start + timedelta(hours=horizon)
    rule = json.loads((folder / "rule.json").read_text())
    result = restrictions(
        values, points, start.isoformat(), end.isoformat(), rule, mode=mode, decision_at=decision_at
    )
    result.update(
        segment_ids=segments,
        profile_id="yana-endpoints-july2025-demo",
        sources=[
            dict(
                point=r["point"],
                source=r["source"],
                retrieved_at=r["retrieved_at"],
                forecast_issued_at=r.get("forecast_issued_at"),
            )
            for r in records
        ],
        data_kind="forecast",
        forecast_issued_at=None,
        spatial_basis="scenario_endpoint_proxy_not_full_segment_coverage",
        automatic_operational_use=False,
    )
    return result
