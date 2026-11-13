"""Post-hoc tariff stress tests; no changes to transport or optimization objectives."""

from copy import deepcopy
from decimal import Decimal
from math import isfinite

from yana_planner.domain import InputError
from yana_planner.economics import cost


def variant_terms(base, variant):
    if variant not in ("S0", "S1", "S2", "S3", "S4", "S5"):
        raise InputError("Нужен вариант S0–S5")
    if not isinstance(base, dict) or not isinstance(base.get("vessel_rates"), dict):
        raise InputError("Нужны условия с индивидуальными ставками судов")
    for rate in base["vessel_rates"].values():
        if not isinstance(rate, dict):
            raise InputError("Нужен объект ставок судна")
        for key in (
            "active_vessel_hour_rub",
            "waiting_vessel_hour_rub",
            "separate_trip_operations_rub",
        ):
            value = rate.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise InputError("Для стресс-теста нужны полные неотрицательные ставки")
    terms = deepcopy(base)
    for rate in terms["vessel_rates"].values():
        if variant == "S1":
            rate["active_vessel_hour_rub"] *= 2
        elif variant == "S2":
            rate["waiting_vessel_hour_rub"] *= 2
        elif variant == "S3":
            rate["waiting_vessel_hour_rub"] = rate["active_vessel_hour_rub"]
        elif variant == "S4":
            rate["separate_trip_operations_rub"] *= 2
    return terms


def price_fixed_plan(cfg, plan, deadline, base_terms, variant="S0"):
    """S5 excludes only never-engaged vessels, retaining all waiting of used vessels.

    This is an explicitly hypothetical billing calculation on a fixed schedule,
    not fleet selection by MILP or a new supported optimizer billing mode.
    """
    terms = variant_terms(base_terms, variant)
    result = cost(cfg, plan, deadline, terms)
    if result["status"] != "scenario_cost":
        raise InputError("Для стресс-теста нужны все ставки")
    result["sensitivity_variant"] = variant
    result["used_vessels"] = [v["vessel_id"] for v in result["per_vessel"] if v["trip_count"]]
    result["excluded_vessels"] = []
    if variant == "S5":
        result["billing_mode"] = "optional_engagement_hypothesis_posthoc"
        result["excluded_vessels"] = [
            v["vessel_id"] for v in result["per_vessel"] if not v["trip_count"]
        ]
        result["per_vessel"] = [v for v in result["per_vessel"] if v["trip_count"]]
        result["components"] = {
            key: float(
                sum((Decimal(str(v["components"][key])) for v in result["per_vessel"]), Decimal(0))
            )
            for key in result["components"]
        }
        result["total_rub"] = float(
            sum((Decimal(str(v["total_rub"])) for v in result["per_vessel"]), Decimal(0))
        )
        result["paid_waiting_h"] = sum(v["paid_waiting_h"] for v in result["per_vessel"])
        result["assumptions"] = (
            "Never-engaged vessels incur no reservation, mobilization or cancellation fee; used vessels remain paid from original availability to common horizon. Unconfirmed contract assumption."
        )
    result["rub_per_delivered_t"] = (
        result["total_rub"] / result["accepted_t"] if result["accepted_t"] else None
    )
    result["reference_terms"] = terms
    result["reoptimized"] = False
    return result
