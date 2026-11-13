"""Offline report for one vessel and two mandatory deliveries."""

import json
from copy import deepcopy
from pathlib import Path

from yana_planner.multi_stop import plan_two_stop
from yana_planner.river_graph import load_network, shortest_path

ROOT = Path(__file__).resolve().parents[1]


def run():
    cfg = json.loads((ROOT / "examples/multi_stop/two-recipients.json").read_text())
    network = load_network()
    cfg["route_reference"] = dict(
        leg1=shortest_path(network, cfg["origin_id"], cfg["stops"][0]["id"]),
        leg2=shortest_path(network, cfg["stops"][0]["id"], cfg["stops"][1]["id"]),
    )
    cases = [
        ("Основной пример", deepcopy(cfg)),
        ("Запрет на втором участке [54, 60)", deepcopy(cfg)),
        ("Первому получателю нужно к часу 51", deepcopy(cfg)),
    ]
    cases[1][1]["movement_closures"]["leg2"] = [[54, 60]]
    cases[2][1]["stops"][0]["deadline_h"] = 51
    output = ROOT / "output/multi-stop-check"
    output.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Один рейс — два получателя",
        "",
        "**Все объёмы, суда, длительности, окна приёмки и сроки учебные.** Названия пунктов иллюстрируют фиксированный путь. Реальные причалы, погода и экономика не подключены.",
        "",
        "Путь по справочному графу Яны: Нижнеянск → Усть-Куйга (324 км) → Батагай (383 км). Время движения задано отдельно, не вычислено из километров.",
        "",
        "Цель: доставить обязательные 200 т в Усть-Куйгу не позже 60 ч и обязательные 400 т в Батагай не позже 120 ч; среди допустимых вариантов завершить приёмку в Батагае как можно раньше. Возврат и подготовка должны закончиться до 160 ч.",
        "",
        "| Условия | Результат | Усть-Куйга, ч | Батагай, ч | Полная готовность, ч |",
        "|---|---|---:|---:|---:|",
    ]
    for i, (name, raw) in enumerate(cases):
        result = plan_two_stop(raw)
        (output / f"case-{i}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        plan = result["plan"]
        if plan:
            a, b = plan["deliveries"]
            lines.append(
                f"| {name} | Задание выполнено | {a['receipt_h']} | {b['receipt_h']} | {plan['ready_h']} |"
            )
        else:
            lines.append(f"| {name} | Невыполнимо в модели | — | — | — |")
        if i == 0:
            base = result
    plan = base["plan"]
    times = plan["starts"]
    lines += [
        "",
        "## Как идёт судно в основном примере",
        "",
        f"- Поступление: 100 т в час 0 и 500 т в час 12. Погрузка всех 600 т: {times['load_start_h']}–{times['load_start_h'] + cfg['vessel']['load_h']} ч.",
        f"- Движение до Усть-Куйги: {times['depart1_h']}–{times['depart1_h'] + cfg['vessel']['leg1_h']} ч. Ожидание окна приёмки: {times['depart1_h'] + cfg['vessel']['leg1_h']}–{times['unload1_start_h']} ч.",
        f"- Выгрузка 200 т: {times['unload1_start_h']}–{plan['deliveries'][0]['receipt_h']} ч. На борту остаётся 400 т.",
        f"- Движение до Батагая: {times['depart2_h']}–{times['depart2_h'] + cfg['vessel']['leg2_h']} ч. Выгрузка 400 т: {times['unload2_start_h']}–{plan['deliveries'][1]['receipt_h']} ч. Остаток 0 т.",
        f"- Возврат: {times['depart_return_h']}–{times['depart_return_h'] + cfg['vessel']['return_h']} ч. Подготовка: {times['depart_return_h'] + cfg['vessel']['return_h']}–{plan['ready_h']} ч.",
        "",
        "Первый срок является обязательным ограничением. При его невыполнимости модель не отдаёт приоритет второму получателю. Второе завершение минимизируется MILP; при равенстве раньше выполняется первая приёмка, затем полная готовность.",
        "",
        "Это отдельный прототип: один рейс, одно судно, два получателя, фиксированные партии и ручные времена. Он не распределяет пока переменные объёмы между судами и не ищет альтернативные пути. В основной сайт не подключён.",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps(
            dict(
                status=base["status"],
                deliveries=plan["deliveries"],
                earliest_proven=base["earliest_second_proven"],
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    run()
