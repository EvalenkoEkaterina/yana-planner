"""One information update with immutable historical dispatches."""

from __future__ import annotations

from copy import deepcopy

from .compat import normalize_event
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


def apply_payload_restriction(cfg: dict, event: dict) -> dict:
    """Overlay a temporary upper bound; preserve the original calendar outside it."""
    cfg = Scenario.from_dict(cfg).to_dict()
    if not isinstance(event, dict) or event.get("type") != "payload_restriction":
        raise InputError("Нужно событие type=payload_restriction")
    announced = integer(event.get("announced_at_h"), "announced_at_h")
    start = integer(event.get("from_h"), "from_h")
    end = integer(event.get("to_h"), "to_h")
    limit = integer(event.get("capacity_t"), "capacity_t")
    if start < announced:
        raise InputError("Ограничение партии не может начинаться до сообщения")
    if start >= end:
        raise InputError("Ограничение партии должно иметь положительную длительность")

    original = cfg["payload_calendar"]
    boundaries = sorted({start, end} | {point["from_h"] for point in original})
    updated = []
    cursor = 0
    for hour in boundaries:
        while cursor + 1 < len(original) and original[cursor + 1]["from_h"] <= hour:
            cursor += 1
        capacity = original[cursor]["capacity_t"]
        if start <= hour < end:
            capacity = min(capacity, limit)
        if not updated or updated[-1]["capacity_t"] != capacity:
            updated.append(dict(from_h=hour, capacity_t=capacity))
    cfg["payload_calendar"] = updated
    return _tag_update(cfg, event, announced)


def _payload_conflicts(cfg: dict, committed: list[dict], deadline: int) -> list[dict]:
    """Explain overloads using checker-reconstructed dates; never authorize a plan."""
    reconstructed = check_schedule(cfg, committed, deadline)
    calendar = cfg["payload_calendar"]
    fleet = {v["id"]: v for v in cfg["vessels"]}
    conflicts = []
    for trip in reconstructed.get("trips", []):
        intervals = []
        for index, point in enumerate(calendar):
            left = max(trip["load_end_h"], point["from_h"])
            right = min(
                trip["arrival_h"],
                calendar[index + 1]["from_h"] if index + 1 < len(calendar) else float("inf"),
            )
            if left < right and trip["quantity_t"] > point["capacity_t"]:
                intervals.append(dict(from_h=left, to_h=right, capacity_t=point["capacity_t"]))
        if intervals:
            conflicts.append(
                dict(
                    kind="payload_limit",
                    vessel_id=trip["vessel_id"],
                    start_h=trip["start_h"],
                    quantity_t=trip["quantity_t"],
                    allowed_capacity_t=min(
                        fleet[trip["vessel_id"]]["capacity_t"],
                        *(part["capacity_t"] for part in intervals),
                    ),
                    limiting_intervals=intervals,
                )
            )
    return conflicts


def apply_event(cfg: dict, event: dict) -> dict:
    event = normalize_event(event)
    if event.get("type") == "arrival_delay":
        return apply_arrival_delay(cfg, event)
    if event.get("type") == "vessel_unavailable":
        return apply_vessel_unavailable(cfg, event)
    if event.get("type") == "payload_restriction":
        return apply_payload_restriction(cfg, event)
    raise InputError(
        "Поддерживаются события arrival_delay, vessel_unavailable и payload_restriction"
    )


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
    source_event = deepcopy(event)
    event = normalize_event(event)
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
            event=deepcopy(event),
            source_event=source_event,
            announcement_h=at,
            snapshot=state,
            committed=committed,
            updated_input=changed,
            result=verdict,
            solver=logs,
            real_admissibility="requires_confirmation",
        )
    except InterventionRequired as error:
        conflicts = (
            _payload_conflicts(changed, committed, deadline)
            if event["type"] == "payload_restriction"
            else []
        )
        messages = [str(error)]
        for conflict in conflicts:
            messages.append(
                f"Судно {conflict['vessel_id']}: в начатом рейсе "
                f"{conflict['quantity_t']} т, допустимо {conflict['allowed_capacity_t']} т. "
                "Нужно решение диспетчера; начатая погрузка и её партия сохранены."
            )
        return dict(
            method=method,
            event=deepcopy(event),
            source_event=source_event,
            announcement_h=at,
            snapshot=state,
            committed=committed,
            updated_input=changed,
            result=dict(
                status="requires_intervention",
                accepted_t=None,
                shortfall_t=None,
                errors=messages,
                conflicts=conflicts,
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
    source_event = deepcopy(event)
    event = normalize_event(event)
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
        experiment={
            "arrival_delay": "single_arrival_delay",
            "vessel_unavailable": "single_vessel_unavailability",
            "payload_restriction": "single_payload_restriction",
        }[event["type"]],
        scenario_id=cfg["id"],
        deadline_h=deadline,
        initial_input=deepcopy(cfg),
        event=deepcopy(event),
        source_event=source_event,
        initial=initial,
        revised_baseline=revised_ready,
        revised_planner=revised_milp,
        information_policy="Both methods learn the update at the same hour; each preserves its own executed prefix",
        real_admissibility="requires_confirmation",
        actual_savings_rub=None,
    )
