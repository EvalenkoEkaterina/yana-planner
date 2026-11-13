"""Experimental single-vessel, two-recipient voyage with explicit priority."""

from copy import deepcopy
from math import isfinite
from time import monotonic

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .domain import InputError, integer

ASSUMPTIONS = ("navigation_assumed", "fleet_assumed", "supply_assumed", "reception_assumed")


def normalize(raw):
    if not isinstance(raw, dict) or raw.get("mode") != "SIM_ONLY":
        raise InputError("Прототип поддерживает только SIM_ONLY")
    c = deepcopy(raw)
    if any(c.get(k) is not True for k in ASSUMPTIONS):
        raise InputError("Нужно явно подтвердить все учебные допущения")
    c["horizon_h"] = integer(c.get("horizon_h"), "horizon_h", minimum=1)
    if c["horizon_h"] > 492:
        raise InputError("Горизонт прототипа — до 492 часов")
    if not isinstance(c.get("origin_id"), str) or not c["origin_id"].strip():
        raise InputError("Нужен origin_id")
    v = c.get("vessel")
    if not isinstance(v, dict) or not isinstance(v.get("id"), str) or not v["id"].strip():
        raise InputError("Нужно одно судно с id")
    for k in ("capacity_t", "load_h", "leg1_h", "leg2_h", "return_h", "available_h", "prep_h"):
        v[k] = integer(v.get(k), k, minimum=0 if k in ("available_h", "prep_h") else 1)
        if k != "capacity_t" and v[k] > 492:
            raise InputError("Время операции должно быть не больше 492 часов")
    c["initial_source_t"] = integer(c.get("initial_source_t"), "initial_source_t")
    if not isinstance(c.get("arrivals"), list) or len(c["arrivals"]) > 100:
        raise InputError("Нужен список до 100 поступлений")
    for a in c["arrivals"]:
        if not isinstance(a, dict):
            raise InputError("Поступление должно быть объектом")
        a["h"] = integer(a.get("h"), "arrival.h")
        a["t"] = integer(a.get("t"), "arrival.t", minimum=1)
    stops = c.get("stops")
    if not isinstance(stops, list) or len(stops) != 2:
        raise InputError("Нужны ровно два получателя в порядке посещения")
    ids = {c["origin_id"]}
    for stop in stops:
        if (
            not isinstance(stop, dict)
            or not isinstance(stop.get("id"), str)
            or not stop["id"].strip()
            or stop["id"] in ids
        ):
            raise InputError("Нужны разные идентификаторы отправителя и получателей")
        ids.add(stop["id"])
        for k in ("required_t", "deadline_h", "unload_h"):
            stop[k] = integer(stop.get(k), k, minimum=1)
        if stop["deadline_h"] > c["horizon_h"]:
            raise InputError("Срок приёмки должен быть в пределах горизонта")
        stop["windows"] = _intervals(stop.get("windows"), c["horizon_h"], nonempty=True)
    closures = c.get("movement_closures")
    if not isinstance(closures, dict) or set(closures) != {"leg1", "leg2", "return"}:
        raise InputError("Задайте запреты движения отдельно для leg1, leg2 и return")
    for key in closures:
        closures[key] = _intervals(closures[key], c["horizon_h"])
    return c


def _intervals(raw, horizon, nonempty=False):
    if not isinstance(raw, list) or len(raw) > 72 or (nonempty and not raw):
        raise InputError("Нужен список до 72 временных интервалов")
    result = []
    for interval in raw:
        if not isinstance(interval, list) or len(interval) != 2:
            raise InputError("Нужен интервал [начало, конец)")
        a, b = (integer(t, "interval") for t in interval)
        if a >= b or b > horizon:
            raise InputError("Интервал должен быть непустым и внутри горизонта")
        result.append([a, b])
    return result


def check_voyage(raw, plan):
    """Reconstruct times, inventories, windows and closures independently of the MILP."""
    c = normalize(raw)
    if not isinstance(plan, dict):
        raise InputError("Нужен план с шестью временами начала операций")
    keys = (
        "load_start_h",
        "depart1_h",
        "unload1_start_h",
        "depart2_h",
        "unload2_start_h",
        "depart_return_h",
    )
    times = [integer(plan.get(k), k) for k in keys]
    load, depart1, unload1, depart2, unload2, back = times
    v, stops = c["vessel"], c["stops"]
    q1, q2 = (s["required_t"] for s in stops)
    errors = []
    if q1 + q2 > v["capacity_t"]:
        errors.append("Превышена вместимость судна")
    stock = c["initial_source_t"] + sum(a["t"] for a in c["arrivals"] if a["h"] <= load)
    if stock < q1 + q2:
        errors.append("К началу погрузки недостаточно топлива")
    if load < v["available_h"]:
        errors.append("Судно ещё недоступно")
    if (
        depart1 < load + v["load_h"]
        or unload1 < depart1 + v["leg1_h"]
        or depart2 < unload1 + stops[0]["unload_h"]
        or unload2 < depart2 + v["leg2_h"]
        or back < unload2 + stops[1]["unload_h"]
    ):
        errors.append("Нарушена последовательность движения и операций")
    receipts = [unload1 + stops[0]["unload_h"], unload2 + stops[1]["unload_h"]]
    for i, (start, finish, stop) in enumerate(
        zip((unload1, unload2), receipts, stops, strict=True)
    ):
        if finish > stop["deadline_h"]:
            errors.append(f"Получатель {i + 1}: нарушен срок")
        if not any(a <= start and finish <= b for a, b in stop["windows"]):
            errors.append(f"Получатель {i + 1}: выгрузка вне окна приёмки")
    for leg, start, duration in [
        ("leg1", depart1, v["leg1_h"]),
        ("leg2", depart2, v["leg2_h"]),
        ("return", back, v["return_h"]),
    ]:
        if any(start < b and a < start + duration for a, b in c["movement_closures"][leg]):
            errors.append(f"{leg}: движение пересекает запрет")
    ready = back + v["return_h"] + v["prep_h"]
    if ready > c["horizon_h"] or any(t > c["horizon_h"] for t in times):
        errors.append("Полная готовность после возврата выходит за горизонт")
    return dict(
        status="valid_simulation" if not errors else "invalid_schedule",
        errors=errors,
        loaded_t=q1 + q2,
        deliveries=[
            dict(
                stop_id=s["id"],
                accepted_t=s["required_t"],
                receipt_h=receipts[i],
                cargo_after_t=q2 if i == 0 else 0,
            )
            for i, s in enumerate(stops)
        ],
        ready_h=ready,
        starts=dict(zip(keys, times, strict=True)),
    )


def plan_two_stop(raw, *, stage_limit_s=2):
    c = normalize(raw)
    if (
        isinstance(stage_limit_s, bool)
        or not isinstance(stage_limit_s, (int, float))
        or not isfinite(stage_limit_s)
        or stage_limit_s <= 0
    ):
        raise InputError("Нужен положительный лимит времени")
    v, stops, H = c["vessel"], c["stops"], c["horizon_h"]
    quantity = sum(s["required_t"] for s in stops)
    common = dict(
        mode="SIM_ONLY",
        goal="required_first_then_earliest_second",
        input=c,
        priority=["both_required_volumes", "first_receipt_deadline", "earliest_second_receipt"],
        real_admissibility="requires_confirmation",
    )
    stock, release = c["initial_source_t"], 0
    for arrival in sorted(c["arrivals"], key=lambda a: a["h"]):
        if stock >= quantity:
            break
        stock += arrival["t"]
        release = arrival["h"]
    if quantity > v["capacity_t"] or stock < quantity or release > H:
        return dict(
            **common,
            status="infeasible",
            goal_met=False,
            plan=None,
            solver=[],
            reason="Недостаточно вместимости или поступлений в пределах горизонта",
        )
    rows, lower, upper = [], [], []
    variable_count = 6
    binaries = []

    def binary():
        nonlocal variable_count
        index = variable_count
        variable_count += 1
        binaries.append(index)
        return index

    def add(entries, lo=-np.inf, hi=np.inf):
        rows.append(dict(entries))
        lower.append(lo)
        upper.append(hi)

    add({0: 1}, lo=max(v["available_h"], release))
    add({1: 1, 0: -1}, lo=v["load_h"])
    add({2: 1, 1: -1}, lo=v["leg1_h"])
    add({3: 1, 2: -1}, lo=stops[0]["unload_h"])
    add({4: 1, 3: -1}, lo=v["leg2_h"])
    add({5: 1, 4: -1}, lo=stops[1]["unload_h"])
    add({5: 1}, hi=H - v["return_h"] - v["prep_h"])
    M = 2 * H + max(v["leg1_h"], v["leg2_h"], v["return_h"], *(s["unload_h"] for s in stops))
    for index, stop in zip((2, 4), stops, strict=True):
        add({index: 1}, hi=stop["deadline_h"] - stop["unload_h"])
        selected = []
        for a, b in stop["windows"]:
            z = binary()
            selected.append(z)
            add({index: 1, z: -M}, lo=a - M)
            add({index: 1, z: M}, hi=b + M - stop["unload_h"])
        add({z: 1 for z in selected}, lo=1, hi=1)
    for leg, index, duration in [
        ("leg1", 1, v["leg1_h"]),
        ("leg2", 3, v["leg2_h"]),
        ("return", 5, v["return_h"]),
    ]:
        for a, b in c["movement_closures"][leg]:
            z = binary()
            add({index: 1, z: -M}, hi=a - duration)
            add({index: 1, z: -M}, lo=b - M)
    bounds_upper = np.full(variable_count, H, dtype=float)
    bounds_upper[binaries] = 1
    best, logs = None, []
    for objective_index, stage in [
        (4, "earliest_second_receipt"),
        (2, "earliest_first_at_fixed_second"),
        (5, "earliest_ready_at_fixed_receipts"),
    ]:
        matrix = np.zeros((len(rows), variable_count))
        for i, row in enumerate(rows):
            for j, value in row.items():
                matrix[i, j] = value
        objective = np.zeros(variable_count)
        objective[objective_index] = 1
        started = monotonic()
        result = milp(
            objective,
            integrality=np.ones(variable_count),
            bounds=Bounds(np.zeros(variable_count), bounds_upper),
            constraints=LinearConstraint(matrix, lower, upper),
            options=dict(time_limit=stage_limit_s, mip_rel_gap=0),
        )
        log = dict(
            stage=stage,
            status=int(result.status),
            optimal=result.status == 0,
            elapsed_s=monotonic() - started,
        )
        logs.append(log)
        if result.x is not None:
            keys = (
                "load_start_h",
                "depart1_h",
                "unload1_start_h",
                "depart2_h",
                "unload2_start_h",
                "depart_return_h",
            )
            verdict = check_voyage(
                c, dict(zip(keys, (int(round(t)) for t in result.x[:6]), strict=True))
            )
            if verdict["status"] == "valid_simulation":
                best = verdict
            else:
                log.update(optimal=False, checker_errors=verdict["errors"])
        if not log["optimal"] or best is None:
            break
        value = best["starts"][keys[objective_index]]
        add({objective_index: 1}, lo=value, hi=value)
    return dict(
        **common,
        status="goal_met"
        if best
        else "infeasible"
        if logs[0]["status"] == 2
        else "no_feasible_solution_found",
        goal_met=best is not None,
        earliest_second_proven=best is not None and logs[0]["optimal"],
        plan=best,
        solver=logs,
    )
