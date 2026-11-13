"""Declared scenario series for the two-recipient prototype, including failures."""

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

from yana_planner.multi_stop import check_voyage, plan_two_stop

ROOT = Path(__file__).resolve().parents[1]


def scenarios(base):
    definitions = [
        ("Базовые условия", "base", None),
        ("Основная партия в час 20", "arrival", 20),
        ("Основная партия в час 24", "arrival", 24),
        ("Все 600 т доступны сразу", "initial", 600),
        ("Первая приёмка открывается в час 40", "first_window", [40, 72]),
        ("Первая приёмка открывается в час 60", "first_window", [60, 80]),
        ("Вторая приёмка открывается в час 100", "second_window", [100, 130]),
        ("Первый срок — час 51", "first_deadline", 51),
        ("Второй срок — час 87", "second_deadline", 87),
        ("Вместимость 599 т вместо 600", "capacity", 599),
        ("Всего поступит 599 т", "supply", 499),
        ("Запрет первого плеча [30,40)", "leg1", [[30, 40]]),
        ("Запрет второго плеча [54,60)", "leg2", [[54, 60]]),
        ("Запрет возврата [90,100)", "return", [[90, 100]]),
        ("Распределение 300 т / 300 т", "volumes", [300, 300]),
        ("Первая выгрузка длится 8 ч", "first_unload", 8),
        ("Судно доступно с часа 20", "availability", 20),
        ("Готовность после возврата до часа 131", "horizon", 131),
    ]
    for i, (label, field, value) in enumerate(definitions):
        cfg = deepcopy(base)
        if field == "arrival":
            cfg["arrivals"][0]["h"] = value
        elif field == "initial":
            cfg.update(initial_source_t=value, arrivals=[])
        elif field == "first_window":
            cfg["stops"][0]["windows"] = [value]
        elif field == "second_window":
            cfg["stops"][1]["windows"] = [value]
        elif field == "first_deadline":
            cfg["stops"][0]["deadline_h"] = value
        elif field == "second_deadline":
            cfg["stops"][1]["deadline_h"] = value
        elif field == "capacity":
            cfg["vessel"]["capacity_t"] = value
        elif field == "supply":
            cfg["arrivals"][0]["t"] = value
        elif field in ("leg1", "leg2", "return"):
            cfg["movement_closures"][field] = value
        elif field == "volumes":
            for stop, quantity in zip(cfg["stops"], value, strict=True):
                stop["required_t"] = quantity
        elif field == "first_unload":
            cfg["stops"][0]["unload_h"] = value
        elif field == "availability":
            cfg["vessel"]["available_h"] = value
        elif field == "horizon":
            cfg["horizon_h"] = value
        yield f"{i:02}", label, cfg


def run(output=None):
    base = json.loads((ROOT / "examples/multi_stop/two-recipients.json").read_text())
    out = Path(output or ROOT / "output/multi-stop-scenarios")
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for ident, name, cfg in scenarios(base):
        result = plan_two_stop(cfg)
        plan = result["plan"]
        if plan:
            checked = check_voyage(cfg, plan["starts"])
            if checked["status"] != "valid_simulation":
                raise RuntimeError("Независимая проверка не подтвердила расписание")
        (out / f"case-{ident}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        rows.append(
            dict(
                name=name,
                status=result["status"],
                first_h=plan["deliveries"][0]["receipt_h"] if plan else None,
                second_h=plan["deliveries"][1]["receipt_h"] if plan else None,
                ready_h=plan["ready_h"] if plan else None,
                earliest_proven=result.get("earliest_second_proven", False),
            )
        )
    counts = dict(Counter(row["status"] for row in rows))
    (out / "summary.json").write_text(
        json.dumps(dict(counts=counts, cases=rows), ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# Проверка двух получателей: 18 условий",
        "",
        "Все сценарии учебные и заданы до запуска. Меняется одна группа условий за раз; ни один исход не исключён. Это не сравнение с READY и не статистика реальных перевозок.",
        "",
        "| Условия | Выполнимость | Первый получатель, ч | Второй получатель, ч | Полная готовность, ч | Минимальность второй приёмки |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        status = {"goal_met": "Выполнено", "infeasible": "Невыполнимо в модели"}.get(
            row["status"], row["status"]
        )
        times = [
            str(row[k]) if row[k] is not None else "—" for k in ("first_h", "second_h", "ready_h")
        ]
        proof = "Подтверждена" if row["earliest_proven"] else "—"
        lines.append("| " + " | ".join([row["name"], status, *times, proof]) + " |")
    lines += [
        "",
        "Сводка: " + json.dumps(counts, ensure_ascii=False),
        "",
        "Модель сохраняет обязательные объёмы обоим получателям. При невыполнимости не выдаёт частичную поставку за успешный план. Независимый проверяющий повторно проверяет каждый найденный план.",
        "",
        "Длительности выгрузки фиксированы: изменение партии само по себе не меняет время в этом прототипе. При переходе к переменным объёмам нужна модель производительности. Погода, ставки, несколько судов и произвольный порядок точек сюда пока не подключены.",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return rows, counts


if __name__ == "__main__":
    _, counts = run()
    print(json.dumps(counts, ensure_ascii=False))
