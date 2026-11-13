"""Sequential READY baseline; no optimization or HTTP handling."""

from copy import deepcopy

from yana_planner.domain import InputError, InterventionRequired, Scenario, integer, proposals
from yana_planner.validation import check_schedule

from .candidates import build_candidates as candidates
from .timing import _times


def _prefix_config(cfg: dict, deadline_of_partial_plan: int) -> dict:
    # In a partial construction, future off-takes still need later trips. They do
    # not release space retroactively; the complete checker always sees all events.
    partial = deepcopy(cfg)
    partial["withdrawals"] = [w for w in cfg["withdrawals"] if w["h"] <= deadline_of_partial_plan]
    return partial


def _validate_fixed(cfg: dict, fixed: list[dict], deadline: int, decision_h: int) -> list[dict]:
    fixed = proposals(fixed)
    if any(p["start_h"] > decision_h for p in fixed):
        raise InputError("В fixed допустимы только уже начатые рейсы")
    if fixed:
        fleet = {v["id"]: v for v in cfg["vessels"]}
        if any(p["vessel_id"] not in fleet for p in fixed):
            raise InputError("Неизвестное судно в fixed")
        end = max(
            _times(fleet[p["vessel_id"]], p["start_h"], p["quantity_t"])["receipt_h"] for p in fixed
        )
        verdict = check_schedule(_prefix_config(cfg, end), fixed, deadline)
        if verdict["status"] != "valid_simulation":
            raise InterventionRequired(
                "Начатые рейсы нарушают новые условия: " + "; ".join(verdict["errors"])
            )
    return fixed


def ready(
    cfg: dict, deadline: int, *, fixed: list[dict] | None = None, decision_h: int = 0
) -> list[dict]:
    cfg = Scenario.from_dict(cfg).to_dict()
    deadline = integer(deadline, "deadline_h")
    decision_h = integer(decision_h, "decision_h")
    plan = _validate_fixed(cfg, fixed or [], deadline, decision_h)
    fleet = {v["id"]: v for v in cfg["vessels"]}
    fixed_keys = {(p["vessel_id"], p["start_h"]) for p in plan}
    ordered = sorted(
        candidates(cfg, deadline, decision_h=decision_h),
        key=lambda c: (c["start_h"], c["vessel_id"], -c["cap"]),
    )
    for c in ordered:
        if (c["vessel_id"], c["start_h"]) in fixed_keys:
            continue
        available = (
            cfg["initial_source_t"]
            + sum(a["t"] for a in cfg["arrivals"] if a["h"] <= c["start_h"])
            - sum(p["quantity_t"] for p in plan)
        )
        arrivals = [
            (
                _times(fleet[p["vessel_id"]], p["start_h"], p["quantity_t"])["arrival_h"],
                p["quantity_t"],
            )
            for p in plan
        ]
        future_points = {c["arrival_h"]} | {h for h, _ in arrivals if h >= c["arrival_h"]}
        # Also preserve the space reserved by a previously selected, slower ship
        # arriving later. A fast candidate may use only the spare capacity over
        # every future reservation point, not merely the space free on arrival.
        free = min(
            cfg["destination_capacity_t"]
            - sum(q for h, q in arrivals if h <= point)
            + sum(w["t"] for w in cfg["withdrawals"] if w["h"] <= point)
            for point in future_points
        )
        q = min(c["cap"], available, cfg["target_t"] - sum(p["quantity_t"] for p in plan), free)
        if q < c.get("min_t", 1):
            continue
        candidate = dict(vessel_id=c["vessel_id"], start_h=c["start_h"], quantity_t=q)
        trial = plan + [candidate]
        end = max(
            _times(fleet[p["vessel_id"]], p["start_h"], p["quantity_t"])["receipt_h"] for p in trial
        )
        if (
            check_schedule(_prefix_config(cfg, end), trial, deadline)["status"]
            == "valid_simulation"
        ):
            plan.append(candidate)
            fixed_keys.add((c["vessel_id"], c["start_h"]))
    return proposals(plan)
