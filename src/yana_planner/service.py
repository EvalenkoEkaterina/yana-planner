"""Small adapter over existing planners; no independent scheduling logic."""

import hashlib
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from .domain import InputError, Scenario
from .experiments import comparisons, event_effects
from .io import _finite, export_result, read_json, read_plan
from .planning import compare
from .replanning import apply_arrival_delay, run_replanning

ROOT = Path(__file__).resolve().parents[2]
NAMES = {"r03": "R03 — обычный расчёт", "delay": "R03 — задержка топлива"}

VESSEL_FIELDS = {
    "available_h": "Доступно с, ч",
    "capacity_t": "Вместимость, т",
    "load_h": "Погрузка, ч",
    "up_h": "Движение с грузом, ч",
    "unload_h": "Приёмка, ч",
    "return_h": "Возвращение, ч",
    "prep_h": "Подготовка, ч",
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
    cfg["vessels"] = []
    for i, ident in enumerate(ids):
        if not ident.strip() or len(ident.strip()) > 40:
            raise InputError("Название судна: от 1 до 40 символов")
        v = {"id": ident.strip()}
        for key, label in VESSEL_FIELDS.items():
            v[key] = number(
                "vessel_" + key,
                f"{v['id']}: {label}",
                minimum=0 if key == "available_h" else 1,
                upper=1_000_000 if key == "capacity_t" else maximum,
                index=i,
            )
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


def calculate(scenario, deadline, *, max_deadline=492, time_limit=2, custom=None, adaptive=False):
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
    result = (
        run_replanning(cfg, event, deadline, **kwargs)
        if event
        else compare(cfg, deadline, **kwargs)
    )
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
        b"".join(f.read_bytes() for f in sorted(Path(__file__).parent.glob("*.py")))
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
