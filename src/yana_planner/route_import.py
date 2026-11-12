"""Versioned route packages, separate from the shared web/service adapters."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from math import isfinite

from .domain import InputError, Scenario, integer

RATE_FIELDS = (
    "separate_trip_operations_rub",
    "active_vessel_hour_rub",
    "waiting_vessel_hour_rub",
)


def _number(value, path, *, positive=False, money=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
        or (positive and value == 0)
    ):
        raise InputError(
            f"{path}: требуется конечное {'положительное' if positive else 'неотрицательное'} число"
        )
    if money and Decimal(str(value)) * 100 != (Decimal(str(value)) * 100).to_integral_value():
        raise InputError(f"{path}: точность не более копейки")
    return value


def _aware(value, path):
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise InputError(f"{path}: нужна дата ISO 8601 с часовым поясом") from None


def import_route_package(raw):
    """Validate without mutation or fetching URLs. Unknown data stay unknown."""
    if not isinstance(raw, dict) or raw.get("package_version") != "yana-route/1":
        raise InputError("package_version: ожидается yana-route/1")
    data = deepcopy(raw)
    route = data.get("route")
    if not isinstance(route, dict):
        raise InputError("route: нужен объект")
    for key in ("id", "origin", "destination"):
        if not isinstance(route.get(key), str) or not route[key].strip():
            raise InputError(f"route.{key}: нужен непустой текст")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise InputError("sources: нужен непустой реестр источников")
    source_ids = set()
    for i, source in enumerate(sources):
        if not isinstance(source, dict):
            raise InputError(f"sources[{i}]: нужен объект")
        for key in ("id", "title", "url", "accessed_at"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                raise InputError(f"sources[{i}].{key}: нужен текст")
        if source["id"] in source_ids or not source["url"].startswith("https://"):
            raise InputError(f"sources[{i}]: повторный ID или ссылка не HTTPS")
        source_ids.add(source["id"])
    facts = data.get("facts", [])
    if not isinstance(facts, list):
        raise InputError("facts: нужен список")
    for i, fact in enumerate(facts):
        if not isinstance(fact, dict) or fact.get("status") not in (
            "documented",
            "scenario_assumption",
            "unknown",
        ):
            raise InputError(f"facts[{i}].status: documented / scenario_assumption / unknown")
        if not isinstance(fact.get("field"), str) or not fact["field"]:
            raise InputError(f"facts[{i}].field: нужен путь поля")
        if fact["status"] == "documented" and fact.get("source_id") not in source_ids:
            raise InputError(f"facts[{i}].source_id: источник не найден")
        if fact["status"] == "unknown" and fact.get("value") is not None:
            raise InputError(f"facts[{i}].value: неизвестное значение должно быть null")
    cfg = data.get("scenario")
    if not isinstance(cfg, dict):
        raise InputError("scenario: нужен объект")
    if not isinstance(cfg.get("vessels"), list) or not cfg["vessels"]:
        raise InputError("scenario.vessels: нужен непустой список")
    for i, vessel in enumerate(cfg["vessels"]):
        if not isinstance(vessel, dict):
            raise InputError(f"scenario.vessels[{i}]: нужен объект")
        if vessel.get("load_rate_tph") is not None:
            _number(vessel["load_rate_tph"], f"scenario.vessels[{i}].load_rate_tph", positive=True)
            integer(vessel.get("load_setup_h", 0), f"scenario.vessels[{i}].load_setup_h")
            if vessel.get("load_h") is None:
                vessel.pop("load_h", None)
        else:
            if vessel.get("load_setup_h") not in (None, 0):
                raise InputError(
                    f"scenario.vessels[{i}].load_setup_h: подготовка к погрузке задаётся "
                    "только вместе со скоростью; укажите load_rate_tph или обнулите подготовку"
                )
            vessel.pop("load_rate_tph", None)
            vessel.pop("load_setup_h", None)
    validation_cfg = deepcopy(cfg)
    temporary_load_fields = []
    for i, vessel in enumerate(validation_cfg["vessels"]):
        if vessel.get("load_rate_tph") is not None and "load_h" not in vessel:
            vessel["load_h"] = 1  # Old domain contract only; never used for a rate-based run.
            temporary_load_fields.append(i)
    data["scenario"] = Scenario.from_dict(validation_cfg).to_dict()
    for i in temporary_load_fields:
        data["scenario"]["vessels"][i].pop("load_h", None)
    # Scenario sorts arrivals stably; event indices refer to the incoming order.
    arrival_order = sorted(range(len(cfg["arrivals"])), key=lambda i: cfg["arrivals"][i]["h"])
    normalized_arrival_index = {old: new for new, old in enumerate(arrival_order)}
    deadline = integer(data.get("deadline_h"), "deadline_h", minimum=1)
    if deadline > cfg["return_cutoff_h"]:
        raise InputError("deadline_h: позже срока возврата")
    if data.get("strategy") not in ("trips", "cost"):
        raise InputError("strategy: trips / cost")
    terms = data.get("cost_terms")
    if terms is not None:
        if not isinstance(terms, dict) or terms.get("billing_mode") != "all_available_time":
            raise InputError("cost_terms.billing_mode: all_available_time")
        if terms.get("currency") != "RUB":
            raise InputError("cost_terms.currency: RUB")
        _number(terms.get("horizon_h"), "cost_terms.horizon_h")
        rates = terms.get("vessel_rates")
        ids = {v["id"] for v in cfg["vessels"]}
        if not isinstance(rates, dict) or set(rates) != ids:
            raise InputError(
                "cost_terms.vessel_rates: нужна ровно одна запись для каждого ID судна"
            )
        if any(field in terms for field in RATE_FIELDS):
            raise InputError("cost_terms: нельзя смешивать общие и индивидуальные ставки")
        for vid, rate in rates.items():
            if not isinstance(rate, dict):
                raise InputError(f"cost_terms.vessel_rates.{vid}: нужен объект")
            for field in RATE_FIELDS:
                if rate.get(field) is not None:
                    _number(rate[field], f"cost_terms.vessel_rates.{vid}.{field}", money=True)
            if rate.get("rate_basis") not in ("scenario", "documented", "unspecified"):
                raise InputError(f"cost_terms.vessel_rates.{vid}.rate_basis: неверный статус")
            if rate["rate_basis"] == "documented" and not (
                isinstance(rate.get("source"), str) and rate["source"].strip()
            ):
                raise InputError(f"cost_terms.vessel_rates.{vid}.source: укажите источник")
            if data["strategy"] == "cost" and any(rate.get(f) is None for f in RATE_FIELDS):
                raise InputError(
                    f"cost_terms.vessel_rates.{vid}: для стратегии cost нужны все ставки"
                )
    elif data["strategy"] == "cost":
        raise InputError("cost_terms: нужны для стратегии cost")
    event = data.get("event")
    if event is not None:
        if not isinstance(event, dict):
            raise InputError("event: нужен объект или null")
        at = integer(event.get("announced_at_h"), "event.announced_at_h")
        if at >= deadline:
            raise InputError("event.announced_at_h: не раньше срока приёмки")
        if event.get("type") == "weather_closure":
            if event.get("rule_status") != "scenario_assumption":
                raise InputError("event.rule_status: scenario_assumption")
            if event.get("data_kind") not in ("synthetic", "reanalysis", "forecast"):
                raise InputError("event.data_kind: synthetic / reanalysis / forecast")
            if not isinstance(event.get("source"), str) or not event["source"].strip():
                raise InputError("event.source: укажите источник или описание синтетики")
            if not isinstance(event.get("closures"), list):
                raise InputError("event.closures: нужен список интервалов")
            for i, interval in enumerate(event["closures"]):
                if not isinstance(interval, list) or len(interval) != 2:
                    raise InputError(f"event.closures[{i}]: нужен [from_h, to_h)")
                a, b = (integer(x, f"event.closures[{i}]") for x in interval)
                if a < at or a >= b:
                    raise InputError(f"event.closures[{i}]: начало >= объявления и конец > начала")
            if event["data_kind"] == "forecast":
                from datetime import timedelta

                issued = _aware(event.get("forecast_issued_at"), "event.forecast_issued_at")
                start = _aware(cfg.get("t0"), "scenario.t0")
                if issued > start + timedelta(hours=at):
                    raise InputError("event.forecast_issued_at: прогноз выпущен после объявления")
            elif event.get("forecast_issued_at") is not None:
                raise InputError(
                    "event.forecast_issued_at: для синтетики/реанализа должно быть null"
                )
        elif event.get("type") == "arrival_delay":
            index = integer(event.get("arrival_index"), "event.arrival_index")
            if index >= len(cfg["arrivals"]):
                raise InputError("event.arrival_index: поступление не найдено")
            event["arrival_index"] = normalized_arrival_index[index]
            old = data["scenario"]["arrivals"][event["arrival_index"]]["h"]
            new = integer(event.get("new_arrival_h"), "event.new_arrival_h")
            if at >= old or new <= old:
                raise InputError("event: объявление должно предшествовать задержанному поступлению")
        else:
            raise InputError("event.type: weather_closure / arrival_delay")
    return data
