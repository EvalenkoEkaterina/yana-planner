"""Explicit adapters for PR #1 formats; no new schedule validity is inferred."""

from __future__ import annotations

from copy import deepcopy

from .domain import ConfirmationRequired, InputError, integer


def normalize_event(event: dict) -> dict:
    if not isinstance(event, dict):
        raise InputError("Событие должно быть объектом")
    result = deepcopy(event)
    if result.get("type") not in ("vessel_unavailable", "vessel_unavailability"):
        return result
    if "interval" in result:
        interval = result.pop("interval")
        if interval is None:
            raise ConfirmationRequired("interval: значение не задано")
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise InputError("interval должен содержать две границы [start, end)")
        for field, value in zip(["from_h", "to_h"], interval):
            value = integer(value, field)
            if field in result and integer(result[field], field) != value:
                raise InputError(f"Противоречивые границы: interval и {field}")
            result[field] = value
    for field in ["from_h", "to_h"]:
        if field in result:
            result[field] = integer(result[field], field)
    result["type"] = "vessel_unavailable"
    return result


def normalize_result(result: dict) -> dict:
    """Adapt old result keys; preserve unknown values and supplied statuses.

    Disagreeing aliases are rejected. Original input cannot be recovered from
    old results, so initial_input is never invented. This is not a checker.
    """
    if not isinstance(result, dict):
        raise InputError("Результат должен быть объектом")
    adapted = deepcopy(result)
    for old, current in [
        ("revised_ready", "revised_baseline"),
        ("revised_milp", "revised_planner"),
    ]:
        if old in adapted:
            if current in adapted and adapted[current] != adapted[old]:
                raise InputError(f"Противоречивые результаты: {old} и {current}")
            adapted[current] = deepcopy(adapted[old])
    initial = adapted.get("initial")
    if "scenario_id" not in adapted and isinstance(initial, dict):
        scenario_id = initial.get("scenario_id")
        if isinstance(scenario_id, str) and scenario_id:
            adapted["scenario_id"] = scenario_id
    if "event" in adapted:
        adapted.setdefault("source_event", deepcopy(adapted["event"]))
        adapted["event"] = normalize_event(adapted["event"])
    return adapted
