"""Explicit scenario costing, independent of procurement NMC prices."""

from math import isfinite
from typing import Any

from .domain import InputError, Scenario
from .validation import check_schedule


_RATE_FIELDS = [
    "separate_trip_operations_rub",
    "active_vessel_hour_rub",
    "waiting_vessel_hour_rub",
]
_BILLING_MODES = {"all_available_time", "inter_voyage_gaps"}


def _waiting_gaps(trips: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    """Return only gaps between consecutive trips of the same vessel."""
    by_vessel: dict[str, list[dict[str, Any]]] = {}
    for trip in trips:
        by_vessel.setdefault(trip["vessel_id"], []).append(trip)

    details = []
    total = 0
    for vessel_id, vessel_trips in sorted(by_vessel.items()):
        ordered = sorted(vessel_trips, key=lambda t: (t["start_h"], t["ready_h"]))
        for index, (previous, following) in enumerate(zip(ordered, ordered[1:]), start=1):
            gap = following["start_h"] - previous["ready_h"]
            # A valid check_schedule result should already prevent overlap.
            if gap < 0:
                raise InputError("Отрицательная межрейсовая пауза: расписание некорректно")
            total += gap
            details.append(
                {
                    "vessel_id": vessel_id,
                    "previous_trip": previous.get("trip", index),
                    "next_trip": following.get("trip", index + 1),
                    "from_h": previous["ready_h"],
                    "to_h": following["start_h"],
                    "waiting_h": gap,
                }
            )
    return total, details


def cost(cfg: dict, plan: list[dict], deadline: int, terms: dict) -> dict:
    """Calculate scenario costs for a validated schedule.

    all_available_time charges all non-active vessel time from each vessel's
    available_h through a common horizon. inter_voyage_gaps charges only
    intervals between consecutive trips of the same vessel.
    """
    cfg = Scenario.from_dict(cfg).to_dict()
    verdict = check_schedule(cfg, plan, deadline)
    if verdict["status"] != "valid_simulation":
        raise InputError("Стоимость требует допустимое расписание")
    if not isinstance(terms, dict):
        raise InputError("Условия расчёта должны быть JSON-объектом")

    mode = terms.get("billing_mode")
    missing = [name for name in _RATE_FIELDS if terms.get(name) is None]
    if mode is None:
        missing.append("billing_mode")
    elif mode not in _BILLING_MODES:
        raise InputError("Неизвестная схема billing_mode")

    if mode == "all_available_time" and terms.get("horizon_h") is None:
        missing.append("horizon_h")

    if missing:
        return {
            "status": "missing_cost_inputs",
            "total_rub": None,
            "missing": missing,
        }

    names_to_validate = list(_RATE_FIELDS)
    if terms.get("horizon_h") is not None:
        names_to_validate.append("horizon_h")
    for name in names_to_validate:
        value = terms[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value < 0
        ):
            raise InputError(f"{name}: требуется конечное неотрицательное число")

    active = verdict["busy_vessel_h"]
    gaps_h, gap_details = _waiting_gaps(verdict["trips"])

    if mode == "all_available_time":
        horizon = terms["horizon_h"]
        if horizon < max((t["ready_h"] for t in verdict["trips"]), default=0):
            raise InputError(
                "Оплачиваемый горизонт должен включать полный оборот всех выбранных рейсов"
            )
        available = sum(max(0, horizon - vessel["available_h"]) for vessel in cfg["vessels"])
        waiting = available - active
        if waiting < 0:
            raise InputError("Занятость превышает доступный ресурс")
        waiting_scope = "all_non_active_time_within_horizon"
    else:
        horizon = terms.get("horizon_h")
        waiting = gaps_h
        waiting_scope = "between_consecutive_trips_only"

    components = {
        "separate_trip_operations_rub": (
            verdict["trip_count"] * terms["separate_trip_operations_rub"]
        ),
        "active_time_rub": active * terms["active_vessel_hour_rub"],
        "waiting_time_rub": waiting * terms["waiting_vessel_hour_rub"],
    }
    return {
        "status": "scenario_cost",
        "total_rub": sum(components.values()),
        "components": components,
        "accepted_t": verdict["accepted_t"],
        "trip_count": verdict["trip_count"],
        "active_vessel_h": active,
        "paid_waiting_h": waiting,
        "inter_voyage_gaps_h": gaps_h,
        "inter_voyage_gap_details": gap_details,
        "waiting_scope": waiting_scope,
        "billing_mode": mode,
        "horizon_h": horizon,
        "weighted_mean_receipt_h": verdict["weighted_mean_receipt_h"],
        "last_receipt_h": verdict["last_receipt_h"],
        "actual_savings_rub": None,
        "assumptions": (
            "Scenario rates only; procurement NMCC rates are excluded. "
            "Trip operations exclude time-rate inclusions."
        ),
    }


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
            "Экономическое сравнение требует двух допустимых планов "
            "с одинаковым доставленным объёмом"
        )
    left = cost(cfg, baseline, deadline, terms)
    right = cost(cfg, alternative, deadline, terms)
    difference = (
        left["total_rub"] - right["total_rub"]
        if left["total_rub"] is not None and right["total_rub"] is not None
        else None
    )
    return {
        "baseline": left,
        "alternative": right,
        # Positive means the alternative schedule is cheaper.
        "scenario_difference_rub": difference,
        "alternative_is_cheaper": difference > 0 if difference is not None else None,
        "actual_savings_rub": None,
    }
