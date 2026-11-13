"""Versioned boundary for the multi-stop frontend; no silent unsupported fields."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .domain import InputError
from .multi_stop import normalize, plan_multi_stop
from .multi_stop_economics import _money

SCENARIO_FIELDS = {
    "mode",
    "navigation_assumed",
    "fleet_assumed",
    "supply_assumed",
    "reception_assumed",
    "origin_id",
    "horizon_h",
    "vessel",
    "initial_source_t",
    "arrivals",
    "stops",
    "movement_closures",
    "provenance",
    "receipt_priority",
    "route_reference",
}
VESSEL_FIELDS = {
    "id",
    "capacity_t",
    "load_h",
    "travel_h",
    "leg1_h",
    "leg2_h",
    "return_h",
    "available_h",
    "prep_h",
}
STOP_FIELDS = {"id", "name", "required_t", "deadline_h", "unload_h", "windows"}
COST_FIELDS = {
    "billing_mode",
    "currency",
    "horizon_h",
    "vessel_id",
    "rate_basis",
    "source",
    "rates",
    "stop_fees_excluded_from_other_rates",
    "stop_fees_rub",
}


def _object(value, allowed, path):
    if not isinstance(value, dict):
        raise InputError(f"{path}: нужен объект")
    if set(value) - allowed:
        raise InputError(
            f"{path}: неподдерживаемые поля: " + ", ".join(sorted(set(value) - allowed))
        )


def calculate_multi_stop_request(payload):
    _object(payload, {"schema_version", "scenario", "cost_terms", "search_seconds"}, "request")
    if payload.get("schema_version", "yana-multi-stop-request/1") != "yana-multi-stop-request/1":
        raise InputError("schema_version: ожидается yana-multi-stop-request/1")
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InputError("Запрос должен содержать конечные числа и JSON-значения") from exc
    raw = payload.get("scenario")
    _object(raw, SCENARIO_FIELDS, "scenario")
    _object(raw.get("vessel"), VESSEL_FIELDS, "scenario.vessel")
    if not isinstance(raw.get("stops"), list):
        raise InputError("scenario.stops: нужен список")
    for i, stop in enumerate(raw["stops"]):
        _object(stop, STOP_FIELDS, f"scenario.stops[{i}]")
    seconds = payload.get("search_seconds", 2)
    if type(seconds) is not int or seconds not in (2, 10):
        raise InputError("search_seconds: 2 или 10 секунд на этап")
    terms = deepcopy(payload.get("cost_terms"))
    if terms is not None:
        _object(terms, COST_FIELDS, "cost_terms")
    cfg = normalize(raw)
    # Validate billing even when transport later proves infeasible.
    if terms is not None:
        if terms.get("billing_mode") != "all_available_time" or terms.get("currency") != "RUB":
            raise InputError("cost_terms: нужны all_available_time и RUB")
        if terms.get("vessel_id") != cfg["vessel"]["id"]:
            raise InputError("cost_terms: ставки относятся к другому судну")
        if terms.get("stop_fees_excluded_from_other_rates") is not True:
            raise InputError("cost_terms: подтвердите отсутствие повторного счёта сборов")
        h = terms.get("horizon_h")
        if type(h) not in (int, float) or not 0 <= h <= 492:
            raise InputError("cost_terms.horizon_h: число от 0 до 492")
        if terms.get("rate_basis") not in ("scenario", "documented", "unspecified"):
            raise InputError("cost_terms.rate_basis: неверный статус")
        if terms.get("rate_basis") == "documented" and not (
            isinstance(terms.get("source"), str) and terms["source"].strip()
        ):
            raise InputError("cost_terms.source: нужен источник документированных ставок")
        allowed_rates = {
            "separate_trip_operations_rub",
            "active_vessel_hour_rub",
            "waiting_vessel_hour_rub",
        }
        _object(terms.get("rates"), allowed_rates, "cost_terms.rates")
        _object(
            terms.get("stop_fees_rub"), {s["id"] for s in cfg["stops"]}, "cost_terms.stop_fees_rub"
        )
        for name, value in {**terms["rates"], **terms["stop_fees_rub"]}.items():
            _money(value, name)
    result = plan_multi_stop(cfg, stage_limit_s=seconds, cost_terms=terms)
    inputs = dict(
        schema_version="yana-multi-stop-request/1",
        scenario=cfg,
        cost_terms=terms,
        settings=dict(stage_limit_s=seconds),
    )
    result["schema_version"] = "yana-multi-stop-result/1"
    result["input_sha256"] = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    files = ["multi_stop.py", "multi_stop_economics.py", "multi_stop_service.py", "domain.py"]
    result["code_version"] = hashlib.sha256(
        b"".join(Path(__file__).with_name(f).read_bytes() for f in files)
    ).hexdigest()
    return inputs, result
