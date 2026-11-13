"""Scenario accounting demonstration with unknown and explicit teaching stop fees."""

import json
from copy import deepcopy
from pathlib import Path

from yana_planner.multi_stop import plan_multi_stop

ROOT = Path(__file__).resolve().parents[1]


def run(output=None):
    cfg = json.loads((ROOT / "examples/multi_stop/four-recipients.json").read_text())
    terms = json.loads((ROOT / "examples/multi_stop/four-recipients.costs.json").read_text())
    complete = deepcopy(terms)
    complete["stop_fees_rub"] = {s["id"]: 1000 for s in cfg["stops"]}
    complete["source"] += (
        " Для отдельного полного учебного опыта предполагаем дополнительные 1000 ₽ за каждый заход. Это не найденные тарифы."
    )
    out = Path(output or ROOT / "output/multi-stop-economics")
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, prices in [("unknown_fees", terms), ("teaching_fees", complete)]:
        result = plan_multi_stop(cfg, cost_terms=prices)
        results[name] = result
        (out / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    full = results["teaching_fees"]["costs"]
    lines = [
        "# Экономика многопунктового рейса",
        "",
        "**Полностью учебная перевозка и условные коэффициенты.** Пункты: Казачье, Усть-Куйга, Сайды, Батагай. Места операций и новые расстояния не подтверждены. Данные не являются тарифами реального судна.",
        "",
        "Один рейс, 600 т и четыре выгрузки. Оплачиваем всё время судна от доступности в час 0 до часа 180. Готовность после возврата — час 145. Стоимость оценена после построения расписания; приоритеты решателя не изменены.",
        "",
        f"Активность: {full['active_vessel_h']} ч. Оплачиваемое ожидание: {full['paid_waiting_h']} ч. Итого доступных оплачиваемых часов: {full['available_vessel_h']}.",
        "",
        "## Что считается один раз",
        "",
        "| Операция | Часы |",
        "|---|---:|",
        *[f"| {k} | {v} |" for k, v in full["active_breakdown"].items()],
        "",
        "Ожидание включает время до погрузки, паузы между операциями, ожидание окон и оплачиваемое время после полной готовности. Активные часы исключены из ожидания. Ставка за рейс применяется один раз, не четыре раза.",
        "",
        "## Если сборы за заходы неизвестны",
        "",
        f"Известная сценарная часть: {results['unknown_fees']['costs']['known_subtotal_rub']:g} ₽. Полная стоимость не определена: сборы за четыре захода не заданы. Это не цена перевозки и не нулевые сборы.",
        "",
        "## Отдельный полностью заданный учебный опыт",
        "",
        "Для проверки механизма дополнительно задаём 1000 ₽ за каждый заход. Эти четыре суммы вымышленные и явно исключены из остальных ставок.",
        "",
        "| Статья | Количество | Ставка, ₽ | Сумма, ₽ |",
        "|---|---:|---:|---:|",
    ]
    for row in full["components"]:
        name = row["kind"] + (": " + row["stop_id"] if "stop_id" in row else "")
        lines.append(
            f"| {name} | {row['quantity']:g} | {row['rate_rub']:g} | {row['amount_rub']:g} |"
        )
    lines += [
        "",
        f"**Итого: {full['total_rub']:g} ₽ — только при этих учебных ставках.**",
        "",
        "C = F за один рейс + активные часы × ставка активности + часы ожидания × ставка ожидания + сумма отдельных сборов за заходы.",
        "",
        "## Ожидание по месту",
        "",
        "| Период | Часы |",
        "|---|---:|",
        *[f"| {w['location']} | {w['hours']:g} |" for w in full["waiting_breakdown"]],
        "",
        "Реальная экономия не установлена. Бюджет разработки продукта не входит в себестоимость рейса. Сборы за тонну, отдельные ставки по операциям и выбор наиболее дешёвого расписания пока не реализованы.",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps(
            dict(
                unknown_total=results["unknown_fees"]["costs"]["total_rub"],
                teaching_total=full["total_rub"],
                active_h=full["active_vessel_h"],
                waiting_h=full["paid_waiting_h"],
            ),
            ensure_ascii=False,
        )
    )
    return results


if __name__ == "__main__":
    run()
