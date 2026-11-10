"""Time-indexed planning. The independent checker reconstructs every result."""

from __future__ import annotations

from copy import deepcopy
from math import isfinite

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from .domain import InputError, InterventionRequired, Scenario, integer, proposals
from .validation import check_schedule


def candidates(cfg: dict, deadline: int, *, decision_h: int = 0) -> list[dict]:
    result = []
    calendar = cfg["payload_calendar"]
    for v in cfg["vessels"]:
        for start in range(max(v["available_h"], decision_h), deadline + 1):
            le = start + v["load_h"]
            ar = le + v["up_h"]
            re = ar + v["unload_h"]
            rt = re + v["return_h"]
            rd = rt + v["prep_h"]
            if re > deadline or rt > cfg["return_cutoff_h"]:
                continue
            # A vessel outage blocks its complete turnaround, including preparation.
            if any(max(start, a) < min(rd, b) for a, b in v["unavailable_intervals"]):
                continue
            if any(max(a, le) < min(b, ar) or max(a, re) < min(b, rt) for a, b in cfg["closures"]):
                continue
            caps = [
                p["capacity_t"]
                for i, p in enumerate(calendar)
                if max(le, p["from_h"])
                < min(ar, calendar[i + 1]["from_h"] if i + 1 < len(calendar) else float("inf"))
            ]
            cap = min(v["capacity_t"], *caps)
            if cap:
                result.append(
                    dict(
                        vessel_id=v["id"],
                        start_h=start,
                        cap=cap,
                        load_end_h=le,
                        arrival_h=ar,
                        receipt_h=re,
                        return_h=rt,
                        ready_h=rd,
                    )
                )
    return sorted(result, key=lambda x: (x["start_h"], x["vessel_id"]))


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
            p["start_h"] + sum(fleet[p["vessel_id"]][k] for k in ["load_h", "up_h", "unload_h"])
            for p in fixed
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
    for c in candidates(cfg, deadline, decision_h=decision_h):
        if (c["vessel_id"], c["start_h"]) in fixed_keys:
            continue
        available = (
            cfg["initial_source_t"]
            + sum(a["t"] for a in cfg["arrivals"] if a["h"] <= c["start_h"])
            - sum(p["quantity_t"] for p in plan)
        )
        arrivals = [
            (
                p["start_h"] + fleet[p["vessel_id"]]["load_h"] + fleet[p["vessel_id"]]["up_h"],
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
        if q < 1:
            continue
        candidate = dict(vessel_id=c["vessel_id"], start_h=c["start_h"], quantity_t=q)
        trial = plan + [candidate]
        end = max(
            p["start_h"] + sum(fleet[p["vessel_id"]][k] for k in ["load_h", "up_h", "unload_h"])
            for p in trial
        )
        if (
            check_schedule(_prefix_config(cfg, end), trial, deadline)["status"]
            == "valid_simulation"
        ):
            plan.append(candidate)
    return proposals(plan)


class _Matrix:
    def __init__(self, columns: int):
        self.columns = columns
        self.rows: list[int] = []
        self.cols: list[int] = []
        self.values: list[float] = []
        self.lower: list[float] = []
        self.upper: list[float] = []

    def add(self, entries, *, lower=-np.inf, upper=np.inf):
        index = len(self.lower)
        for column, value in entries:
            if value:
                self.rows.append(index)
                self.cols.append(column)
                self.values.append(value)
        self.lower.append(lower)
        self.upper.append(upper)

    def constraint(self):
        sparse = coo_matrix(
            (self.values, (self.rows, self.cols)), shape=(len(self.lower), self.columns)
        ).tocsc()
        return LinearConstraint(sparse, np.array(self.lower), np.array(self.upper))


def optimize(
    cfg: dict,
    deadline: int,
    baseline: list[dict] | None = None,
    *,
    time_limit: float = 3,
    incumbent: list[dict] | None = None,
    fixed: list[dict] | None = None,
    decision_h: int = 0,
) -> tuple[list[dict], list[dict]]:
    cfg = Scenario.from_dict(cfg).to_dict()
    deadline = integer(deadline, "deadline_h")
    decision_h = integer(decision_h, "decision_h")
    if (
        isinstance(time_limit, bool)
        or not isinstance(time_limit, (int, float))
        or not isfinite(time_limit)
        or time_limit <= 0
    ):
        raise InputError("time_limit должен быть положительным числом секунд")
    fixed = _validate_fixed(cfg, fixed or [], deadline, decision_h)
    fleet = {v["id"]: v for v in cfg["vessels"]}

    def rank(plan):
        end = max(
            (
                p["start_h"] + sum(fleet[p["vessel_id"]][k] for k in ["load_h", "up_h", "unload_h"])
                for p in plan
            ),
            default=0,
        )
        return (-sum(p["quantity_t"] for p in plan), len(plan), end)

    def preserves_history(plan):
        return all(p in plan for p in fixed) and all(
            p in fixed or p["start_h"] >= decision_h for p in plan
        )

    best = None
    for plan in [
        baseline
        if baseline is not None
        else ready(cfg, deadline, fixed=fixed, decision_h=decision_h),
        incumbent,
    ]:
        if plan is None:
            continue
        try:
            plan = proposals(plan)
        except InputError:
            continue
        if (
            preserves_history(plan)
            and check_schedule(cfg, plan, deadline)["status"] == "valid_simulation"
            and (best is None or rank(plan) < rank(best))
        ):
            best = plan
    cand = candidates(cfg, deadline, decision_h=decision_h)
    for p in fixed:
        v = fleet[p["vessel_id"]]
        le = p["start_h"] + v["load_h"]
        ar = le + v["up_h"]
        re = ar + v["unload_h"]
        rt = re + v["return_h"]
        if not any(c["vessel_id"] == p["vessel_id"] and c["start_h"] == p["start_h"] for c in cand):
            cand.append(
                dict(
                    vessel_id=p["vessel_id"],
                    start_h=p["start_h"],
                    cap=p["quantity_t"],
                    load_end_h=le,
                    arrival_h=ar,
                    receipt_h=re,
                    return_h=rt,
                    ready_h=rt + v["prep_h"],
                )
            )
    cand.sort(key=lambda c: (c["start_h"], c["vessel_id"]))
    n = len(cand)
    if not n:
        return best or [], [
            dict(
                stage="no_candidates",
                status=0 if best is not None else 2,
                optimal=best is not None,
                message="No departure can meet the receipt/return constraints",
            )
        ]
    matrix = _Matrix(2 * n + 1)
    for i, c in enumerate(cand):
        matrix.add([(n + i, 1), (i, -c["cap"])], upper=0)
        matrix.add([(i, 1), (n + i, -1)], upper=0)
        matrix.add([(i, c["receipt_h"]), (2 * n, -1)], upper=0)
    horizon = max(c["ready_h"] for c in cand)
    for vid in fleet:
        for h in range(horizon):
            entries = [
                (i, 1)
                for i, c in enumerate(cand)
                if c["vessel_id"] == vid and c["start_h"] <= h < c["ready_h"]
            ]
            if entries:
                matrix.add(entries, upper=1)
    for start, end in [("start_h", "load_end_h"), ("arrival_h", "receipt_h")]:
        for h in range(horizon):
            entries = [(i, 1) for i, c in enumerate(cand) if c[start] <= h < c[end]]
            if entries:
                matrix.add(entries, upper=1)
    for h in sorted({a["h"] for a in cfg["arrivals"]}):
        matrix.add(
            [(n + i, 1) for i, c in enumerate(cand) if c["start_h"] < h],
            upper=cfg["initial_source_t"] + sum(a["t"] for a in cfg["arrivals"] if a["h"] < h),
        )
    matrix.add(
        [(n + i, 1) for i in range(n)],
        lower=-rank(best)[0] if best is not None else 0,
        upper=min(cfg["target_t"], cfg["initial_source_t"] + sum(a["t"] for a in cfg["arrivals"])),
    )
    if not cfg["withdrawals"]:
        matrix.add([(n + i, 1) for i in range(n)], upper=cfg["destination_capacity_t"])
    else:
        # Capacity is reserved at arrival. Confirmed withdrawals release space
        # only at their time; receipts must physically cover each withdrawal.
        for h in sorted({c["arrival_h"] for c in cand} | {w["h"] for w in cfg["withdrawals"]}):
            out = sum(w["t"] for w in cfg["withdrawals"] if w["h"] <= h)
            matrix.add(
                [(n + i, 1) for i, c in enumerate(cand) if c["arrival_h"] <= h],
                upper=cfg["destination_capacity_t"] + out,
            )
        for h in sorted({w["h"] for w in cfg["withdrawals"]}):
            out = sum(w["t"] for w in cfg["withdrawals"] if w["h"] <= h)
            matrix.add([(n + i, 1) for i, c in enumerate(cand) if c["receipt_h"] <= h], lower=out)
    lower = np.zeros(2 * n + 1)
    upper = np.array([1] * n + [c["cap"] for c in cand] + [deadline], dtype=float)
    for p in fixed:
        i = next(
            i
            for i, c in enumerate(cand)
            if c["vessel_id"] == p["vessel_id"] and c["start_h"] == p["start_h"]
        )
        lower[i] = upper[i] = 1
        lower[n + i] = upper[n + i] = p["quantity_t"]
    integral = np.array([1] * (2 * n) + [0])
    logs = []

    def solve(objective, stage):
        result = milp(
            objective,
            integrality=integral,
            bounds=Bounds(lower, upper),
            constraints=matrix.constraint(),
            options={"time_limit": time_limit, "mip_rel_gap": 0, "presolve": True},
        )
        logs.append(
            dict(
                stage=stage,
                status=int(result.status),
                optimal=result.status == 0,
                message=result.message,
                mip_gap=getattr(result, "mip_gap", None),
                dual_bound=getattr(result, "mip_dual_bound", None),
            )
        )
        if result.x is None:
            return None
        plan = [
            dict(
                vessel_id=c["vessel_id"],
                start_h=c["start_h"],
                quantity_t=int(round(result.x[n + i])),
            )
            for i, c in enumerate(cand)
            if result.x[i] > 0.5
        ]
        verdict = check_schedule(cfg, plan, deadline)
        if verdict["status"] != "valid_simulation" or not preserves_history(plan):
            logs[-1]["checker_errors"] = verdict["errors"] or ["history changed"]
            return None
        return proposals(plan)

    objective = np.zeros(2 * n + 1)
    objective[n : 2 * n] = -1
    p = solve(objective, "maximize_tonnes")
    if p is not None and (best is None or rank(p) < rank(best)):
        best = p
    if best is None:
        return [], logs
    tonnes = -rank(best)[0]
    matrix.add([(n + i, 1) for i in range(n)], lower=tonnes, upper=tonnes)
    objective = np.zeros(2 * n + 1)
    objective[:n] = 1
    p = solve(objective, "minimize_trips_at_fixed_tonnes")
    if p is not None and rank(p) < rank(best):
        best = p
    count = len(best)
    matrix.add([(i, 1) for i in range(n)], lower=count, upper=count)
    upper[-1] = rank(best)[2]
    objective = np.zeros(2 * n + 1)
    objective[-1] = 1
    p = solve(objective, "minimize_last_receipt_at_fixed_tonnes_trips")
    if p is not None and rank(p) < rank(best):
        best = p
    return proposals(best), logs


def compare(
    cfg: dict, deadline: int, *, time_limit: float = 3, incumbent: list[dict] | None = None
) -> dict:
    scenario = Scenario.from_dict(cfg)
    cfg = scenario.to_dict()
    baseline = ready(cfg, deadline)
    plan, logs = optimize(cfg, deadline, baseline, time_limit=time_limit, incumbent=incumbent)
    a = check_schedule(cfg, baseline, deadline)
    b = check_schedule(cfg, plan, deadline)
    if b["status"] != "valid_simulation":
        b["status"] = (
            "infeasible" if logs and logs[0].get("status") == 2 else "no_feasible_solution_found"
        )
    return dict(
        schema_version="1.0",
        scenario_id=cfg["id"],
        input_sha256=scenario.fingerprint,
        deadline_h=deadline,
        baseline=a,
        planner=b,
        solver=logs,
        real_admissibility="requires_confirmation",
        actual_savings_rub=None,
    )
