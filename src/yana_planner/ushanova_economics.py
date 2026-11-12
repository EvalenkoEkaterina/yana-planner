"""Rate sensitivity without changing the shared costing or optimizer contract."""

from copy import deepcopy
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from math import isfinite

from .domain import InputError
from .economics import RATE_FIELDS, compare_cost


def _rate(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise InputError("Rate must be a finite nonnegative number")


def run_cost_grid(
    cfg,
    baseline,
    alternative,
    deadline,
    base_terms,
    *,
    x_parameter,
    x_values,
    y_parameter,
    y_values,
):
    """Re-cost equal-volume validated schedules; never optimize or mutate inputs."""
    if (
        x_parameter not in RATE_FIELDS
        or y_parameter not in RATE_FIELDS
        or x_parameter == y_parameter
    ):
        raise InputError("Choose two distinct rate fields")
    if not isinstance(base_terms, dict) or "vessel_rates" in base_terms:
        raise InputError("Сетка чувствительности использует единые ставки для всего флота")
    x_values, y_values = list(x_values), list(y_values)
    rows = []
    for x in x_values:
        _rate(x)
        for y in y_values:
            _rate(y)
            terms = deepcopy(base_terms)
            terms.update({x_parameter: x, y_parameter: y})
            comparison = compare_cost(cfg, baseline, alternative, deadline, terms)
            difference = comparison["scenario_difference_rub"]
            rows.append(
                {
                    "terms": terms,
                    "status": "scenario_cost" if difference is not None else "missing_cost_inputs",
                    "baseline_total_rub": comparison["baseline"]["total_rub"],
                    "alternative_total_rub": comparison["alternative"]["total_rub"],
                    "scenario_difference_rub": difference,
                    "actual_savings_rub": None,
                }
            )
    return rows


def break_even(cfg, baseline, alternative, deadline, base_terms, *, parameter):
    """Solve baseline-minus-alternative = intercept + coefficient * rate."""
    if parameter not in RATE_FIELDS:
        raise InputError("Unknown rate field")
    if not isinstance(base_terms, dict) or "vessel_rates" in base_terms:
        raise InputError("Порог рассчитывается для единой ставки всего флота")
    terms = deepcopy(base_terms)
    terms[parameter] = 0
    zero = compare_cost(cfg, baseline, alternative, deadline, terms)
    intercept = zero["scenario_difference_rub"]
    if intercept is None:
        return {"status": "missing_cost_inputs", "threshold_rub": None}
    if zero["baseline"]["horizon_h"] % 1:
        raise InputError("Аналитический порог требует целочасового горизонта оплаты")
    # Use physical metric differences to avoid subtracting two large money totals.
    if parameter == "separate_trip_operations_rub":
        coefficient = len(baseline) - len(alternative)
    else:
        metric = "active_vessel_h" if parameter == "active_vessel_hour_rub" else "paid_waiting_h"
        coefficient = zero["baseline"][metric] - zero["alternative"][metric]
    result = {
        "parameter": parameter,
        "intercept_rub": intercept,
        "coefficient": coefficient,
        "threshold_rub": None,
        "actual_savings_rub": None,
    }
    if coefficient == 0:
        result["status"] = "all_rates_equal" if intercept == 0 else "no_threshold"
        return result
    root = -intercept / coefficient
    result["mathematical_root_rub"] = root
    if root < 0:
        result["status"] = "no_nonnegative_threshold"
    else:
        result.update(status="scenario_threshold", threshold_rub=root)
        exact_root = -Decimal(str(intercept)) / Decimal(str(coefficient))
        lower = exact_root.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
        upper = exact_root.quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        result["rate_increment_rub"] = 0.01
        result["lower_grid_rate_rub"] = float(lower)
        result["upper_grid_rate_rub"] = float(upper)
        for name, value in [
            ("lower_grid_difference_rub", lower),
            ("upper_grid_difference_rub", upper),
        ]:
            probe = dict(base_terms, **{parameter: float(value)})
            result[name] = compare_cost(cfg, baseline, alternative, deadline, probe)[
                "scenario_difference_rub"
            ]
    return result
