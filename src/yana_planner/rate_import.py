"""Import the team's explicit vessel tariff table into the shared costing contract."""

import csv
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .domain import InputError, Scenario
from .economics import RATE_FIELDS, resolve_cost_terms

STATUSES = {
    "scenario_assumption": "scenario",
    "scenario": "scenario",
    "documented": "documented",
    "confirmed": "documented",
    "unknown": "unspecified",
    "": "unspecified",
}


def _money(raw, name):
    if raw is None or not raw.strip():
        return None
    try:
        value = Decimal(raw.strip().replace(",", "."))
    except InvalidOperation as error:
        raise InputError(f"{name}: требуется число рублей") from error
    if not value.is_finite() or value < 0:
        raise InputError(f"{name}: требуется конечная неотрицательная ставка")
    if value > Decimal(2**53 - 1) / 100:
        raise InputError(f"{name}: ставка слишком велика")
    cents = value * 100
    if cents != cents.to_integral_value():
        raise InputError(f"{name}: точность ставки до копейки")
    return int(value) if value == value.to_integral_value() else float(value)


def load_vessel_rates(cfg: dict, base_terms: dict, path: str | Path) -> dict:
    """CSV supplies rates; base JSON supplies billing mode/horizon. Missing is not zero.

    'confirmed' is only a user-supplied document label, not independent verification.
    """
    cfg = Scenario.from_dict(cfg).to_dict()
    if not isinstance(base_terms, dict) or "vessel_rates" in base_terms:
        raise InputError("Для импорта нужны общие условия оплаты без vessel_rates")
    if base_terms.get("currency", "RUB") != "RUB":
        raise InputError("Поддерживается только валюта RUB")
    required = {"vessel_id", *RATE_FIELDS, "source", "status"}
    fleet = {v["id"] for v in cfg["vessels"]}
    rates = {}
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if (
                reader.fieldnames is None
                or set(reader.fieldnames) != required
                or len(reader.fieldnames) != len(required)
            ):
                raise InputError("CSV тарифов: нужны vessel_id, три ставки, source и status")
            for index, row in enumerate(reader, start=2):
                if None in row or any(value is None for value in row.values()):
                    raise InputError(f"CSV тарифов: неверное число столбцов в строке {index}")
                ident = row["vessel_id"].strip()
                if ident not in fleet:
                    raise InputError(f"CSV тарифов: неизвестное судно в строке {index}")
                if ident in rates:
                    raise InputError(f"CSV тарифов: повтор судна {ident}")
                status = row["status"].strip()
                if status not in STATUSES:
                    raise InputError(f"CSV тарифов: неизвестный статус в строке {index}")
                rates[ident] = {
                    **{field: _money(row[field], f"{ident}.{field}") for field in RATE_FIELDS},
                    "rate_basis": STATUSES[status],
                    "source": row["source"].strip() or None,
                }
    except (OSError, UnicodeError, csv.Error) as error:
        raise InputError(f"Не удалось прочитать таблицу тарифов: {error}") from error
    if set(rates) != fleet:
        raise InputError(
            "CSV тарифов: нет строк для судов " + ", ".join(sorted(fleet - set(rates)))
        )
    terms = {k: deepcopy(v) for k, v in base_terms.items() if k not in RATE_FIELDS}
    terms.update(currency="RUB", vessel_rates=rates)
    resolve_cost_terms(cfg, terms)  # Unknown values stay null; malformed values are rejected.
    return terms
