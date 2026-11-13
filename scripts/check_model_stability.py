"""One-factor checks around the teaching example; no selection of winners."""

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

from yana_planner.domain import InputError
from yana_planner.service import calculate_package

ROOT = Path(__file__).resolve().parents[1]


def variations(package):
    changes = [
        ("Исходный пример", None, None),
        ("Основная партия в час 6", "arrival", 6),
        ("Основная партия в час 18", "arrival", 18),
        ("Основная партия в час 24", "arrival", 24),
        ("Первая партия 200 т", "first_stock", 200),
        ("Первая партия 400 т", "first_stock", 400),
        ("SIM-B доступно с часа 12", "available", 12),
        ("SIM-B доступно с часа 24", "available", 24),
        ("SIM-B доступно с часа 48", "available", 48),
        ("Срок приёмки 85 ч", "deadline", 85),
        ("Срок приёмки 110 ч", "deadline", 110),
        ("Вместимости 300 и 400 т", "capacity_a", 300),
        ("Вместимости 400 и 300 т", "capacity_b", 300),
        ("Погрузка 20 т/ч, подготовка 1 ч", "loading_rate", 20),
    ]
    for index, (label, field, value) in enumerate(changes):
        case = deepcopy(package)
        cfg = case["scenario"]
        cfg["id"] = f"STABILITY-{index:02}"
        if field == "arrival":
            cfg["arrivals"][1]["h"] = value
        elif field == "first_stock":
            cfg["arrivals"][0]["t"] = value
            cfg["arrivals"][1]["t"] = cfg["target_t"] - value
        elif field == "available":
            cfg["vessels"][1]["available_h"] = value
        elif field == "deadline":
            case["deadline_h"] = value
        elif field in ("capacity_a", "capacity_b"):
            cfg["vessels"][field == "capacity_b"]["capacity_t"] = value
        elif field == "loading_rate":
            for vessel in cfg["vessels"]:
                vessel.update(load_rate_tph=value, load_setup_h=1)
                vessel.pop("load_h", None)
        # Replacing fixture descriptions avoids stale claims about changed inputs.
        case["facts"] = [f for f in case["facts"] if f["field"] not in ("transport", "variation")]
        case["facts"].append(dict(field="variation", value=label, status="scenario_assumption"))
        yield label, case


def classify(result):
    a, b = result["baseline"], result["planner"]
    if any(p.get("status") != "valid_simulation" for p in (a, b)):
        return "Допустимость не подтверждена"
    # Evaluate the implemented lexicographic objective, not a preferred metric.
    if a["accepted_t"] != b["accepted_t"]:
        return "Больше топлива" if b["accepted_t"] > a["accepted_t"] else "Хуже по объёму"
    if a["trip_count"] != b["trip_count"]:
        return "Меньше рейсов" if b["trip_count"] < a["trip_count"] else "Хуже по рейсам"
    if a["last_receipt_h"] != b["last_receipt_h"]:
        return "Раньше приёмка" if b["last_receipt_h"] < a["last_receipt_h"] else "Позже приёмка"
    return "Показатели совпали"


def run(package, output):
    if package.get("strategy") != "trips":
        raise InputError(
            "Эта серия проверяет приоритет объёма и рейсов; для cost нужен отдельный протокол"
        )
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for label, case in variations(package):
        try:
            inputs, result = calculate_package(case, search_seconds=2)
        except InputError as exc:
            error = dict(
                name=label, outcome="Условия требуют исправления", error=str(exc), optimal=False
            )
            rows.append(error)
            (output / f"{case['scenario']['id']}.json").write_text(
                json.dumps(dict(package=case, error=str(exc)), ensure_ascii=False, indent=2) + "\n"
            )
            continue
        summary = dict(
            name=label,
            outcome=classify(result),
            optimal=bool(result["solver"]) and all(s["optimal"] for s in result["solver"]),
        )
        for key in ("baseline", "planner"):
            plan = result[key]
            summary[key] = {k: plan.get(k) for k in ("accepted_t", "trip_count", "last_receipt_h")}
            summary[key]["cost_rub"] = result["costs"][key]["total_rub"]
        ident = case["scenario"]["id"]
        (output / f"{ident}.json").write_text(
            json.dumps(dict(inputs=inputs, result=result), ensure_ascii=False, indent=2) + "\n"
        )
        rows.append(summary)
    counts = dict(Counter(row["outcome"] for row in rows))
    (output / "summary.json").write_text(
        json.dumps(
            dict(search_seconds_per_stage=2, counts=counts, cases=rows),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    lines = [
        "# Проверка устойчивости учебного примера",
        "",
        "Все варианты заданы заранее и включены в сводку. Это локальная проверка вокруг одного примера, не репрезентативная выборка реальных перевозок.",
        "",
        "Один маршрут, тот же исторический погодный профиль без превышений. Меняется одна группа условий за раз. Приоритет: объём → рейсы → последняя приёмка; стоимость оценена отдельно. Лимит — 2 секунды на этап.",
        "",
        "| Условия | Тонны READY → MILP | Рейсы | Приёмка, ч | Расходы, ₽ | Итог | Оптимальность MILP |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        if "error" in row:
            lines.append(
                "| " + row["name"] + " | — | — | — | — | " + row["error"] + " | не определена |"
            )
            continue
        a, b = row["baseline"], row["planner"]
        pairs = [
            f"{a[k]} → {b[k]}" for k in ("accepted_t", "trip_count", "last_receipt_h", "cost_rub")
        ]
        lines.append(
            "| "
            + " | ".join(
                [
                    row["name"],
                    *pairs,
                    row["outcome"],
                    "подтверждена" if row["optimal"] else "не подтверждена",
                ]
            )
            + " |"
        )
    lines += [
        "",
        "## Число исходов",
        "",
        *[f"- {name}: {count}" for name, count in counts.items()],
        "",
        "При разных объёмах расходы не интерпретируются как экономия на одинаковой выполненной работе. Погодная поправка не включена; времена движения и ставки остаются учебными. Ухудшения и ограничения поиска не скрываются.",
    ]
    (output / "summary.md").write_text("\n".join(lines) + "\n")
    return rows, counts


if __name__ == "__main__":
    from check_graph_weather import run as prepare

    folder = ROOT / "output/model-stability"
    prepare(folder / "base", example="consolidation")
    package = json.loads((folder / "base/package.json").read_text())
    _, counts = run(package, folder)
    print(json.dumps(counts, ensure_ascii=False))
