"""Explicit scenario costing, independent of procurement NMC prices."""

from math import isfinite

from .domain import InputError, Scenario
from .validation import check_schedule


def cost(cfg: dict, plan: list[dict], deadline: int, terms: dict) -> dict:
    cfg = Scenario.from_dict(cfg).to_dict()
    verdict = check_schedule(cfg, plan, deadline)
    if verdict["status"] != "valid_simulation":
        raise InputError("Стоимость требует допустимое расписание")
    fields = ["separate_trip_operations_rub", "active_vessel_hour_rub", "waiting_vessel_hour_rub"]
    missing = [k for k in fields if terms.get(k) is None]
    if missing or terms.get("billing_mode") is None or terms.get("horizon_h") is None:
        return dict(
            status="missing_cost_inputs",
            total_rub=None,
            missing=missing + [k for k in ["billing_mode", "horizon_h"] if terms.get(k) is None],
        )
    if terms["billing_mode"] != "all_available_time":
        raise InputError("Поддерживается только явно выбранная схема all_available_time")
    for name in fields + ["horizon_h"]:
        value = terms[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value < 0
        ):
            raise InputError(f"{name}: требуется конечное неотрицательное число")
    horizon = terms["horizon_h"]
    if horizon < max((t["ready_h"] for t in verdict["trips"]), default=0):
        raise InputError(
            "Оплачиваемый горизонт должен включать полный оборот всех выбранных рейсов"
        )
    available = sum(max(0, horizon - v["available_h"]) for v in cfg["vessels"])
    active = verdict["busy_vessel_h"]
    waiting = available - active
    if waiting < 0:
        raise InputError("Занятость превышает доступный ресурс")
    components = dict(
        separate_trip_operations_rub=verdict["trip_count"] * terms["separate_trip_operations_rub"],
        active_time_rub=active * terms["active_vessel_hour_rub"],
        waiting_time_rub=waiting * terms["waiting_vessel_hour_rub"],
    )
    return dict(
        status="scenario_cost",
        total_rub=sum(components.values()),
        components=components,
        accepted_t=verdict["accepted_t"],
        active_vessel_h=active,
        paid_waiting_h=waiting,
        horizon_h=horizon,
        actual_savings_rub=None,
        assumptions="All available time is paid through a common horizon; trip operations exclude time-rate inclusions",
    )


def compare_cost(
    cfg: dict, baseline: list[dict], alternative: list[dict], deadline: int, terms: dict
) -> dict:
    a = check_schedule(cfg, baseline, deadline)
    b = check_schedule(cfg, alternative, deadline)
    if (
        a["status"] != "valid_simulation"
        or b["status"] != "valid_simulation"
        or a["accepted_t"] != b["accepted_t"]
    ):
        raise InputError(
            "Экономическое сравнение требует двух допустимых планов с одинаковым доставленным объёмом"
        )
    left = cost(cfg, baseline, deadline, terms)
    right = cost(cfg, alternative, deadline, terms)
    return dict(
        baseline=left,
        alternative=right,
        scenario_difference_rub=left["total_rub"] - right["total_rub"]
        if left["total_rub"] is not None and right["total_rub"] is not None
        else None,
        actual_savings_rub=None,
    )
