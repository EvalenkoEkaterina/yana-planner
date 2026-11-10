"""Summarize checked experiments without promoting unknowns to improvements.

This module consumes checker verdicts, not raw schedules. It does not validate
voyages or prove solver optimality; those responsibilities stay with the engine.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from .compat import normalize_result
from .domain import InputError, integer

METRICS = {
    "t": "accepted_t",
    "shortfall_t": "shortfall_t",
    "trips": "trip_count",
    "last_receipt_h": "last_receipt_h",
    "weighted_mean_receipt_h": "weighted_mean_receipt_h",
    "busy_vessel_h": "busy_vessel_h",
}
SAVINGS = {
    "trips_saved": "trips",
    "hours_saved": "last_receipt_h",
    "mean_receipt_hours_saved": "weighted_mean_receipt_h",
    "busy_vessel_hours_saved": "busy_vessel_h",
}


def _value(value: Any, label: str) -> int | float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise InputError(f"{label}: требуется конечное неотрицательное число или null")
    return value


def _pair(
    baseline: Any, planner: Any, *, phase: str, deadline: int, scenario_id: Any
) -> dict[str, Any]:
    row = dict(phase=phase, scenario_id=scenario_id, deadline_h=deadline)
    valid = {}
    for name, verdict in [("READY", baseline), ("MILP", planner)]:
        if verdict is not None and not isinstance(verdict, dict):
            raise InputError(f"{name}: результат должен быть объектом или null")
        verdict = verdict or {}
        row[f"{name}_status"] = verdict.get("status", "missing_result")
        valid[name] = row[f"{name}_status"] == "valid_simulation"
        for column, source in METRICS.items():
            # Invalid results may retain diagnostic trip counts/times. They are
            # deliberately not used as accepted performance in a summary.
            row[f"{name}_{column}"] = (
                _value(verdict.get(source), f"{name}.{source}") if valid[name] else None
            )
        shortfall = row[f"{name}_shortfall_t"]
        row[f"{name}_target_met"] = (
            shortfall == 0 if row[f"{name}_t"] is not None and shortfall is not None else None
        )
    a, b = row["READY_t"], row["MILP_t"]
    known_tonnage = all(valid.values()) and a is not None and b is not None
    row["delta_t"] = b - a if known_tonnage else None
    row["equal_volume"] = a == b if known_tonnage else None
    for column, metric in SAVINGS.items():
        left, right = row[f"READY_{metric}"], row[f"MILP_{metric}"]
        row[column] = (
            left - right
            if row["equal_volume"] is True and left is not None and right is not None
            else None
        )
    values = [row[name] for name in SAVINGS]
    known = [x for x in values if x is not None]
    if any(x > 0 for x in known) and any(x < 0 for x in known):
        row["tradeoff"] = True
    else:
        row["tradeoff"] = False if all(x is not None for x in values) else None
    row["actual_savings_rub"] = None
    row["real_admissibility"] = "requires_confirmation"
    return row


def summarize_experiment(result: dict[str, Any]) -> list[dict[str, Any]]:
    """One row for compare(), or before/after rows for run_replanning().

    Positive delta_t means more tonnes for MILP. Positive *_saved means fewer
    resources/earlier receipt for MILP, and is reported only at equal volume.
    A tradeoff includes later weighted mean receipt even if final receipt is
    earlier. Unknown or invalid results never become zero-valued improvements.
    """
    result = normalize_result(result)
    deadline = integer(result.get("deadline_h"), "deadline_h")
    common = dict(deadline=deadline, scenario_id=result.get("scenario_id"))
    if "initial" in result:
        initial = result["initial"]
        if not isinstance(initial, dict):
            raise InputError("initial должен быть объектом")
        if integer(initial.get("deadline_h"), "initial.deadline_h") != deadline:
            raise InputError("Сроки до и после события должны совпадать")
        revised = []
        for name in ["revised_baseline", "revised_planner"]:
            value = result.get(name)
            if value is not None and not isinstance(value, dict):
                raise InputError(f"{name} должен быть объектом или null")
            revised.append((value or {}).get("result"))
        return [
            _pair(initial.get("baseline"), initial.get("planner"), phase="before", **common),
            _pair(*revised, phase="after", **common),
        ]
    if "baseline" not in result and "planner" not in result:
        raise InputError("Не найдено сравнение READY и MILP")
    return [_pair(result.get("baseline"), result.get("planner"), phase="static", **common)]
