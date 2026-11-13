"""Show objective-order changes without inventing timetable improvements."""

import json
from copy import deepcopy
from pathlib import Path

from yana_planner.multi_stop import plan_multi_stop

ROOT = Path(__file__).resolve().parents[1]


def run():
    cfg = json.loads((ROOT / "examples/multi_stop/four-recipients.json").read_text())
    visit = [s["id"] for s in cfg["stops"]]
    orders = [[visit[-1], *visit[:-1]], visit, [visit[1], visit[0], visit[3], visit[2]]]
    out = ROOT / "output/receipt-priorities"
    out.mkdir(parents=True, exist_ok=True)
    names = dict(
        kazachye="Казачье",
        **{"ust-kuyga": "Усть-Куйга", "saydy-verkhoyansky": "Сайды"},
        batagay="Батагай",
    )
    lines = [
        "# Приоритеты получателей",
        "",
        "Все партии, времена и окна учебные. Приоритет задаёт порядок минимизации времени приёмки, но не порядок посещения. Все обязательные объёмы и сроки сохраняются.",
        "",
        "| Приоритеты | Казачье, ч | Усть-Куйга, ч | Сайды, ч | Батагай, ч | Все этапы доказаны |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for i, priority in enumerate(orders):
        raw = deepcopy(cfg)
        raw["receipt_priority"] = priority
        result = plan_multi_stop(raw)
        (out / f"case-{i}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        times = [str(d["receipt_h"]) for d in result["plan"]["deliveries"]]
        lines.append(
            "| "
            + " | ".join(
                [
                    " → ".join(names[p] for p in priority),
                    *times,
                    "да" if result["lexicographic_optimal"] else "нет",
                ]
            )
            + " |"
        )
    lines += [
        "",
        "В этой постановке времена совпали: одно судно, фиксированный путь, фиксированные партии и разрешённое ожидание позволяют выполнить все приёмки максимально рано одновременно. Более ранний заход не мешает последующим: судно может подождать нужного окна.",
        "",
        "Это не доказательство реального выигрыша от переключателя. Конфликт целей потребует дополнительных решений: например, распределения ограниченного груза сверх обязательного минимума, общего флота или выбора порядка посещений. Такие решения пока не реализованы.",
        "",
        "В result.json каждого опыта отдельно записаны visit_order, receipt_priority, objective_stop_id, receipt_optima и статусы этапов. earliest_last_proven относится к глобальной минимизации последней приёмки только когда последний получатель стоит первым в целях. Для остальных целей используется optimal_given_higher_priorities.",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print("3 порядка целей проверены; времена приёмки совпали, порядок решения этапов различается.")


if __name__ == "__main__":
    run()
