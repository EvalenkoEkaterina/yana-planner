"""Small adapter over existing planners; no independent scheduling logic."""

import hashlib
import math
import re
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from .domain import InputError, Scenario, integer
from .economics import resolve_cost_terms
from .experiments import comparisons, event_effects
from .io import _finite, export_result, read_json, read_plan
from .planning import compare
from .replanning import apply_arrival_delay, apply_event, run_replanning

ROOT = Path(__file__).resolve().parents[2]
NAMES = {"r03": "R03 — обычный расчёт", "delay": "R03 — задержка топлива"}

VESSEL_FIELDS = {
    "available_h": "Доступно с, ч",
    "capacity_t": "Вместимость, т",
    "load_h": "Погрузка без скорости, ч",
    "up_h": "Движение с грузом, ч",
    "unload_h": "Приёмка, ч",
    "return_h": "Возвращение, ч",
    "prep_h": "Подготовка после возврата, ч",
}


def form_defaults(preset="r03"):
    if preset not in NAMES:
        raise InputError("Неизвестный пример. Выберите обычный R03 или задержку топлива")
    cfg = read_json(ROOT / "examples/r03.json")
    values = {
        key: [str(cfg[key])]
        for key in ("target_t", "initial_source_t", "destination_capacity_t", "return_cutoff_h")
    }
    values.update(
        scenario=[preset],
        deadline=["280" if preset == "delay" else "328"],
        search_seconds=["auto"],
        announced_at_h=["60"],
        delayed_arrival=["2"],
        new_arrival_h=["168"],
    )
    for key in ["id", *VESSEL_FIELDS]:
        values["vessel_" + key] = [str(v[key]) for v in cfg["vessels"]]
    for key in ["h", "t"]:
        values["arrival_" + key] = [str(a[key]) for a in cfg["arrivals"]]
    return values


def custom_conditions(values, *, with_delay, maximum=492):
    """Parse editable fields before starting either planner; never silently default them."""

    def number(key, label, minimum=0, upper=maximum, index=0):
        items = values.get(key, [])
        value = items[index] if index < len(items) else ""
        if (
            not value.isascii()
            or not value.isdecimal()
            or len(value) > 7
            or not minimum <= int(value) <= upper
        ):
            raise InputError(f"{label}: целое число от {minimum} до {upper}")
        return int(value)

    cfg = read_json(ROOT / "examples/r03.json")
    for key, label in [
        ("target_t", "Цель, т"),
        ("initial_source_t", "Начальный запас, т"),
        ("destination_capacity_t", "Вместимость получателя, т"),
    ]:
        cfg[key] = number(
            key, label, minimum=0 if key == "initial_source_t" else 1, upper=1_000_000
        )
    cfg["return_cutoff_h"] = number("return_cutoff_h", "Возврат до, ч", minimum=1)
    ids = values.get("vessel_id", [])
    if not 1 <= len(ids) <= 6:
        raise InputError("Добавьте от 1 до 6 судов")
    if any(len(values.get("vessel_" + k, [])) != len(ids) for k in VESSEL_FIELDS):
        raise InputError("Заполните все параметры каждого судна")
    for key in ["vessel_load_rate_tph", "vessel_load_setup_h"]:
        if key in values and len(values[key]) != len(ids):
            raise InputError("Параметры скорости погрузки должны соответствовать строкам судов")
    cfg["vessels"] = []
    for i, ident in enumerate(ids):
        if not ident.strip() or len(ident.strip()) > 40:
            raise InputError("Название судна: от 1 до 40 символов")
        v = {"id": ident.strip()}
        raw_rate = values.get("vessel_load_rate_tph", [""] * len(ids))[i].strip()
        raw_setup = values.get("vessel_load_setup_h", ["0"] * len(ids))[i].strip()
        for key, label in VESSEL_FIELDS.items():
            if key == "load_h" and raw_rate:
                continue  # A fixed duration is not an input to the throughput model.
            v[key] = number(
                "vessel_" + key,
                f"{v['id']}: {label}",
                minimum=0 if key == "available_h" else 1,
                upper=1_000_000 if key == "capacity_t" else maximum,
                index=i,
            )
        if raw_rate:
            if len(raw_rate) > 24:
                raise InputError(f"{v['id']}: слишком длинное значение скорости погрузки")
            try:
                rate = float(raw_rate.replace(",", "."))
            except ValueError as error:
                raise InputError(f"{v['id']}: скорость погрузки — число т/ч") from error
            if not math.isfinite(rate) or not 0 < rate <= 1_000_000:
                raise InputError(f"{v['id']}: скорость погрузки — больше 0 и до 1000000 т/ч")
            v["load_rate_tph"] = rate
            if raw_setup and "vessel_load_setup_h" in values:
                v["load_setup_h"] = number(
                    "vessel_load_setup_h", "Подготовка к погрузке, ч", index=i
                )
            else:
                v["load_setup_h"] = 0
        elif raw_setup not in ("", "0"):
            raise InputError(f"{v['id']}: для подготовки к погрузке укажите скорость, т/ч")
        cfg["vessels"].append(v)
    hours = values.get("arrival_h", [])
    if not 1 <= len(hours) <= 12 or len(values.get("arrival_t", [])) != len(hours):
        raise InputError("Добавьте от 1 до 12 поступлений и заполните время и объём")
    indexed = [
        (
            i,
            dict(
                h=number("arrival_h", "Час поступления", index=i),
                t=number("arrival_t", "Объём поступления, т", minimum=1, upper=1_000_000, index=i),
            ),
        )
        for i in range(len(hours))
    ]
    indexed.sort(key=lambda pair: pair[1]["h"])
    cfg["arrivals"] = [a for _, a in indexed]
    # No hidden 400-t cap: each vessel's own capacity applies to custom inputs.
    cfg["payload_calendar"] = [
        {"from_h": 0, "capacity_t": max(v["capacity_t"] for v in cfg["vessels"])}
    ]
    original = read_json(ROOT / "examples/r03.json")
    if cfg != original:
        cfg.update(id="CUSTOM", label="Свои условия")
        cfg.pop("factors", None)
        cfg["provenance"] = dict(
            kind="synthetic_scenario",
            origin="web form",
            operating_parameters="user supplied; not verified real vessels",
        )
    cfg = Scenario.from_dict(cfg).to_dict()
    event = None
    if with_delay:
        row = (
            number("delayed_arrival", "Номер поступления с задержкой", minimum=1, upper=len(hours))
            - 1
        )
        event = dict(
            id="CUSTOM-DELAY",
            type="arrival_delay",
            announced_at_h=number("announced_at_h", "Сообщение о задержке, ч"),
            arrival_index=next(i for i, (old, _) in enumerate(indexed) if old == row),
            new_arrival_h=number("new_arrival_h", "Новое время поступления, ч"),
            status="scenario_assumption",
        )
        apply_arrival_delay(cfg, event)
    return cfg, event


def calculate(
    scenario,
    deadline,
    *,
    max_deadline=492,
    time_limit=2,
    custom=None,
    adaptive=False,
    total_budget_s=None,
):
    """Validate bounded hours, run existing core and retain complete inputs."""
    if scenario not in NAMES:
        raise InputError("Выберите расчёт без задержки или с задержкой топлива")
    if (
        not isinstance(deadline, str)
        or not deadline.isascii()
        or not deadline.isdecimal()
        or len(deadline) > 3
        or not 0 < int(deadline) <= max_deadline
    ):
        raise InputError(f"Срок: целое число от 1 до {max_deadline} часов")
    deadline = int(deadline)
    cfg = Scenario.from_dict(read_json(ROOT / "examples/r03.json")).to_dict()
    event = read_json(ROOT / "examples/arrival_delay.json") if scenario == "delay" else None
    if custom is not None:
        cfg, event = custom_conditions(custom, with_delay=scenario == "delay", maximum=max_deadline)
    if deadline > cfg["return_cutoff_h"]:
        raise InputError(
            f"Срок приёмки не должен превышать выбранный срок возврата ({cfg['return_cutoff_h']} ч)"
        )
    if event and event["announced_at_h"] >= deadline:
        raise InputError("Сообщение о задержке должно поступить раньше срока приёмки")
    reference = (
        read_plan(ROOT / "examples/r03.reference.json")
        if deadline == 328 and cfg["id"] == "R03"
        else None
    )
    kwargs = dict(time_limit=time_limit, incumbent=reference, adaptive=adaptive)
    if total_budget_s is not None:
        kwargs["search_deadline"] = monotonic() + max(0, total_budget_s)
    result = (
        run_replanning(cfg, event, deadline, **kwargs)
        if event
        else compare(cfg, deadline, **kwargs)
    )
    return _report(cfg, event, deadline, result, time_limit, reference, adaptive)


def calculate_request(payload, *, max_deadline=492, total_budget_s=None):
    """JSON boundary for the frontend; never complete missing operational data."""
    if not isinstance(payload, dict):
        raise InputError("Запрос должен быть JSON-объектом")
    allowed = {"scenario", "deadline_h", "event", "strategy", "cost_terms", "search_seconds"}
    if set(payload) - allowed:
        raise InputError("Неизвестные поля запроса: " + ", ".join(sorted(set(payload) - allowed)))
    cfg = Scenario.from_dict(payload.get("scenario")).to_dict()
    deadline = integer(payload.get("deadline_h"), "deadline_h", minimum=1)
    if deadline > max_deadline or cfg["return_cutoff_h"] > max_deadline:
        raise InputError(f"Веб-расчёт: срок приёмки и возврата не больше {max_deadline} ч")
    if deadline > cfg["return_cutoff_h"]:
        raise InputError("Срок приёмки не должен превышать срок возврата")
    # Bound request size separately from the core/CLI campaign limits.
    if len(cfg["vessels"]) > 6 or any(
        len(cfg[name]) > 100 for name in ("arrivals", "withdrawals", "closures", "payload_calendar")
    ):
        raise InputError("Веб-расчёт: до 6 судов и до 100 записей в каждом календаре")
    strategy = payload.get("strategy", "trips")
    if strategy not in ("trips", "cost"):
        raise InputError("strategy: выберите trips или cost")
    terms = payload.get("cost_terms")
    if strategy == "cost" and terms is None:
        raise InputError("Для выбора по стоимости нужны cost_terms")
    if terms is not None:
        resolved = resolve_cost_terms(cfg, terms)
        if strategy == "cost" and resolved["missing"]:
            raise InputError("Для выбора по стоимости заполните: " + ", ".join(resolved["missing"]))
    seconds = payload.get("search_seconds", "auto")
    if type(seconds) not in (str, int) or seconds not in ("auto", 2, 10, 30):
        raise InputError("search_seconds: auto либо число 2, 10 или 30")
    adaptive = seconds == "auto"
    time_limit = 30 if adaptive else seconds
    event = deepcopy(payload.get("event"))
    if isinstance(event, dict) and event.get("type") == "arrival_delay":
        index = integer(event.get("arrival_index"), "arrival_index")
        original = payload["scenario"]["arrivals"]
        if index >= len(original):
            raise InputError("arrival_index вне списка поступлений")
        event["arrival_index"] = next(
            i
            for i, (old, _) in enumerate(sorted(enumerate(original), key=lambda x: x[1]["h"]))
            if old == index
        )
    if event is not None:
        apply_event(cfg, event)
        if event["announced_at_h"] >= deadline:
            raise InputError("Событие должно быть объявлено раньше срока приёмки")
    kwargs = dict(time_limit=time_limit, adaptive=adaptive, strategy=strategy, cost_terms=terms)
    if total_budget_s is not None:
        kwargs["search_deadline"] = monotonic() + max(0, total_budget_s)
    result = (
        run_replanning(cfg, event, deadline, **kwargs)
        if event is not None
        else compare(cfg, deadline, **kwargs)
    )
    inputs, result = _report(cfg, event, deadline, result, time_limit, None, adaptive)
    inputs["settings"]["strategy"] = strategy
    inputs["cost_terms"] = terms
    return inputs, result


def calculate_package(package, *, search_seconds="auto", max_deadline=492, total_budget_s=None):
    """Import a route envelope and retain its provenance with the calculation."""
    from .route_import import import_route_package

    package = import_route_package(package)
    inputs, result = calculate_request(
        {
            key: package.get(key)
            for key in ("scenario", "deadline_h", "event", "strategy", "cost_terms")
        }
        | dict(search_seconds=search_seconds),
        max_deadline=max_deadline,
        total_budget_s=total_budget_s,
    )
    inputs["route_package"] = package
    return inputs, result


def _report(cfg, event, deadline, result, time_limit, reference, adaptive):
    result["comparisons"] = comparisons(result)
    result["event_effects"] = event_effects(result)
    with TemporaryDirectory() as directory:
        export_result(directory, result)
        result["report_html"] = re.sub(
            r' · <a href="(?:comparison|schedule|events)\.csv">.*?</a>',
            "",
            (Path(directory) / "report.html").read_text(),
        )
    result["code_version"] = hashlib.sha256(
        b"".join(f.read_bytes() for f in sorted(Path(__file__).parent.rglob("*.py")))
    ).hexdigest()
    inputs = dict(
        scenario=cfg,
        event=event,
        deadline_h=deadline,
        settings=dict(
            time_limit_per_stage_s=time_limit, reference=reference, adaptive_search=adaptive
        ),
    )
    return inputs, _finite(result)
