"""Online vessel-availability updates with immutable executed history."""

from __future__ import annotations

from copy import deepcopy

from .domain import InputError, InterventionRequired, Scenario, integer, proposals
from .planning import compare, optimize, ready
from .validation import check_schedule, snapshot


def apply_vessel_unavailability(cfg: dict, event: dict) -> dict:
    cfg = Scenario.from_dict(cfg).to_dict()
    if not isinstance(event, dict) or event.get("type") != "vessel_unavailability":
        raise InputError("Поддерживается событие type=vessel_unavailability")
    announced = integer(event.get("announced_at_h"), "announced_at_h")
    vessel_id = event.get("vessel_id")
    if not isinstance(vessel_id, str) or not vessel_id:
        raise InputError("Нужен vessel_id")
    interval = event.get("interval")
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        raise InputError("interval должен иметь вид [start, end)")
    start = integer(interval[0], "unavailable.start")
    end = integer(interval[1], "unavailable.end")
    if start >= end or end <= announced:
        raise InputError("Интервал должен быть положительным и иметь будущее после объявления")
    vessel = next((v for v in cfg["vessels"] if v["id"] == vessel_id), None)
    if vessel is None:
        raise InputError(f"Неизвестное судно: {vessel_id}")
    vessel["unavailable_intervals"] = sorted(
        vessel["unavailable_intervals"] + [[start, end]]
    )
    cfg["id"] = f"{cfg['id']}@{announced}"
    cfg["provenance"] = dict(
        cfg.get("provenance", {}),
        update=deepcopy(event),
        parent_id=cfg["id"].split("@")[0],
    )
    return Scenario.from_dict(cfg).to_dict()


def replan_unavailability(
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
    at = integer(event.get("announced_at_h"), "announced_at_h")
    state = snapshot(cfg, initial_plan, deadline, at)
    changed = apply_vessel_unavailability(cfg, event)
    committed = proposals(state["committed_trips"])
    try:
        # A committed voyage intersecting the newly announced outage is not rewritten.
        revised_ready = ready(changed, deadline, fixed=committed, decision_h=at + 1)
        if method == "milp":
            schedule, logs = optimize(
                changed,
                deadline,
                revised_ready,
                fixed=committed,
                decision_h=at + 1,
                time_limit=time_limit,
            )
        else:
            schedule, logs = revised_ready, []
        verdict = check_schedule(changed, schedule, deadline)
        if verdict["status"] != "valid_simulation":
            verdict["status"] = "no_feasible_solution_found"
        return dict(
            method=method,
            announcement_h=at,
            event=deepcopy(event),
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
            event=deepcopy(event),
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


def run_unavailability_experiment(
    cfg: dict,
    event: dict,
    deadline: int,
    *,
    time_limit: float = 10,
    reference: dict | None = None,
) -> dict:
    """Run both methods without revealing the outage before its announcement."""
    incumbent = None
    if reference:
        incumbent = reference.get("planner", {}).get("trips")
    initial = compare(cfg, deadline, time_limit=time_limit, incumbent=incumbent)
    if initial["baseline"]["status"] != "valid_simulation" or initial["planner"]["status"] != "valid_simulation":
        raise InputError("Исходные планы должны пройти независимую проверку")
    ready_result = replan_unavailability(
        cfg, initial["baseline"]["trips"], event, deadline,
        method="ready", time_limit=time_limit,
    )
    milp_result = replan_unavailability(
        cfg, initial["planner"]["trips"], event, deadline,
        method="milp", time_limit=time_limit,
    )
    return {
        "schema_version": "1.0",
        "experiment": "single_vessel_unavailability",
        "event": deepcopy(event),
        "deadline_h": deadline,
        "initial": initial,
        "revised_ready": ready_result,
        "revised_milp": milp_result,
        "information_policy": "The outage is disclosed only at announced_at_h; each method preserves its own started trips.",
        "real_admissibility": "requires_confirmation",
    }
