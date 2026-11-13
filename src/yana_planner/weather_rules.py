"""Configurable route rules; unsupported engine effects fail explicitly."""

from copy import deepcopy

from .domain import InputError
from .weather import _positive, build_weather_event

SUPPORTED_SCOPE = {
    "location": "entire_route",
    "vessels": "all",
    "operations": ["loaded_movement", "return_movement"],
}


def validate_rule(rule):
    if not isinstance(rule, dict):
        raise InputError("Погодное правило должно быть объектом")
    required = {
        "schema_version",
        "id",
        "route_id",
        "metric",
        "unit",
        "aggregation",
        "operator",
        "threshold",
        "point_ids",
        "effect",
        "scope",
        "basis",
    }
    if not required <= rule.keys() or rule.keys() - required - {"sensitivity_thresholds"}:
        raise InputError("Неполные или неизвестные поля погодного правила")
    for field in ("id", "route_id"):
        if not isinstance(rule[field], str) or not rule[field].strip():
            raise InputError(f"{field}: нужен непустой идентификатор")
    supported = dict(
        schema_version="1.0",
        metric="wind_speed_10m",
        unit="m/s",
        aggregation="maximum_selected_points",
        operator=">=",
        effect="movement_closure",
    )
    for key, value in supported.items():
        if rule[key] != value:
            raise InputError(f"Пока не поддерживается {key}={rule[key]!r}")
    if rule["scope"] != SUPPORTED_SCOPE:
        raise InputError(
            "Ядро поддерживает только оба плеча всего маршрута для всех судов; локальное ограничение не будет расширено автоматически"
        )
    basis = rule["basis"]
    if (
        not isinstance(basis, dict)
        or basis.get("status") != "scenario_assumption"
        or not basis.get("description")
    ):
        raise InputError(
            "Нужно описание сценарного основания; нормативные правила требуют отдельной проверки"
        )
    _positive(rule["threshold"], "threshold")
    points = rule["point_ids"]
    if (
        not isinstance(points, list)
        or not points
        or any(not isinstance(p, str) or not p.strip() for p in points)
        or len(set(points)) != len(points)
    ):
        raise InputError("point_ids: непустой список уникальных идентификаторов")
    levels = rule.get("sensitivity_thresholds", [])
    if not isinstance(levels, list):
        raise InputError("sensitivity_thresholds: нужен список")
    for level in levels:
        _positive(level, "sensitivity_thresholds")
    return deepcopy(rule)


def event_from_rule(records, rule, *, route_id, t0, horizon_h, announced_at_h):
    rule = validate_rule(rule)
    if route_id != rule["route_id"]:
        raise InputError("Правило относится к другому маршруту")
    by_id = {}
    for record in records:
        ident = record.get("requested_point", {}).get("name")
        if not isinstance(ident, str) or ident in by_id:
            raise InputError("У погодных точек должны быть уникальные идентификаторы")
        by_id[ident] = record
    if set(by_id) != set(rule["point_ids"]):
        raise InputError("Набор погодных точек должен точно совпадать с правилом")
    event = build_weather_event(
        [by_id[p] for p in rule["point_ids"]],
        t0=t0,
        horizon_h=horizon_h,
        announced_at_h=announced_at_h,
        threshold_ms=rule["threshold"],
    )
    event.update(rule=rule, route_id=route_id, scope=deepcopy(rule["scope"]))
    return event
