"""PR #1 API retained as adapters to the common replanning implementation.

The original participant implementation is preserved in the PR merge history.
Entry points accept her event shape and return shared results plus legacy
aliases. The actual planner and independent checker have one implementation.
"""

from __future__ import annotations

from copy import deepcopy

from .compat import normalize_event
from .domain import InputError
from .replanning import apply_vessel_unavailable, replan, run_replanning


def _vessel_event(event):
    normalized = normalize_event(event)
    if normalized.get("type") != "vessel_unavailable":
        raise InputError("Нужно событие недоступности судна")
    return normalized


def apply_vessel_unavailability(cfg: dict, event: dict) -> dict:
    return apply_vessel_unavailable(cfg, _vessel_event(event))


def replan_unavailability(
    cfg: dict,
    initial_plan: list[dict],
    event: dict,
    deadline: int,
    *,
    method: str = "milp",
    time_limit: float = 3,
) -> dict:
    _vessel_event(event)
    return replan(cfg, initial_plan, event, deadline, method=method, time_limit=time_limit)


def run_unavailability_experiment(
    cfg: dict,
    event: dict,
    deadline: int,
    *,
    time_limit: float = 10,
    reference: dict | None = None,
) -> dict:
    _vessel_event(event)
    if reference is not None and not isinstance(reference, dict):
        raise InputError("reference должен быть объектом или null")
    incumbent = None
    if reference:
        if not isinstance(reference.get("planner"), dict):
            raise InputError("reference должен содержать planner")
        incumbent = reference["planner"].get("trips")
    result = run_replanning(cfg, event, deadline, time_limit=time_limit, incumbent=incumbent)
    result["revised_ready"] = deepcopy(result["revised_baseline"])
    result["revised_milp"] = deepcopy(result["revised_planner"])
    return result
