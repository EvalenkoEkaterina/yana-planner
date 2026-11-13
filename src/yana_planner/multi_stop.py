"""Experimental single-vessel, fixed-order multi-recipient voyage with explicit priority."""

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
    for k in ("capacity_t", "load_h", "return_h", "available_h", "prep_h"):
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
    if not isinstance(stops, list) or not 1 <= len(stops) <= 8:
        raise InputError("Нужны от 1 до 8 получателей в порядке посещения")
    travel = v.get("travel_h")
    legacy = "leg1_h" in v or "leg2_h" in v
    if travel is None and len(stops) == 2:
        travel = [v.get("leg1_h"), v.get("leg2_h")]
    if not isinstance(travel, list) or len(travel) != len(stops):
        raise InputError("travel_h: нужна длительность движения до каждого получателя")
    v["travel_h"] = [integer(h, "travel_h", minimum=1) for h in travel]
    if any(h > 492 for h in v["travel_h"]):
        raise InputError("Время движения должно быть не больше 492 часов")
    if legacy and (len(stops) != 2 or [v.get("leg1_h"), v.get("leg2_h")] != v["travel_h"]):
        raise InputError("Не смешивайте противоречивые travel_h и leg1_h/leg2_h")
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
    stop_ids = [stop["id"] for stop in stops]
    priorities = c.get("receipt_priority", [stop_ids[-1], *stop_ids[:-1]])
    if (
        not isinstance(priorities, list)
        or len(priorities) != len(stop_ids)
        or any(not isinstance(ident, str) for ident in priorities)
        or len(set(priorities)) != len(priorities)
        or set(priorities) != set(stop_ids)
    ):
        raise InputError(
            "receipt_priority: укажите каждого получателя ровно один раз в порядке важности"
        )
    c["receipt_priority"] = priorities
    closures = c.get("movement_closures")
    if not isinstance(closures, dict) or set(closures) != {
        *(f"leg{i + 1}" for i in range(len(stops))),
        "return",
    }:
        raise InputError("Задайте запреты для каждого leg1…legN и return")
    for key in closures:
        closures[key] = _intervals(closures[key], c["horizon_h"])
    if "leg_payload_limits" in c:
        limits = c["leg_payload_limits"]
        if not isinstance(limits, list) or len(limits) > len(stops):
            raise InputError("leg_payload_limits: не больше одного лимита на плечо")
        seen = set()
        for limit in limits:
            if not isinstance(limit, dict) or set(limit) != {
                "leg_id",
                "max_onboard_t",
                "status",
                "source",
            }:
                raise InputError("Лимит участка: нужны leg_id, max_onboard_t, status и source")
            leg = limit["leg_id"]
            if (
                not isinstance(leg, str)
                or leg not in {f"leg{i + 1}" for i in range(len(stops))}
                or leg in seen
            ):
                raise InputError("Лимит участка: неизвестное или повторяющееся плечо")
            seen.add(leg)
            limit["max_onboard_t"] = integer(limit["max_onboard_t"], "max_onboard_t")
            if limit["status"] not in ("scenario_assumption", "documented"):
                raise InputError("Лимит участка: нужен статус scenario_assumption или documented")
            if not isinstance(limit["source"], str) or not limit["source"].strip():
                raise InputError("Лимит участка: укажите источник или описание учебного допущения")
    return c


def _departure_checks(c):
    limits = {row["leg_id"]: row for row in c.get("leg_payload_limits", [])}
    cargo = sum(stop["required_t"] for stop in c["stops"])
    checks = []
    origin = c["origin_id"]
    for i, stop in enumerate(c["stops"]):
        leg = f"leg{i + 1}"
        if leg in limits:
            limit = limits[leg]
            checks.append(
                dict(
                    leg_id=leg,
                    from_id=origin,
                    to_id=stop["id"],
                    onboard_t=cargo,
                    max_onboard_t=limit["max_onboard_t"],
                    passed=cargo <= limit["max_onboard_t"],
                    status=limit["status"],
                    source=limit["source"],
                )
            )
        cargo -= stop["required_t"]
        origin = stop["id"]
    return checks


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


def _start_keys(count):
    return [
        "load_start_h",
        *[key for i in range(1, count + 1) for key in (f"depart{i}_h", f"unload{i}_start_h")],
        "depart_return_h",
    ]


def check_voyage(raw, plan):
    """Reconstruct every stop and remaining cargo independently of the MILP."""
    c = normalize(raw)
    if not isinstance(plan, dict):
        raise InputError("Нужен план с временами начала всех операций")
    v, stops = c["vessel"], c["stops"]
    keys = _start_keys(len(stops))
    times = [integer(plan.get(k), k) for k in keys]
    load, back = times[0], times[-1]
    quantity = sum(s["required_t"] for s in stops)
    errors = []
    if quantity > v["capacity_t"]:
        errors.append("Превышена вместимость судна")
    stock = c["initial_source_t"] + sum(a["t"] for a in c["arrivals"] if a["h"] <= load)
    if stock < quantity:
        errors.append("К началу погрузки недостаточно топлива")
    if load < v["available_h"]:
        errors.append("Судно ещё недоступно")
    previous_end, cargo = load + v["load_h"], quantity
    deliveries = []
    movements = []
    for i, stop in enumerate(stops):
        departure, start = times[1 + 2 * i], times[2 + 2 * i]
        duration = v["travel_h"][i]
        finish = start + stop["unload_h"]
        if departure < previous_end or start < departure + duration:
            errors.append(f"Получатель {i + 1}: нарушена последовательность движения и операций")
        if finish > stop["deadline_h"]:
            errors.append(f"Получатель {i + 1}: нарушен срок")
        if not any(a <= start and finish <= b for a, b in stop["windows"]):
            errors.append(f"Получатель {i + 1}: выгрузка вне окна приёмки")
        # Recheck the cargo before this departure, independently of preflight.
        for limit in c.get("leg_payload_limits", []):
            if limit["leg_id"] == f"leg{i + 1}" and cargo > limit["max_onboard_t"]:
                errors.append(
                    f"leg{i + 1}: груз {cargo} т превышает лимит {limit['max_onboard_t']} т перед отправлением"
                )
        cargo -= stop["required_t"]
        deliveries.append(
            dict(
                stop_id=stop["id"],
                accepted_t=stop["required_t"],
                receipt_h=finish,
                cargo_after_t=cargo,
            )
        )
        movements.append((f"leg{i + 1}", departure, duration))
        previous_end = finish
    if back < previous_end:
        errors.append("Возврат начался до окончания последней выгрузки")
    movements.append(("return", back, v["return_h"]))
    for leg, start, duration in movements:
        if any(start < b and a < start + duration for a, b in c["movement_closures"][leg]):
            errors.append(f"{leg}: движение пересекает запрет")
    ready = back + v["return_h"] + v["prep_h"]
    if ready > c["horizon_h"] or any(t > c["horizon_h"] for t in times):
        errors.append("Полная готовность после возврата выходит за горизонт")
    return dict(
        status="valid_simulation" if not errors else "invalid_schedule",
        errors=errors,
        loaded_t=quantity,
        deliveries=deliveries,
        ready_h=ready,
        starts=dict(zip(keys, times, strict=True)),
    )


def plan_multi_stop(raw, *, stage_limit_s=2, cost_terms=None):
    c = normalize(raw)
    if cost_terms is None:
        cost_terms = c.get("cost_terms")
    elif c.get("cost_terms") is not None and cost_terms != c["cost_terms"]:
        raise InputError("Переданы разные условия оплаты в JSON и аргументе")
    if (
        isinstance(stage_limit_s, bool)
        or not isinstance(stage_limit_s, (int, float))
        or not isfinite(stage_limit_s)
        or stage_limit_s <= 0
    ):
        raise InputError("Нужен положительный лимит времени")
    v, stops, H = c["vessel"], c["stops"], c["horizon_h"]
    count = len(stops)
    keys = _start_keys(count)
    back_index = 2 * count + 1
    quantity = sum(s["required_t"] for s in stops)
    priorities = c["receipt_priority"]
    primary_is_last = priorities[0] == stops[-1]["id"]
    common = dict(
        mode="SIM_ONLY",
        goal="required_deliveries_then_receipt_priorities",
        input=c,
        priority=["all_required_volumes", "all_receipt_deadlines", "receipt_priority"],
        receipt_priority=priorities,
        visit_order=[stop["id"] for stop in stops],
        objective_stop_id=priorities[0],
        real_admissibility="requires_confirmation",
        departure_checks=_departure_checks(c),
    )
    stock, release = c["initial_source_t"], 0
    for arrival in sorted(c["arrivals"], key=lambda a: a["h"]):
        if stock >= quantity:
            break
        stock += arrival["t"]
        release = arrival["h"]
    if quantity > v["capacity_t"] or stock < quantity or release > H:
        return _with_costs(
            dict(
                **common,
                status="infeasible",
                goal_met=False,
                plan=None,
                solver=[],
                primary_receipt_proven=False,
                lexicographic_optimal=False,
                receipt_optima=[],
                earliest_last_proven=False,
                **({"earliest_second_proven": False} if count == 2 else {}),
                reason="Недостаточно вместимости или поступлений в пределах горизонта",
            ),
            cost_terms,
        )
    failed = [check for check in common["departure_checks"] if not check["passed"]]
    if failed:
        return _with_costs(
            dict(
                **common,
                status="infeasible",
                goal_met=False,
                plan=None,
                solver=[],
                primary_receipt_proven=False,
                lexicographic_optimal=False,
                receipt_optima=[],
                earliest_last_proven=False,
                **({"earliest_second_proven": False} if count == 2 else {}),
                reason_code="leg_payload_limit_exceeded",
                reason="Груз на борту превышает заданный лимит участка; обязательные партии не уменьшены",
            ),
            cost_terms,
        )
    rows, lower, upper = [], [], []
    variable_count = len(keys)
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
    for i, stop in enumerate(stops):
        departure, unload = 1 + 2 * i, 2 + 2 * i
        previous = 0 if i == 0 else 2 * i
        duration = v["load_h"] if i == 0 else stops[i - 1]["unload_h"]
        add({departure: 1, previous: -1}, lo=duration)
        add({unload: 1, departure: -1}, lo=v["travel_h"][i])
    add({back_index: 1, 2 * count: -1}, lo=stops[-1]["unload_h"])
    add({back_index: 1}, hi=H - v["return_h"] - v["prep_h"])
    M = 2 * H + max(*v["travel_h"], v["return_h"], *(s["unload_h"] for s in stops))
    for i, stop in enumerate(stops):
        index = 2 + 2 * i
        add({index: 1}, hi=stop["deadline_h"] - stop["unload_h"])
        selected = []
        for a, b in stop["windows"]:
            z = binary()
            selected.append(z)
            add({index: 1, z: -M}, lo=a - M)
            add({index: 1, z: M}, hi=b + M - stop["unload_h"])
        add({z: 1 for z in selected}, lo=1, hi=1)
    for leg, index, duration in [
        *[(f"leg{i + 1}", 1 + 2 * i, h) for i, h in enumerate(v["travel_h"])],
        ("return", back_index, v["return_h"]),
    ]:
        for a, b in c["movement_closures"][leg]:
            z = binary()
            add({index: 1, z: -M}, hi=a - duration)
            add({index: 1, z: -M}, lo=b - M)
    bounds_upper = np.full(variable_count, H, dtype=float)
    bounds_upper[binaries] = 1
    best, logs = None, []
    stop_index = {stop["id"]: i for i, stop in enumerate(stops)}
    objectives = [(2 + 2 * stop_index[ident], f"earliest_receipt:{ident}") for ident in priorities]
    objectives.append((back_index, "earliest_ready_at_fixed_receipts"))
    for objective_index, stage in objectives:
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
        if objective_index != back_index:
            log["stop_id"] = stops[(objective_index - 2) // 2]["id"]
        logs.append(log)
        if result.x is not None:
            verdict = check_voyage(
                c, dict(zip(keys, (int(round(t)) for t in result.x[: len(keys)]), strict=True))
            )
            if verdict["status"] == "valid_simulation":
                best = verdict
                if objective_index != back_index:
                    log["receipt_h"] = verdict["deliveries"][(objective_index - 2) // 2][
                        "receipt_h"
                    ]
            else:
                log.update(optimal=False, checker_errors=verdict["errors"])
        if not log["optimal"] or best is None:
            break
        value = best["starts"][keys[objective_index]]
        add({objective_index: 1}, lo=value, hi=value)
    return _with_costs(
        dict(
            **common,
            status="goal_met"
            if best
            else "infeasible"
            if logs[0]["status"] == 2
            else "no_feasible_solution_found",
            goal_met=best is not None,
            primary_receipt_proven=best is not None and logs[0]["optimal"],
            lexicographic_optimal=best is not None
            and len(logs) == len(objectives)
            and all(log["optimal"] for log in logs),
            receipt_optima=[
                dict(
                    stop_id=ident,
                    priority=i + 1,
                    optimal_given_higher_priorities=best is not None
                    and len(logs) > i
                    and all(log["optimal"] for log in logs[: i + 1]),
                )
                for i, ident in enumerate(priorities)
            ],
            earliest_last_proven=primary_is_last and best is not None and logs[0]["optimal"],
            **(
                {
                    "earliest_second_proven": primary_is_last
                    and best is not None
                    and logs[0]["optimal"]
                }
                if count == 2
                else {}
            ),
            plan=best,
            solver=logs,
        ),
        cost_terms,
    )


def plan_two_stop(raw, *, stage_limit_s=2, cost_terms=None):
    """Compatibility entry point for the existing two-recipient prototype."""
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("stops"), list)
        or len(raw["stops"]) != 2
    ):
        raise InputError("Для plan_two_stop нужны ровно два получателя")
    return plan_multi_stop(raw, stage_limit_s=stage_limit_s, cost_terms=cost_terms)


def _with_costs(result, terms):
    if terms is not None:
        from .multi_stop_economics import price_voyage

        result["cost_terms"] = deepcopy(terms)
        result["costs"] = (
            price_voyage(result["input"], result["plan"]["starts"], terms)
            if result["plan"]
            else dict(status="no_valid_plan", total_rub=None, actual_savings_rub=None)
        )
    return result
