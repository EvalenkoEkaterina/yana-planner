"""One-voyage scenario accounting; every active or waiting hour is charged once."""

from decimal import Decimal
from math import isfinite

from yana_planner.domain import InputError
from yana_planner.economics import RATE_FIELDS
from yana_planner.multi_stop import check_voyage, normalize


def _money(value, name):
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise InputError(f"{name}: нужна неотрицательная ставка или null")
    cents = Decimal(str(value)) * 100
    if cents != cents.to_integral_value() or cents > 2**53 - 1:
        raise InputError(f"{name}: точность до копейки, ставка в допустимом диапазоне")
    return int(cents)


def price_voyage(raw, starts, terms):
    c = normalize(raw)
    verdict = check_voyage(c, starts)
    if verdict["status"] != "valid_simulation":
        raise InputError("Экономика требует проверенное допустимое расписание")
    if (
        not isinstance(terms, dict)
        or terms.get("billing_mode") != "all_available_time"
        or terms.get("currency") != "RUB"
    ):
        raise InputError("Нужна схема all_available_time, валюта RUB")
    v = c["vessel"]
    if terms.get("vessel_id") != v["id"]:
        raise InputError("Ставки относятся к другому судну")
    horizon = terms.get("horizon_h")
    if (
        isinstance(horizon, bool)
        or not isinstance(horizon, (int, float))
        or not isfinite(horizon)
        or horizon < 0
        or horizon > 492
    ):
        raise InputError("Нужен горизонт оплаты от 0 до 492 часов")
    if horizon < verdict["ready_h"]:
        raise InputError(
            f"Оплата должна покрывать возврат и подготовку: минимум {verdict['ready_h']} ч"
        )
    if terms.get("stop_fees_excluded_from_other_rates") is not True:
        raise InputError("Укажите, что отдельные сборы за заходы не входят в остальные ставки")
    basis = terms.get("rate_basis")
    if basis not in ("scenario", "documented", "unspecified"):
        raise InputError("Нужен статус ставок: scenario/documented/unspecified")
    if basis == "documented" and not (
        isinstance(terms.get("source"), str) and terms["source"].strip()
    ):
        raise InputError("Для документированных ставок нужен источник")
    rates = terms.get("rates")
    fees = terms.get("stop_fees_rub")
    if not isinstance(rates, dict) or set(rates) - set(RATE_FIELDS):
        raise InputError("Нужны три ставки: за рейс, активность и ожидание")
    stop_ids = {s["id"] for s in c["stops"]}
    if not isinstance(fees, dict) or set(fees) - stop_ids:
        raise InputError("Сборы за заходы должны относиться к выбранным получателям")
    active = (
        v["load_h"]
        + sum(v["travel_h"])
        + sum(s["unload_h"] for s in c["stops"])
        + v["return_h"]
        + v["prep_h"]
    )
    available = Decimal(str(horizon)) - v["available_h"]
    waiting = available - active
    if waiting < 0:
        raise InputError("Активность превышает оплачиваемое доступное время")
    missing, components = [], []
    quantities = [Decimal(1), Decimal(active), waiting]
    for field, quantity in zip(RATE_FIELDS, quantities, strict=True):
        cents = _money(rates.get(field), field)
        if cents is None:
            missing.append("rates." + field)
        components.append(
            dict(
                kind=field,
                quantity=float(quantity),
                rate_rub=None if cents is None else cents / 100,
                amount_cents=None
                if cents is None
                else int((quantity * cents).quantize(Decimal(1), rounding="ROUND_HALF_UP")),
            )
        )
    for stop in c["stops"]:
        cents = _money(fees.get(stop["id"]), "stop_fees_rub." + stop["id"])
        if cents is None:
            missing.append("stop_fees_rub." + stop["id"])
        components.append(
            dict(
                kind="stop_fee",
                stop_id=stop["id"],
                quantity=1,
                rate_rub=None if cents is None else cents / 100,
                amount_cents=cents,
            )
        )
    known = sum(row["amount_cents"] for row in components if row["amount_cents"] is not None)
    if known > 2**53 - 1:
        raise InputError("Расходы слишком велики для точного денежного результата")
    for row in components:
        cents = row.pop("amount_cents")
        row["amount_rub"] = None if cents is None else cents / 100
    times = verdict["starts"]
    waits = [dict(location="origin_before_loading", hours=times["load_start_h"] - v["available_h"])]
    waits.append(
        dict(
            location="origin_after_loading",
            hours=times["depart1_h"] - times["load_start_h"] - v["load_h"],
        )
    )
    for i, stop in enumerate(c["stops"]):
        waits.append(
            dict(
                location=stop["id"] + ":before_unload",
                hours=times[f"unload{i + 1}_start_h"]
                - times[f"depart{i + 1}_h"]
                - v["travel_h"][i],
            )
        )
        next_departure = (
            times[f"depart{i + 2}_h"] if i + 1 < len(c["stops"]) else times["depart_return_h"]
        )
        waits.append(
            dict(
                location=stop["id"] + ":after_unload",
                hours=next_departure - verdict["deliveries"][i]["receipt_h"],
            )
        )
    waits.append(
        dict(location="after_ready", hours=float(Decimal(str(horizon)) - verdict["ready_h"]))
    )
    if sum((Decimal(str(w["hours"])) for w in waits), Decimal(0)) != waiting:
        raise InputError("Не совпала независимая сумма часов ожидания")
    return dict(
        status="missing_cost_inputs" if missing else "scenario_cost",
        currency="RUB",
        billing_mode="all_available_time",
        horizon_h=horizon,
        vessel_id=v["id"],
        trip_count=1,
        visit_count=len(c["stops"]),
        available_vessel_h=float(available),
        active_vessel_h=active,
        paid_waiting_h=float(waiting),
        waiting_breakdown=waits,
        active_breakdown=dict(
            loading_h=v["load_h"],
            loaded_movement_h=sum(v["travel_h"]),
            unloading_h=sum(s["unload_h"] for s in c["stops"]),
            return_h=v["return_h"],
            preparation_h=v["prep_h"],
        ),
        components=components,
        missing=missing,
        known_subtotal_rub=known / 100,
        total_rub=None if missing else known / 100,
        rate_basis=basis,
        source=terms.get("source"),
        optimization_includes_cost=False,
        actual_savings_rub=None,
        note="Fixed-schedule scenario cost. Trip charge once, each selected stop fee once; hourly activity and waiting do not overlap. Unknown rates are not zero.",
    )
