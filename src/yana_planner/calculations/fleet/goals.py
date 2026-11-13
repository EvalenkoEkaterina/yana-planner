"""Required-volume earliest completion, using the independently checked MILP core."""

from math import isfinite
from time import monotonic

from yana_planner.domain import InputError, Scenario, integer
from yana_planner.planning import compare


def earliest_required_volume(cfg, deadline, *, total_budget_s=10, stage_limit_s=2):
    """Find full-volume feasibility over deadlines; unproved failures never prune time.

    Partial schedules are diagnostics, never successful fulfilment of the goal.
    The outer budget bounds solver allowances, not OS process/preparation time.
    Web callers must retain their existing worker wall-clock timeout.
    """
    cfg = Scenario.from_dict(cfg).to_dict()
    deadline = integer(deadline, "deadline_h", minimum=1)
    if deadline > cfg["return_cutoff_h"]:
        raise InputError("Приёмка должна быть не позже срока возврата")
    if cfg["target_t"] <= 0:
        raise InputError("Для этого режима задайте положительный обязательный объём")
    for value in (total_budget_s, stage_limit_s):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value <= 0
        ):
            raise InputError("Бюджет поиска должен быть положительным конечным числом")
    started = monotonic()
    trials = []
    best, partial = None, None
    lower, upper = 1, deadline
    stopped = None

    def evaluate(bound):
        remaining = total_budget_s - (monotonic() - started)
        if remaining <= 0:
            return None
        result = compare(cfg, bound, time_limit=min(stage_limit_s, remaining / 3))
        plan = result["planner"]
        full = plan["status"] == "valid_simulation" and plan["accepted_t"] == cfg["target_t"]
        proved_shortfall = (
            not full
            and bool(result["solver"])
            and any(
                s["stage"] in ("maximize_tonnes", "no_candidates") and s.get("optimal")
                for s in result["solver"]
            )
        )
        trials.append(
            dict(
                deadline_h=bound,
                full_volume=full,
                shortfall_proven=proved_shortfall,
                solver=result["solver"],
            )
        )
        return result, full, proved_shortfall

    first = evaluate(deadline)
    if first is None:
        stopped = "budget_exhausted"
    else:
        result, full, proved = first
        if not full:
            partial = result
            stopped = "required_volume_infeasible" if proved else "full_volume_not_found"
        else:
            best = result
            upper = result["planner"]["last_receipt_h"]
    while best is not None and lower < upper:
        midpoint = (lower + upper) // 2
        attempt = evaluate(midpoint)
        if attempt is None:
            stopped = "budget_exhausted"
            break
        result, full, proved = attempt
        if full:
            best = result
            upper = result["planner"]["last_receipt_h"]
        elif proved:
            lower = midpoint + 1
        else:
            stopped = "shorter_deadline_unresolved"
            break
    return dict(
        goal="required_volume_earliest",
        required_t=cfg["target_t"],
        goal_met=best is not None,
        status="goal_met" if best is not None else stopped,
        earliest_proven=best is not None and lower == upper,
        last_receipt_h=best["planner"]["last_receipt_h"] if best is not None else None,
        plan=best["planner"] if best is not None else None,
        best_partial=partial["planner"] if partial is not None else None,
        stop_reason=stopped,
        trials=trials,
        elapsed_s=monotonic() - started,
        objective_priority=["required_volume_constraint", "last_receipt_h", "trip_count"],
        scope="Single origin and destination; manual travel times; SIM_ONLY",
    )
