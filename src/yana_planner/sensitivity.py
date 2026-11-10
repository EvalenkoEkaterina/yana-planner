"""Scenario rate sensitivity for validated schedules; independent of NMC prices."""

from __future__ import annotations

from typing import Any

from .domain import InputError
from .economics import compare_cost, cost

TRIP_OPERATION_RATES_RUB = (25_000,)
ACTIVE_VESSEL_HOUR_RATES_RUB = (500, 1_000, 2_000)
WAITING_VESSEL_HOUR_RATES_RUB = (250, 500, 1_000)
BILLING_MODES = ("inter_voyage_gaps", "all_available_time")


def _terms(mode: str, horizon_h: int | None, fixed: int, active: int, waiting: int) -> dict:
    return {
        "billing_mode": mode,
        "horizon_h": horizon_h if mode == "all_available_time" else None,
        "separate_trip_operations_rub": fixed,
        "active_vessel_hour_rub": active,
        "waiting_vessel_hour_rub": waiting,
    }


def _break_even_waiting_rate(
    baseline: dict, alternative: dict, fixed_rate: int, active_rate: int
) -> dict:
    """Classify a + b*W = 0 on the allowed domain W >= 0."""
    delta_without_waiting = (baseline["trip_count"] - alternative["trip_count"]) * fixed_rate + (
        baseline["active_vessel_h"] - alternative["active_vessel_h"]
    ) * active_rate
    delta_waiting = baseline["paid_waiting_h"] - alternative["paid_waiting_h"]
    root = -delta_without_waiting / delta_waiting if delta_waiting else None
    if delta_waiting == 0:
        status = "always_equal" if delta_without_waiting == 0 else "no_crossing"
    elif root < 0:
        status = "outside_nonnegative_domain"
    else:
        status = "at_zero" if root == 0 else "finite"
    admissible = root if status in ("finite", "at_zero") else None
    constant_winner = None
    if status in ("no_crossing", "outside_nonnegative_domain"):
        constant_winner = "alternative" if delta_without_waiting > 0 else "baseline"
    elif status == "always_equal":
        constant_winner = "equal"
    return {
        "break_even_waiting_vessel_hour_rub": admissible,
        "mathematical_waiting_rate_root_rub": root,
        "threshold_status": status,
        "constant_winner_for_nonnegative_rates": constant_winner,
        "alternative_cheaper_when": (
            "waiting_rate_below_threshold" if delta_waiting < 0 else "waiting_rate_above_threshold"
        )
        if admissible is not None
        else None,
        "difference_at_zero_waiting_rub": delta_without_waiting,
        "waiting_delta_baseline_minus_alternative_h": delta_waiting,
    }


def run_sensitivity(
    cfg: dict[str, Any],
    baseline: list[dict[str, Any]],
    alternative: list[dict[str, Any]],
    deadline: int,
    *,
    horizon_h: int | None = None,
) -> dict[str, Any]:
    """Build nine rate combinations for each billing mode.

    `baseline` and `alternative` are always revalidated and re-costed from
    the supplied plans. Call this again with revised plans after replanning.
    A positive baseline-minus-alternative difference means the alternative
    is cheaper in the scenario model.
    """
    # Derive schedule metrics with explicit zero scenario rates; zero here is
    # a calculation device, not a default for any unknown rate in user terms.
    metric_terms = _terms("inter_voyage_gaps", None, 0, 0, 0)
    base_metrics = cost(cfg, baseline, deadline, metric_terms)
    alt_metrics = cost(cfg, alternative, deadline, metric_terms)
    if base_metrics["accepted_t"] != alt_metrics["accepted_t"]:
        raise InputError("Анализ чувствительности требует одинакового доставленного объёма")
    if horizon_h is None:
        return {
            "status": "missing_cost_inputs",
            "total_rub": None,
            "missing": ["horizon_h"],
            "billing_modes": {},
            "actual_savings_rub": None,
        }

    modes: dict[str, Any] = {}
    for mode in BILLING_MODES:
        # Get waiting metrics for this specific billing mode.
        zero_terms = _terms(mode, horizon_h, 0, 0, 0)
        base_zero = cost(cfg, baseline, deadline, zero_terms)
        alt_zero = cost(cfg, alternative, deadline, zero_terms)

        rows = []
        thresholds = []
        for fixed_rate in TRIP_OPERATION_RATES_RUB:
            for active_rate in ACTIVE_VESSEL_HOUR_RATES_RUB:
                threshold = _break_even_waiting_rate(base_zero, alt_zero, fixed_rate, active_rate)
                thresholds.append(
                    {
                        "separate_trip_operations_rub": fixed_rate,
                        "active_vessel_hour_rub": active_rate,
                        **threshold,
                    }
                )
                for waiting_rate in WAITING_VESSEL_HOUR_RATES_RUB:
                    terms = _terms(mode, horizon_h, fixed_rate, active_rate, waiting_rate)
                    comparison = compare_cost(cfg, baseline, alternative, deadline, terms)
                    rows.append(
                        {
                            "separate_trip_operations_rub": fixed_rate,
                            "active_vessel_hour_rub": active_rate,
                            "waiting_vessel_hour_rub": waiting_rate,
                            "baseline_total_rub": comparison["baseline"]["total_rub"],
                            "alternative_total_rub": comparison["alternative"]["total_rub"],
                            "baseline_minus_alternative_rub": comparison["scenario_difference_rub"],
                            "alternative_is_cheaper": comparison["alternative_is_cheaper"],
                            "baseline_trip_count": comparison["baseline"]["trip_count"],
                            "alternative_trip_count": comparison["alternative"]["trip_count"],
                            "baseline_active_vessel_h": comparison["baseline"]["active_vessel_h"],
                            "alternative_active_vessel_h": comparison["alternative"][
                                "active_vessel_h"
                            ],
                            "baseline_paid_waiting_h": comparison["baseline"]["paid_waiting_h"],
                            "alternative_paid_waiting_h": comparison["alternative"][
                                "paid_waiting_h"
                            ],
                            "accepted_t": comparison["baseline"]["accepted_t"],
                        }
                    )
        modes[mode] = {
            "rows": rows,
            "break_even_thresholds": thresholds,
            "baseline_metrics": {
                "trip_count": base_zero["trip_count"],
                "active_vessel_h": base_zero["active_vessel_h"],
                "paid_waiting_h": base_zero["paid_waiting_h"],
                "weighted_mean_receipt_h": base_zero["weighted_mean_receipt_h"],
                "last_receipt_h": base_zero["last_receipt_h"],
            },
            "alternative_metrics": {
                "trip_count": alt_zero["trip_count"],
                "active_vessel_h": alt_zero["active_vessel_h"],
                "paid_waiting_h": alt_zero["paid_waiting_h"],
                "weighted_mean_receipt_h": alt_zero["weighted_mean_receipt_h"],
                "last_receipt_h": alt_zero["last_receipt_h"],
            },
        }

    return {
        "status": "scenario_sensitivity",
        "scenario_id": cfg.get("id"),
        "horizon_h": horizon_h,
        "actual_savings_rub": None,
        "outage_payment_policy": "included_when_inside_selected_waiting_intervals",
        "rate_sets": {
            "separate_trip_operations_rub": list(TRIP_OPERATION_RATES_RUB),
            "active_vessel_hour_rub": list(ACTIVE_VESSEL_HOUR_RATES_RUB),
            "waiting_vessel_hour_rub": list(WAITING_VESSEL_HOUR_RATES_RUB),
        },
        "billing_modes": modes,
        "interpretation": (
            "Positive baseline_minus_alternative_rub means the alternative plan "
            "has a lower modeled scenario cost. These are not actual savings. "
            "NMC procurement prices are excluded."
        ),
    }
