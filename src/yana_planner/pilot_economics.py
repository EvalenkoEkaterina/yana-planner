"""Separate published order values, voluntary hours, and paid pilot budgets."""

from decimal import ROUND_HALF_UP, Decimal
from math import isfinite

from .domain import InputError


def _number(value, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise InputError(f"{name}: expected a finite nonnegative number")
    return Decimal(str(value))


def _money(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def order_value(quantity_t, rate_rub_t):
    """Calculate Q*p, preserving a missing value rather than substituting zero."""
    for name, value in [("quantity_t", quantity_t), ("rate_rub_t", rate_rub_t)]:
        if value is not None:
            _number(value, name)
    if quantity_t is None or rate_rub_t is None:
        return {"status": "missing_inputs", "order_value_rub": None}
    return {
        "status": "quantity_times_rate_not_carrier_cost",
        "order_value_rub": _money(Decimal(str(quantity_t)) * Decimal(str(rate_rub_t))),
        "actual_savings_rub": None,
    }


def pilot_budget(spec, *, mode, operating_months=12):
    """Forward estimate only; student donation is conditional, not committed funding."""
    if mode not in {"student", "paid"}:
        raise InputError("mode must be student or paid")
    months = _number(operating_months, "operating_months")
    rate = (
        _number(spec["paid_labour_rate_rub_h"], "paid_labour_rate_rub_h")
        if mode == "paid"
        else Decimal(0)
    )
    hours = sum((_number(t["hours"], "task hours") for t in spec["tasks"]), Decimal(0))
    support = _number(spec["monthly_support_hours"], "monthly_support_hours") * rate
    labour = hours * rate
    reserve_rate = _number(spec["paid_contingency_percent"], "paid_contingency_percent") / 100
    setup = _number(spec["incremental_setup_cash_rub"], "incremental_setup_cash_rub")
    running = _number(spec["incremental_monthly_cash_rub"], "incremental_monthly_cash_rub")
    paid_labour = labour if mode == "paid" else Decimal(0)
    paid_support = support if mode == "paid" else Decimal(0)
    reserve = (setup + paid_labour) * reserve_rate
    cash_setup = setup + paid_labour + reserve
    cash_monthly = running + paid_support
    volunteer_hours = hours if mode == "student" else Decimal(0)
    volunteer_monthly = (
        _number(spec["monthly_support_hours"], "monthly_support_hours")
        if mode == "student"
        else Decimal(0)
    )
    return {
        "mode": mode,
        "status": "conditional_planning_estimate",
        "setup_hours": float(hours),
        "cash_setup_rub": _money(cash_setup),
        "cash_monthly_rub": _money(cash_monthly),
        "cash_setup_contingency_rub": _money(reserve),
        "volunteer_setup_hours": float(volunteer_hours),
        "volunteer_monthly_hours": float(volunteer_monthly),
        "volunteer_total_hours": float(volunteer_hours + months * volunteer_monthly),
        "volunteer_labour_policy": "unpaid_no_monetary_valuation",
        "valued_resources_setup_rub": _money(cash_setup) if mode == "paid" else None,
        "operating_months": float(months),
        "cash_total_rub": _money(cash_setup + months * cash_monthly),
        "valued_resources_total_rub": _money(cash_setup + months * cash_monthly)
        if mode == "paid"
        else None,
        "valuation_scope": "listed cash payments; volunteer work recorded only in hours",
        "unpriced_resources": list(spec["excluded_unpriced_resources"]),
        "actual_savings_rub": None,
    }


def illustrative_payback(budget, benefit_per_case_rub, *, cases):
    """A scenario, never a forecast or verified saving; preserve adverse outcomes."""
    if isinstance(cases, bool) or not isinstance(cases, int) or cases < 0:
        raise InputError("cases must be a nonnegative integer")
    if benefit_per_case_rub is None:
        return {"status": "missing_benefit", "net_resource_effect_rub": None}
    if (
        isinstance(benefit_per_case_rub, bool)
        or not isinstance(benefit_per_case_rub, (int, float))
        or not isfinite(benefit_per_case_rub)
    ):
        raise InputError("benefit must be a finite number")
    benefit = Decimal(str(benefit_per_case_rub)) * cases
    cash_cost = _number(budget["cash_total_rub"], "cash_total_rub")
    resources = budget["valued_resources_total_rub"]
    if resources is not None:
        resources = _number(resources, "valued_resources_total_rub")
    return {
        "status": "illustration_not_forecast",
        "cases": cases,
        "scenario_benefit_rub": _money(benefit),
        "net_cash_effect_rub": _money(benefit - cash_cost),
        "net_resource_effect_rub": _money(benefit - resources) if resources is not None else None,
        "payback_status": "not_applicable_zero_cash_budget" if not cash_cost else "scenario_only",
        "cash_roi_percent": _money((benefit - cash_cost) / cash_cost * 100) if cash_cost else None,
        "actual_savings_rub": None,
    }
