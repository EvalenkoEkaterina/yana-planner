"""Reprice the fixed final READY/MILP schedules using Ushanova's S0–S5 protocol."""

import hashlib
import json
from pathlib import Path

from yana_planner.cost_sensitivity import price_fixed_plan

ROOT = Path(__file__).resolve().parents[1]
NAMES = dict(
    S0="Контроль",
    S1="Активность ×2",
    S2="Ожидание ×2",
    S3="Ожидание равно активности",
    S4="За рейс ×2",
    S5="Можно не привлекать судно без рейсов",
)


def run(inputs, result, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    prices = []
    cfg, terms = inputs["scenario"], inputs["cost_terms"]
    for variant, name in NAMES.items():
        pair = dict(variant=variant, name=name)
        for method in ("baseline", "planner"):
            pair[method] = price_fixed_plan(
                cfg, result[method]["trips"], inputs["deadline_h"], terms, variant
            )
        pair["equal_volume"] = pair["baseline"]["accepted_t"] == pair["planner"]["accepted_t"]
        pair["same_volume_difference_rub"] = (
            pair["baseline"]["total_rub"] - pair["planner"]["total_rub"]
            if pair["equal_volume"]
            else None
        )
        prices.append(pair)
    files = [
        "examples/ushanova/vessel_rates.csv",
        "examples/ushanova/terms.json",
        "docs/ushanova-defense-economics.md",
    ]
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}
    (output / "results.json").write_text(
        json.dumps(
            dict(inputs=inputs, fixed_plans=result, sensitivity=prices, source_sha256=sources),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    lines = [
        "# Экономика: чувствительность итогового примера",
        "",
        "Маршрут и историческая погода прежние. Оба фиксированных расписания построены с приоритетом объём → рейсы → приёмка. Ниже только переоценка расходов: повторной оптимизации по стоимости и выбора флота не было.",
        "",
        "Исходные ставки SIM-A/SIM-B: 25 000 ₽/рейс, 1 000 ₽/активный час, 500 ₽/час ожидания. CSV Лизы импортирован без изменения ставок. Оба судна доступны с часа 0, оплачиваемый горизонт — 120 ч. Значения сценарные, не подтверждены перевозчиком.",
        "",
        "| Вариант | READY, т | MILP, т | READY, ₽ | MILP, ₽ | READY, ₽/т | MILP, ₽/т |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for pair in prices:
        a, b = pair["baseline"], pair["planner"]
        lines.append(
            f"| {pair['variant']} — {pair['name']} | {a['accepted_t']} | {b['accepted_t']} | {a['total_rub']:g} | {b['total_rub']:g} | {format(a['rub_per_delivered_t'], 'g') if a['rub_per_delivered_t'] is not None else '—'} | {format(b['rub_per_delivered_t'], 'g') if b['rub_per_delivered_t'] is not None else '—'} |"
        )
    lines += [
        "",
        "## Использование флота в каждом фиксированном плане",
        "",
        "| План | Рейсы | Использованные суда | Активность, судо-ч | Оплачиваемое ожидание, судо-ч |",
        "|---|---:|---|---:|---:|",
    ]
    for key, label in [("baseline", "READY"), ("planner", "MILP")]:
        p = prices[0][key]
        lines.append(
            f"| {label} | {sum(v['trip_count'] for v in p['per_vessel'])} | {', '.join(p['used_vessels'])} | {p['active_vessel_h']} | {p['paid_waiting_h']} |"
        )
    lines += [
        "",
        "## Что можно сказать на защите",
        "",
        "- В контроле MILP принимает 800 т против 500 т: на 300 т больше при одинаковых сценарных расходах 255 000 ₽.",
        "- На этих двух планах совпадают число рейсов, активные и оплачиваемые часы ожидания по судам. Поэтому предложенные изменения ставок одинаково меняют их расходы. Это свойство выбранных планов, не всех возможных расписаний.",
        "- S5 здесь ничего не меняет: оба плана используют оба судна. Возможность отказаться от неиспользованного судна проверяется отдельно, не означает бесплатного ожидания используемого судна.",
        "- Более низкие расходы на тонну относятся к разным объёмам; это не снижение общего бюджета и не подтверждённое уменьшение тарифа.",
        "- Множители ×2 — условный стресс-тест. Опубликованные цены топлива и чужие договорные условия не заменяют полные ставки SIM-A/SIM-B. Топливную составляющую нельзя добавлять поверх ставки, уже включающей топливо.",
        "- Последняя приёмка у READY — час 64, у MILP — час 76; выигрыш по объёму к сроку, не ускорение.",
        "",
        "S5 — отдельная гипотеза договора: отказ от судна до начала оплаты без сборов за резервирование/отмену. Расходы используемых судов считаются с исходной доступности, включая ожидание до первого выхода и после возвращения. Эта схема не добавлена как режим в общий оптимизатор.",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    return prices


if __name__ == "__main__":
    from check_graph_weather import run as prepare

    out = ROOT / "output/economics-sensitivity"
    inputs, result, _ = prepare(out / "base", example="consolidation")
    prices = run(inputs, result, out)
    print(
        json.dumps(
            {p["variant"]: {k: p[k]["total_rub"] for k in ("baseline", "planner")} for p in prices},
            ensure_ascii=False,
        )
    )
