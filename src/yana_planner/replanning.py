"""One information update with immutable historical dispatches."""

from __future__ import annotations

from copy import deepcopy

from .domain import InputError, InterventionRequired, Scenario, integer, proposals
from .planning import compare, optimize, ready
from .validation import check_schedule, snapshot


def _tag_update(cfg: dict, event: dict, at: int) -> dict:
    cfg["id"] = f"{cfg['id']}@{at}"
    cfg["provenance"] = dict(
        cfg.get("provenance", {}), update=deepcopy(event), parent_id=cfg["id"].split("@")[0]
    )
    return Scenario.from_dict(cfg).to_dict()


def apply_arrival_delay(cfg: dict, event: dict) -> dict:
    cfg = Scenario.from_dict(cfg).to_dict()
    if not isinstance(event, dict) or event.get("type") != "arrival_delay":
        raise InputError("Поддерживается событие type=arrival_delay")
    at = integer(event.get("announced_at_h"), "announced_at_h")
    index = integer(event.get("arrival_index"), "arrival_index")
    when = integer(event.get("new_arrival_h"), "new_arrival_h")
    if index >= len(cfg["arrivals"]):
        raise InputError("arrival_index вне списка поступлений")
    old = cfg["arrivals"][index]["h"]
    if old <= at:
        raise InputError("Нельзя менять уже произошедшее поступление")
    if when <= old or when <= at:
        raise InputError("Задержка должна переносить будущее поступление на более поздний момент")
    cfg["arrivals"][index]["h"] = when
    return _tag_update(cfg, event, at)


def apply_vessel_unavailable(cfg: dict, event: dict) -> dict:
    cfg = Scenario.from_dict(cfg).to_dict()
    if not isinstance(event, dict) or event.get("type") != "vessel_unavailable":
        raise InputError("Нужно событие type=vessel_unavailable")
    at = integer(event.get("announced_at_h"), "announced_at_h")
    start = integer(event.get("from_h"), "from_h")
    end = integer(event.get("to_h"), "to_h")
    if start < at:
        raise InputError("Начало недоступности не может предшествовать сообщению")
    if start >= end:
        raise InputError("Недоступность должна иметь положительную длительность")
    vessel_id = event.get("vessel_id")
    if not isinstance(vessel_id, str) or not vessel_id:
        raise InputError("В событии нужен vessel_id")
    vessel = next((v for v in cfg["vessels"] if v["id"] == vessel_id), None)
    if vessel is None:
        raise InputError("Неизвестное судно в событии недоступности")
    vessel.setdefault("unavailable_intervals", []).append([start, end])
    return _tag_update(cfg, event, at)


def apply_event(cfg: dict, event: dict) -> dict:
    if not isinstance(event, dict):
        raise InputError("Событие должно быть объектом")
    if event.get("type") == "arrival_delay":
        return apply_arrival_delay(cfg, event)
    if event.get("type") == "vessel_unavailable":
        return apply_vessel_unavailable(cfg, event)
    raise InputError("Поддерживаются события arrival_delay и vessel_unavailable")


def replan(
    cfg: dict,
    initial_plan: list[dict],
    event: dict,
    deadline: int,
    *,
    method: str = "milp",
    time_limit: float = 3,
) -> dict:
    if method not in ("ready", "milp"):
        raise InputError("method должен быть ready или milp")
    changed = apply_event(cfg, event)
    at = integer(event.get("announced_at_h"), "announced_at_h")
    state = snapshot(cfg, initial_plan, deadline, at)
    committed = proposals(state["committed_trips"])
    try:
        # Event at h is processed after the observed departures through h.
        # All new dispatches must start strictly later than that observation.
        fallback = ready(changed, deadline, fixed=committed, decision_h=at + 1)
        if method == "milp":
            schedule, logs = optimize(
                changed,
                deadline,
                fallback,
                fixed=committed,
                decision_h=at + 1,
                time_limit=time_limit,
            )
        else:
            schedule, logs = fallback, []
        verdict = check_schedule(changed, schedule, deadline)
        if verdict["status"] != "valid_simulation":
            verdict["status"] = "no_feasible_solution_found"
        return dict(
            method=method,
            announcement_h=at,
            snapshot=state,
            committed=committed,
            updated_input=changed,
            result=verdict,
            solver=logs,
            real_admissibility="requires_confirmation",
        )
    except InterventionRequired as error:
        return dict(
            method=method,
            announcement_h=at,
            snapshot=state,
            committed=committed,
            updated_input=changed,
            result=dict(
                status="requires_intervention",
                accepted_t=None,
                shortfall_t=None,
                errors=[str(error)],
            ),
            solver=[],
            real_admissibility="requires_confirmation",
        )


def run_replanning(
    cfg: dict,
    event: dict,
    deadline: int,
    *,
    time_limit: float = 3,
    incumbent: list[dict] | None = None,
) -> dict:
    cfg = Scenario.from_dict(cfg).to_dict()
    # Validate on a copy. The initial planner still receives only original input.
    apply_event(cfg, event)
    # Crucially: the initial optimizer receives only the original input.
    initial = compare(cfg, deadline, time_limit=time_limit, incumbent=incumbent)
    if any(initial[k]["status"] != "valid_simulation" for k in ["baseline", "planner"]):
        raise InputError("Начальные планы должны быть допустимыми до сравнения перепланирования")
    revised_ready = replan(
        cfg,
        proposals(initial["baseline"]["trips"]),
        event,
        deadline,
        method="ready",
        time_limit=time_limit,
    )
    revised_milp = replan(
        cfg,
        proposals(initial["planner"]["trips"]),
        event,
        deadline,
        method="milp",
        time_limit=time_limit,
    )
    return dict(
        schema_version="1.0",
        experiment=(
            "single_arrival_delay"
            if event["type"] == "arrival_delay"
            else "single_vessel_unavailability"
        ),
        scenario_id=cfg["id"],
        deadline_h=deadline,
        initial_input=deepcopy(cfg),
        event=deepcopy(event),
        initial=initial,
        revised_baseline=revised_ready,
        revised_planner=revised_milp,
        information_policy="Both methods learn the update at the same hour; each preserves its own executed prefix",
        real_admissibility="requires_confirmation",
        actual_savings_rub=None,
    )
