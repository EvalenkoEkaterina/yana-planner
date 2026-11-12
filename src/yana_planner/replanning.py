"""One information update with immutable historical dispatches."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import (
    ConfirmationRequired,
    InputError,
    InterventionRequired,
    Scenario,
    integer,
    proposals,
)
from .economics import cost
from .planning import compare, optimize, ready
from .validation import check_schedule, snapshot


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
    cfg["id"] = f"{cfg['id']}@{at}"
    cfg["provenance"] = dict(
        cfg.get("provenance", {}), update=deepcopy(event), parent_id=cfg["id"].split("@")[0]
    )
    return Scenario.from_dict(cfg).to_dict()


def _aware_time(value, name, timezone=None):
    if not isinstance(value, str):
        raise ConfirmationRequired(f"{name}: требуется дата и часовой пояс")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None and isinstance(timezone, str):
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise InputError(f"{name}: некорректная дата или часовой пояс") from error
    if parsed.tzinfo is None:
        raise ConfirmationRequired(f"{name}: укажите часовой пояс")
    return parsed


def apply_weather_closure(cfg: dict, event: dict) -> dict:
    """Apply explicit future restrictions; does not fetch or infer any weather."""
    cfg = Scenario.from_dict(cfg).to_dict()
    if not isinstance(event, dict) or event.get("type") != "weather_closure":
        raise InputError("Поддерживается событие type=weather_closure")
    at = integer(event.get("announced_at_h"), "announced_at_h")
    if event.get("rule_status") != "scenario_assumption":
        raise ConfirmationRequired("Погодное правило пока должно быть явно scenario_assumption")
    kind = event.get("data_kind")
    if kind not in ("synthetic", "reanalysis", "forecast"):
        raise ConfirmationRequired("Укажите data_kind: synthetic, reanalysis или forecast")
    if kind == "forecast":
        issued = _aware_time(event.get("forecast_issued_at"), "forecast_issued_at")
        t0 = _aware_time(cfg.get("t0"), "t0", cfg.get("timezone"))
        try:
            announcement = t0 + timedelta(hours=at)
        except OverflowError as error:
            raise InputError("Момент объявления выходит за допустимый диапазон дат") from error
        if issued > announcement:
            raise InputError(
                "Прогноз выпущен позже момента объявления; нельзя использовать будущее"
            )
    elif event.get("forecast_issued_at") is not None:
        raise InputError("Реанализ и синтетический сценарий не имеют времени выпуска прогноза")
    intervals = event.get("closures")
    if not isinstance(intervals, list):
        raise InputError("closures: требуется список интервалов [начало, конец)")
    added = []
    for interval in intervals:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise InputError("Погодный запрет: нужны две границы [начало, конец)")
        start = integer(interval[0], "weather.closure.start")
        end = integer(interval[1], "weather.closure.end")
        if start < at:
            raise InputError("Нельзя добавлять погодный запрет задним числом")
        if start >= end:
            raise InputError("Погодный запрет должен иметь положительную длительность")
        added.append([start, end])
    merged = []
    for start, end in sorted([*cfg["closures"], *added]):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parent_id = cfg["id"]
    cfg.update(id=f"{parent_id}@weather{at}", closures=merged)
    cfg["provenance"] = dict(cfg.get("provenance", {}), update=deepcopy(event), parent_id=parent_id)
    return Scenario.from_dict(cfg).to_dict()


def apply_event(cfg: dict, event: dict) -> dict:
    if not isinstance(event, dict):
        raise InputError("Событие должно быть JSON-объектом")
    if event.get("type") == "arrival_delay":
        return apply_arrival_delay(cfg, event)
    if event.get("type") == "weather_closure":
        return apply_weather_closure(cfg, event)
    raise InputError("Поддерживаются arrival_delay и weather_closure")


def replan(
    cfg: dict,
    initial_plan: list[dict],
    event: dict,
    deadline: int,
    *,
    method: str = "milp",
    time_limit: float = 3,
    adaptive: bool = False,
    strategy: str = "trips",
    cost_terms: dict | None = None,
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
            extra = (
                dict(strategy=strategy, cost_terms=cost_terms)
                if strategy != "trips" or cost_terms is not None
                else {}
            )
            schedule, logs = optimize(
                changed,
                deadline,
                fallback,
                fixed=committed,
                decision_h=at + 1,
                time_limit=time_limit,
                adaptive=adaptive,
                **extra,
            )
        else:
            schedule, logs = fallback, []
        verdict = check_schedule(changed, schedule, deadline)
        if verdict["status"] != "valid_simulation":
            verdict["status"] = "no_feasible_solution_found"
        output = dict(
            method=method,
            announcement_h=at,
            snapshot=state,
            committed=committed,
            updated_input=changed,
            result=verdict,
            solver=logs,
            real_admissibility="requires_confirmation",
        )
        if cost_terms is not None:
            output["cost"] = (
                cost(changed, schedule, deadline, cost_terms)
                if verdict["status"] == "valid_simulation"
                else None
            )
        return output
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
    adaptive: bool = False,
    strategy: str = "trips",
    cost_terms: dict | None = None,
) -> dict:
    """Replan after one event; already started trips remain immutable."""
    apply_event(cfg, event)  # Validate before solving, without revealing it to the initial planner.
    extra = (
        dict(strategy=strategy, cost_terms=cost_terms)
        if strategy != "trips" or cost_terms is not None
        else {}
    )
    # Crucially: the initial optimizer receives only the original input.
    initial = compare(
        cfg, deadline, time_limit=time_limit, incumbent=incumbent, adaptive=adaptive, **extra
    )
    if any(initial[k]["status"] != "valid_simulation" for k in ["baseline", "planner"]):
        raise InputError("Начальные планы должны быть допустимыми до сравнения перепланирования")
    revised_ready = replan(
        cfg,
        proposals(initial["baseline"]["trips"]),
        event,
        deadline,
        method="ready",
        time_limit=time_limit,
        **extra,
    )
    revised_milp = replan(
        cfg,
        proposals(initial["planner"]["trips"]),
        event,
        deadline,
        method="milp",
        time_limit=time_limit,
        adaptive=adaptive,
        **extra,
    )
    result = dict(
        target_t=cfg["target_t"],
        schema_version="1.0",
        experiment="single_weather_closure"
        if event["type"] == "weather_closure"
        else "single_arrival_delay",
        scenario_id=cfg["id"],
        deadline_h=deadline,
        original_input=deepcopy(cfg),
        event=deepcopy(event),
        initial=initial,
        revised_baseline=revised_ready,
        revised_planner=revised_milp,
        information_policy="Both methods learn the update at the same hour; each preserves its own executed prefix",
        real_admissibility="requires_confirmation",
        actual_savings_rub=None,
    )
    if extra:
        result.update(planning_strategy=strategy, objective_priority=initial["objective_priority"])
    if cost_terms is not None:
        a, b = revised_ready.get("cost"), revised_milp.get("cost")
        equal = (
            revised_ready["result"]["status"]
            == revised_milp["result"]["status"]
            == "valid_simulation"
            and revised_ready["result"]["accepted_t"] == revised_milp["result"]["accepted_t"]
        )
        difference = (
            float(Decimal(str(a["total_rub"])) - Decimal(str(b["total_rub"])))
            if equal and a and b and a["total_rub"] is not None and b["total_rub"] is not None
            else None
        )
        result.update(
            cost_terms=deepcopy(cost_terms),
            costs=dict(
                baseline=a,
                planner=b,
                equal_volume=equal,
                scenario_difference_rub=difference,
                actual_savings_rub=None,
            ),
        )
    return result
