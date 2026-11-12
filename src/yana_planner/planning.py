"""Time-indexed planning. The independent checker reconstructs every result."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from math import isfinite
from time import monotonic

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from .domain import (
    ConfirmationRequired,
    InputError,
    InterventionRequired,
    Scenario,
    integer,
    loading_hours,
    proposals,
)
from .economics import compare_cost, cost, resolve_cost_terms
from .validation import check_schedule

MAX_LOADING_CANDIDATES = 12_000


def _times(vessel, start, quantity):
    le = start + loading_hours(vessel, quantity)
    ar = le + vessel["up_h"]
    re = ar + vessel["unload_h"]
    rt = re + vessel["return_h"]
    return dict(
        load_end_h=le, arrival_h=ar, receipt_h=re, return_h=rt, ready_h=rt + vessel["prep_h"]
    )


def _matches(candidate, trip):
    return (
        candidate["vessel_id"] == trip["vessel_id"]
        and candidate["start_h"] == trip["start_h"]
        and candidate.get("min_t", 1) <= trip["quantity_t"] <= candidate["cap"]
    )


def candidates(cfg: dict, deadline: int, *, decision_h: int = 0) -> list[dict]:
    result = []
    calendar = cfg["payload_calendar"]
    for v in cfg["vessels"]:
        rate = Fraction(str(v["load_rate_tph"])) if "load_rate_tph" in v else None
        quantity_limit = min(
            v["capacity_t"],
            cfg["target_t"],
            cfg["initial_source_t"] + sum(a["t"] for a in cfg["arrivals"]),
        )
        for start in range(max(v["available_h"], decision_h), deadline + 1):
            if rate is None:
                durations = [v["load_h"]]
            elif quantity_limit:
                durations = range(
                    v["load_setup_h"] + 1,
                    min(
                        loading_hours(v, quantity_limit),
                        deadline - start - v["up_h"] - v["unload_h"],
                        cfg["return_cutoff_h"] - start - v["up_h"] - v["unload_h"] - v["return_h"],
                    )
                    + 1,
                )
            else:
                durations = []
            for duration in durations:
                le = start + duration
                ar = le + v["up_h"]
                re = ar + v["unload_h"]
                rt = re + v["return_h"]
                rd = rt + v["prep_h"]
                if re > deadline or rt > cfg["return_cutoff_h"]:
                    continue
                if any(
                    max(a, le) < min(b, ar) or max(a, re) < min(b, rt) for a, b in cfg["closures"]
                ):
                    continue
                caps = [
                    p["capacity_t"]
                    for i, p in enumerate(calendar)
                    if max(le, p["from_h"])
                    < min(ar, calendar[i + 1]["from_h"] if i + 1 < len(calendar) else float("inf"))
                ]
                minimum = 1
                cap = min(v["capacity_t"], *caps)
                if rate is not None:
                    transfer = duration - v["load_setup_h"]
                    minimum = ((transfer - 1) * rate.numerator) // rate.denominator + 1
                    cap = min(cap, quantity_limit, (transfer * rate.numerator) // rate.denominator)
                if cap >= minimum:
                    item = dict(
                        vessel_id=v["id"],
                        start_h=start,
                        cap=cap,
                        load_end_h=le,
                        arrival_h=ar,
                        receipt_h=re,
                        return_h=rt,
                        ready_h=rd,
                    )
                    if rate is not None:
                        item["min_t"] = minimum
                    result.append(item)
                    if len(result) > MAX_LOADING_CANDIDATES:
                        raise InputError(
                            "Слишком много вариантов погрузки. Сократите горизонт или число судов "
                            "для одного расчёта; проверьте единицы скорости (т/ч)."
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
    adaptive: bool = False,
    strategy: str = "trips",
    cost_terms: dict | None = None,
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
    if strategy not in ("trips", "cost"):
        raise InputError("strategy: выберите trips или cost")
    resolved = None
    if strategy == "cost":
        resolved = resolve_cost_terms(cfg, cost_terms)
        if resolved["missing"]:
            raise ConfirmationRequired(
                "Для экономической оптимизации заполните: " + ", ".join(resolved["missing"])
            )

    def turn_cost(vessel_id, duration):
        rates = resolved["vessel_rates"][vessel_id]["cents"]
        # Availability is a common constant; a turn replaces paid waiting.
        return rates["separate_trip_operations_rub"] + duration * (
            rates["active_vessel_hour_rub"] - rates["waiting_vessel_hour_rub"]
        )

    def rank(plan):
        end = max(
            (
                _times(fleet[p["vessel_id"]], p["start_h"], p["quantity_t"])["receipt_h"]
                for p in plan
            ),
            default=0,
        )
        secondary = (
            sum(
                turn_cost(
                    p["vessel_id"],
                    _times(fleet[p["vessel_id"]], p["start_h"], p["quantity_t"])["ready_h"]
                    - p["start_h"],
                )
                for p in plan
            )
            if strategy == "cost"
            else len(plan)
        )
        return (-sum(p["quantity_t"] for p in plan), secondary, end)

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
        if not any(_matches(c, p) for c in cand):
            cand.append(
                dict(
                    vessel_id=p["vessel_id"],
                    start_h=p["start_h"],
                    cap=p["quantity_t"],
                    **_times(v, p["start_h"], p["quantity_t"]),
                )
            )
    cand.sort(key=lambda c: (c["start_h"], c["vessel_id"]))
    trip_costs = (
        [turn_cost(c["vessel_id"], c["ready_h"] - c["start_h"]) for c in cand]
        if resolved is not None
        else []
    )
    if resolved is not None:
        latest_ready = max((c["ready_h"] for c in cand), default=0)
        if resolved["horizon_h"] < latest_ready:
            raise InputError(
                f"Для оптимизации горизонт оплаты должен покрывать все варианты рейсов: "
                f"не менее {latest_ready} ч. Он не должен сокращать допустимые перевозки."
            )
        if sum(abs(value) for value in trip_costs) > 2**53 - 1:
            raise InputError("Ставки слишком велики для точного денежного сравнения в MILP")
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
        matrix.add([(i, c.get("min_t", 1)), (n + i, -1)], upper=0)
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
        i = next(i for i, c in enumerate(cand) if _matches(c, p))
        lower[i] = upper[i] = 1
        lower[n + i] = upper[n + i] = p["quantity_t"]
    integral = np.array([1] * (2 * n) + [0])
    logs = []

    def attempt(objective, budget):
        started = monotonic()
        result = milp(
            objective,
            integrality=integral,
            bounds=Bounds(lower, upper),
            constraints=matrix.constraint(),
            options={"time_limit": budget, "mip_rel_gap": 0, "presolve": True},
        )
        record = dict(
            status=int(result.status),
            optimal=result.status == 0,
            message=result.message,
            mip_gap=getattr(result, "mip_gap", None),
            dual_bound=getattr(result, "mip_dual_bound", None),
            time_limit_s=budget,
            elapsed_s=monotonic() - started,
        )
        if result.x is None:
            record["optimal"] = False
            return None, record
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
            record["checker_errors"] = verdict["errors"] or ["history changed"]
            record["optimal"] = False
            return None, record
        return proposals(plan), record

    def solve(objective, stage, component):
        if not adaptive:
            plan, record = attempt(objective, time_limit)
            logs.append(dict(stage=stage, **record))
            return plan
        # SciPy/HiGHS has no resumable search API. Restart with a tighter bound
        # from the independently checked incumbent, retaining that plan outside the solver.
        stage_best = best
        attempts = []
        started = monotonic()
        budgets = [min(2, time_limit)]
        if time_limit > 2:
            budgets.append(min(8, time_limit - 2))
        if time_limit > 10:
            budgets.append(time_limit - 10)
        entries = [(int(i), float(objective[i])) for i in np.flatnonzero(objective)]
        for budget in budgets:
            remaining = time_limit - (monotonic() - started)
            if remaining <= 0:
                break
            if stage_best is not None:
                matrix.add(entries, upper=rank(stage_best)[component])
            plan, record = attempt(objective, min(budget, remaining))
            if plan is not None and (stage_best is None or rank(plan) < rank(stage_best)):
                stage_best = plan
            record["best_objective"] = (
                rank(stage_best)[component] if stage_best is not None else None
            )
            attempts.append(record)
            if record["optimal"] or record["status"] != 1:
                break
        if not attempts:
            # Budget is positive, but still preserve a known plan if preparation
            # consumed it before the first call.
            attempts = [dict(status=1, optimal=False, message="Stage budget exhausted")]
        logs.append(
            dict(
                stage=stage,
                **attempts[-1],
                adaptive=True,
                attempts=attempts,
                budget_s=time_limit,
                total_elapsed_s=monotonic() - started,
            )
        )
        return stage_best

    objective = np.zeros(2 * n + 1)
    objective[n : 2 * n] = -1
    p = solve(objective, "maximize_tonnes", 0)
    if p is not None and (best is None or rank(p) < rank(best)):
        best = p
    if best is None:
        return [], logs
    tonnes = -rank(best)[0]
    matrix.add([(n + i, 1) for i in range(n)], lower=tonnes, upper=tonnes)
    objective = np.zeros(2 * n + 1)
    objective[:n] = trip_costs if strategy == "cost" else 1
    secondary_stage = (
        "minimize_cost_at_fixed_tonnes" if strategy == "cost" else "minimize_trips_at_fixed_tonnes"
    )
    p = solve(objective, secondary_stage, 1)
    if p is not None and rank(p) < rank(best):
        best = p
    secondary_value = rank(best)[1]
    matrix.add(
        [(i, float(objective[i])) for i in range(n)],
        lower=secondary_value,
        upper=secondary_value,
    )
    upper[-1] = rank(best)[2]
    objective = np.zeros(2 * n + 1)
    objective[-1] = 1
    final_stage = (
        "minimize_last_receipt_at_fixed_tonnes_cost"
        if strategy == "cost"
        else "minimize_last_receipt_at_fixed_tonnes_trips"
    )
    p = solve(objective, final_stage, 2)
    if p is not None and rank(p) < rank(best):
        best = p
    return proposals(best), logs


def compare(
    cfg: dict,
    deadline: int,
    *,
    time_limit: float = 3,
    incumbent: list[dict] | None = None,
    adaptive: bool = False,
    strategy: str = "trips",
    cost_terms: dict | None = None,
) -> dict:
    """Run READY and MILP, independently validating tonnes and hours."""
    scenario = Scenario.from_dict(cfg)
    cfg = scenario.to_dict()
    baseline = ready(cfg, deadline)
    kwargs = dict(time_limit=time_limit, incumbent=incumbent, adaptive=adaptive)
    if strategy != "trips" or cost_terms is not None:
        kwargs.update(strategy=strategy, cost_terms=cost_terms)
    plan, logs = optimize(cfg, deadline, baseline, **kwargs)
    a = check_schedule(cfg, baseline, deadline)
    b = check_schedule(cfg, plan, deadline)
    if b["status"] != "valid_simulation":
        b["status"] = (
            "infeasible" if logs and logs[0].get("status") == 2 else "no_feasible_solution_found"
        )
    result = dict(
        target_t=cfg["target_t"],
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
    if any("load_rate_tph" in v for v in cfg["vessels"]):
        result["loading_models"] = [
            dict(
                vessel_id=v["id"],
                mode="rate",
                load_rate_tph=v["load_rate_tph"],
                load_setup_h=v["load_setup_h"],
            )
            if "load_rate_tph" in v
            else dict(vessel_id=v["id"], mode="fixed", load_h=v["load_h"])
            for v in cfg["vessels"]
        ]
    if strategy != "trips" or cost_terms is not None:
        result.update(
            planning_strategy=strategy,
            objective_priority=[
                "accepted_t",
                "total_rub" if strategy == "cost" else "trip_count",
                "last_receipt_h",
            ],
        )
    if cost_terms is not None:
        equal = (
            a["status"] == b["status"] == "valid_simulation" and a["accepted_t"] == b["accepted_t"]
        )
        if equal:
            priced = compare_cost(cfg, baseline, plan, deadline, cost_terms)
            costs = dict(
                baseline=priced["baseline"],
                planner=priced["alternative"],
                scenario_difference_rub=priced["scenario_difference_rub"],
            )
        else:
            costs = dict(
                baseline=cost(cfg, baseline, deadline, cost_terms)
                if a["status"] == "valid_simulation"
                else None,
                planner=cost(cfg, plan, deadline, cost_terms)
                if b["status"] == "valid_simulation"
                else None,
                scenario_difference_rub=None,
            )
        result.update(
            cost_terms=deepcopy(cost_terms),
            costs=dict(
                **costs,
                equal_volume=equal,
                actual_savings_rub=None,
            ),
        )
    return result
