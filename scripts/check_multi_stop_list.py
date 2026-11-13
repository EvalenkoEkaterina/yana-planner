"""Demonstrate a fixed-order list; all new stop operating parameters are synthetic."""

import json
from pathlib import Path

from yana_planner.multi_stop import plan_multi_stop

ROOT = Path(__file__).resolve().parents[1]


def run():
    cfg = json.loads((ROOT / "examples/multi_stop/four-recipients.json").read_text())
    result = plan_multi_stop(cfg)
    out = ROOT / "output/multi-stop-list"
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    names = {
        "kazachye": "Казачье",
        "ust-kuyga": "Усть-Куйга",
        "saydy-verkhoyansky": "Сайды",
        "batagay": "Батагай",
    }
    lines = [
        "# Одно судно — список получателей",
        "",
        "**Полностью учебная перевозка.** Использована предлагаемая последовательность населённых пунктов. Места выгрузки и длины новых плеч не подтверждены; времена, объёмы и окна заданы сценарно. Это не новый подтверждённый маршрут графа.",
        "",
        "Погрузка в Нижнеянске: 600 т. Все четыре объёма и срока обязательны. Среди допустимых планов минимизируем завершение приёмки в Батагае. При равенстве — более ранние приёмки в промежуточных пунктах по порядку, затем готовность после возврата.",
        "",
        "| Получатель | Объём, т | Срок, ч | Приёмка завершена, ч | Остаток на борту, т |",
        "|---|---:|---:|---:|---:|",
    ]
    if result["plan"]:
        for stop, delivery in zip(cfg["stops"], result["plan"]["deliveries"], strict=True):
            lines.append(
                f"| {names[stop['id']]} | {stop['required_t']} | {stop['deadline_h']} | {delivery['receipt_h']} | {delivery['cargo_after_t']} |"
            )
        lines += [
            "",
            f"Готовность после возврата и подготовки: {result['plan']['ready_h']} ч.",
            f"Минимальность времени последней приёмки: {'подтверждена' if result['earliest_last_proven'] else 'не подтверждена'}.",
        ]
    else:
        lines += ["", result["status"]]
    lines += [
        "",
        "Расчёт отдельно от сайта. Поддерживается 1–8 получателей, один фиксированный порядок, одно судно и один рейс. Порядок важности получателей можно задавать полем receipt_priority в JSON. Перестановка посещений, общий флот, экономика, прогноз и интерфейс выбора приоритетов ещё не подключены.",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps(
            dict(
                status=result["status"],
                last_receipt=result["plan"]["deliveries"][-1]["receipt_h"]
                if result["plan"]
                else None,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    run()
