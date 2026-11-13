"""Explicit scenario costing, independent of procurement NMC prices."""

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite

from .domain import InputError, Scenario
from .validation import check_schedule

RATE_FIELDS = (
    "separate_trip_operations_rub",
    "active_vessel_hour_rub",
    "waiting_vessel_hour_rub",
)


def _number(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{name}: требуется конечное неотрицательное число")
    try:
        valid = isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise InputError(f"{name}: требуется конечное неотрицательное число")
    return Decimal(str(value))


def _rates(raw: Mapping, prefix: str, missing: list[str]) -> dict:
    values = {}
    for name in RATE_FIELDS:
        path = prefix + name
        if raw.get(name) is None:
            missing.append(path)
            continue
        value = _number(raw[name], path)
        cents = value * 100
        if cents != cents.to_integral_value():
            raise InputError(f"{path}: ставка должна быть задана с точностью до копейки")
        values[name] = int(cents)
    basis = raw.get("rate_basis", "unspecified")
    if basis not in ("scenario", "documented", "unspecified"):
        raise InputError(f"{prefix}rate_basis: ожидается scenario, documented или unspecified")
    source = raw.get("source")
    if source is not None and not isinstance(source, str):
        raise InputError(f"{prefix}source: требуется текстовый источник")
    source = source.strip() if source is not None else None
    if basis == "documented" and not source:
        missing.append(prefix + "source")
    return dict(cents=values, rate_basis=basis, source=source or None)


def _rubles(cents: int) -> float:
    try:
        value = float(Decimal(cents) / 100)
    except OverflowError as error:
        raise InputError("Стоимость слишком велика для экспорта") from error
    if not isfinite(value):
        raise InputError("Стоимость слишком велика для экспорта")
    return value


def resolve_cost_terms(cfg: dict, terms: dict) -> dict:
    """Validate input independently of any plan; internal amounts are integer kopecks.

    The returned Decimal horizon is for core calculations, not a JSON response.
    """
    cfg = Scenario.from_dict(cfg).to_dict()
    if not isinstance(terms, Mapping):
        raise InputError("Условия стоимости должны быть JSON-объектом")
    if terms.get("currency", "RUB") != "RUB":
        raise InputError("Поддерживается только валюта RUB")
    missing = []
    fleet = {v["id"]: v for v in cfg["vessels"]}
    if "vessel_rates" in terms:
        if any(name in terms for name in RATE_FIELDS):
            raise InputError("Нельзя смешивать общие ставки и vessel_rates")
        raw_rates = terms["vessel_rates"]
        if not isinstance(raw_rates, Mapping):
            raise InputError("vessel_rates: требуется объект со ставками по ID судов")
        if any(vessel_id not in fleet for vessel_id in raw_rates):
            raise InputError("vessel_rates: указано неизвестное судно")
        rates = {}
        for vessel_id in fleet:
            raw = raw_rates.get(vessel_id, {})
            if not isinstance(raw, Mapping):
                raise InputError(f"vessel_rates.{vessel_id}: требуется объект ставок")
            rates[vessel_id] = _rates(raw, f"vessel_rates.{vessel_id}.", missing)
        rates_format = "per_vessel"
    else:
        uniform = _rates(terms, "", missing)
        rates = dict.fromkeys(fleet, uniform)
        rates_format = "uniform"
    billing = terms.get("billing_mode")
    if billing is None:
        missing.append("billing_mode")
    elif billing != "all_available_time":
        raise InputError("Поддерживается только явно выбранная схема all_available_time")
    horizon = terms.get("horizon_h")
    if horizon is None:
        missing.append("horizon_h")
    else:
        horizon = _number(horizon, "horizon_h")
    return dict(
        horizon_h=horizon,
        billing_mode=billing,
        rates_format=rates_format,
        vessel_rates=rates,
        missing=missing,
    )


def cost(cfg: dict, plan: list[dict], deadline: int, terms: dict) -> dict:
    """Price a validated plan; unknown rates never become free resources.

    Legacy uniform rates and explicit per-vessel rates are mutually exclusive.
    Each vessel's cost component is rounded to kopecks before summing the report.
    """
    cfg = Scenario.from_dict(cfg).to_dict()
    verdict = check_schedule(cfg, plan, deadline)
    if verdict["status"] != "valid_simulation":
        raise InputError("Стоимость требует допустимое расписание")
    resolved = resolve_cost_terms(cfg, terms)
    missing = resolved["missing"]
    if missing:
        return dict(
            status="missing_cost_inputs",
            total_rub=None,
            missing=missing,
        )
    horizon = resolved["horizon_h"]
    fleet = {v["id"]: v for v in cfg["vessels"]}
    rates = resolved["vessel_rates"]
    required_horizon = max((t["ready_h"] for t in verdict["trips"]), default=0)
    if horizon < required_horizon:
        raise InputError(
            "Оплачиваемый горизонт должен включать полный оборот всех выбранных рейсов "
            f"с подготовкой: минимум {required_horizon} ч; задано {horizon:g} ч"
        )
    by_vessel = {vessel_id: [] for vessel_id in fleet}
    for trip in verdict["trips"]:
        by_vessel[trip["vessel_id"]].append(trip)
    per_vessel = []
    total_components = dict(separate_trip_operations_rub=0, active_time_rub=0, waiting_time_rub=0)
    for vessel_id, vessel in fleet.items():
        trips = by_vessel[vessel_id]
        available = max(Decimal(0), horizon - vessel["available_h"])
        active = sum(trip["ready_h"] - trip["start_h"] for trip in trips)
        waiting = available - active
        if waiting < 0:
            raise InputError("Занятость превышает доступный ресурс")
        rate = rates[vessel_id]
        cents = rate["cents"]
        components = dict(
            separate_trip_operations_rub=len(trips) * cents["separate_trip_operations_rub"],
            active_time_rub=active * cents["active_vessel_hour_rub"],
            waiting_time_rub=int(
                (waiting * cents["waiting_vessel_hour_rub"]).to_integral_value(
                    rounding=ROUND_HALF_UP
                )
            ),
        )
        for key, value in components.items():
            total_components[key] += value
        per_vessel.append(
            dict(
                vessel_id=vessel_id,
                trip_count=len(trips),
                available_vessel_h=float(available),
                active_vessel_h=active,
                paid_waiting_h=float(waiting),
                rates={name: _rubles(cents[name]) for name in RATE_FIELDS},
                rate_basis=rate["rate_basis"],
                source=rate["source"],
                components={key: _rubles(value) for key, value in components.items()},
                total_rub=_rubles(sum(components.values())),
            )
        )
    return dict(
        status="scenario_cost",
        currency="RUB",
        billing_mode=resolved["billing_mode"],
        rates_format=resolved["rates_format"],
        total_rub=_rubles(sum(total_components.values())),
        components={key: _rubles(value) for key, value in total_components.items()},
        per_vessel=per_vessel,
        accepted_t=verdict["accepted_t"],
        active_vessel_h=verdict["busy_vessel_h"],
        paid_waiting_h=sum(row["paid_waiting_h"] for row in per_vessel),
        horizon_h=terms["horizon_h"],
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
        scenario_difference_rub=float(
            Decimal(str(left["total_rub"])) - Decimal(str(right["total_rub"]))
        )
        if left["total_rub"] is not None and right["total_rub"] is not None
        else None,
        actual_savings_rub=None,
    )
