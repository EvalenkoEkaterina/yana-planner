"""Input contract. Unknown values never become zero or a production clearance."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from fractions import Fraction
from hashlib import sha256
from typing import Any


class InputError(ValueError):
    """Malformed or unsupported input; safe to display in CLI."""


class ConfirmationRequired(InputError):
    """Missing facts or scenario permissions; no numeric delivery is confirmed."""


class InterventionRequired(InputError):
    """An already committed decision cannot satisfy newly supplied constraints."""


def integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if value is None:
        raise ConfirmationRequired(f"{name}: значение не задано")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{name}: требуется число")
    try:
        valid = math.isfinite(value) and value == int(value) and value >= minimum
    except (ValueError, OverflowError):
        valid = False
    if not valid:
        raise InputError(f"{name}: требуется целое число >= {minimum}")
    return int(value)


def loading_hours(vessel: Mapping[str, Any], tonnes: int) -> int:
    """Planner-side hourly duration; the execution checker reconstructs it independently."""
    if "load_rate_tph" not in vessel:
        return vessel["load_h"]
    rate = Fraction(str(vessel["load_rate_tph"]))
    transfer_h = (tonnes * rate.denominator + rate.numerator - 1) // rate.numerator
    return vessel.get("load_setup_h", 0) + transfer_h


@dataclass(frozen=True)
class Voyage:
    vessel_id: str
    start_h: int
    quantity_t: int

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Voyage:
        if (
            not isinstance(raw, Mapping)
            or not isinstance(raw.get("vessel_id"), str)
            or not raw["vessel_id"]
        ):
            raise InputError("Рейс: нужен непустой vessel_id")
        return cls(
            raw["vessel_id"],
            integer(raw.get("start_h"), "start_h"),
            integer(raw.get("quantity_t"), "quantity_t", minimum=1),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Scenario:
    """Validated, defensively copied input. Access returns copies to avoid hidden edits."""

    _data: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Scenario:
        if not isinstance(raw, Mapping):
            raise InputError("Сценарий должен быть JSON-объектом")
        data = deepcopy(dict(raw))
        if data.get("mode") != "SIM_ONLY":
            raise ConfirmationRequired(
                "Расчёт поддерживает SIM_ONLY; BASE не превращается в сценарий автоматически"
            )
        gates = ["navigation_assumed", "reception_assumed", "fleet_assumed", "supply_assumed"]
        missing = [k for k in gates if data.get(k) is not True]
        if missing:
            raise ConfirmationRequired("Не заданы условия SIM: " + ", ".join(missing))
        for k in ["target_t", "initial_source_t", "destination_capacity_t", "return_cutoff_h"]:
            data[k] = integer(data.get(k), k)
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise InputError("Сценарию нужен непустой id")
        vessels = data.get("vessels")
        if not isinstance(vessels, list) or not vessels:
            raise InputError("Нужен непустой список vessels")
        ids = []
        for v in vessels:
            if not isinstance(v, dict) or not isinstance(v.get("id"), str) or not v["id"]:
                raise InputError("Судну нужен id")
            ids.append(v["id"])
            for k in [
                "available_h",
                "capacity_t",
                "up_h",
                "unload_h",
                "return_h",
                "prep_h",
            ]:
                v[k] = integer(v.get(k), f"{v['id']}.{k}", minimum=0 if k == "available_h" else 1)
            if "load_rate_tph" in v:
                rate = v["load_rate_tph"]
                try:
                    valid_rate = (
                        not isinstance(rate, bool)
                        and isinstance(rate, (int, float))
                        and math.isfinite(rate)
                        and rate > 0
                    )
                except OverflowError:
                    valid_rate = False
                if not valid_rate:
                    raise InputError(
                        f"{v['id']}.load_rate_tph: требуется положительная скорость, т/ч"
                    )
                v["load_setup_h"] = integer(v.get("load_setup_h", 0), f"{v['id']}.load_setup_h")
                if "load_h" in v:
                    v["load_h"] = integer(v["load_h"], f"{v['id']}.load_h", minimum=1)
            else:
                v["load_h"] = integer(v.get("load_h"), f"{v['id']}.load_h", minimum=1)
                if "load_setup_h" in v:
                    raise InputError(f"{v['id']}: load_setup_h требует load_rate_tph")
        if len(ids) != len(set(ids)):
            raise InputError("Повторяющиеся ID судов")
        for name in ["arrivals", "withdrawals"]:
            records = data.get(name, [] if name == "withdrawals" else None)
            if not isinstance(records, list):
                raise InputError(f"{name}: нужен список событий")
            for event in records:
                if not isinstance(event, dict):
                    raise InputError(f"{name}: событие должно быть объектом")
                event["h"] = integer(event.get("h"), f"{name}.h")
                event["t"] = integer(event.get("t"), f"{name}.t")
                if name == "withdrawals" and event.get("confirmed_in_scenario") is not True:
                    raise ConfirmationRequired("Отбор не подтверждён даже как условие сценария")
            data[name] = sorted(records, key=lambda x: x["h"])
        calendar = data.get("payload_calendar")
        if not isinstance(calendar, list) or not calendar:
            raise InputError("Нужен непустой payload_calendar")
        for c in calendar:
            if not isinstance(c, dict):
                raise InputError("payload_calendar: запись должна быть объектом")
            for k in ["from_h", "capacity_t"]:
                c[k] = integer(c.get(k), f"payload_calendar.{k}")
        times = [c["from_h"] for c in calendar]
        if min(times) != 0 or len(times) != len(set(times)):
            raise InputError("Календарь партии должен начинаться в 0 ч и не иметь повторных границ")
        data["payload_calendar"] = sorted(calendar, key=lambda x: x["from_h"])
        closures = data.get("closures", [])
        if not isinstance(closures, list):
            raise InputError("closures: нужен список интервалов")
        for interval in closures:
            if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                raise InputError("Остановка: нужны две границы [start, end)")
            a = integer(interval[0], "closure.start")
            b = integer(interval[1], "closure.end")
            if a >= b:
                raise InputError("Остановка должна иметь положительную длительность")
        data["closures"] = sorted(closures)
        data["real_admissibility"] = "requires_confirmation"
        data.setdefault("schema_version", "1.0")
        if data["schema_version"] != "1.0":
            raise InputError("Неподдерживаемая schema_version")
        return cls(data)

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self._data)

    @property
    def fingerprint(self) -> str:
        return sha256(
            json.dumps(self._data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        ).hexdigest()


def proposals(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        raise InputError("Расписание должно быть списком рейсов")
    result = [Voyage.from_dict(r).to_dict() for r in records]
    if len({(r["vessel_id"], r["start_h"]) for r in result}) != len(result):
        raise InputError("Повторяющийся рейс одного судна в одно время")
    return sorted(result, key=lambda x: (x["start_h"], x["vessel_id"]))
