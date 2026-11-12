"""Readable charts from saved, independently checked results; never solves a plan."""

from html import escape
from math import isfinite

from .experiments import comparisons

VISUAL_STYLE = """
.vr{color:#202024;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px 0}
.vr *{box-sizing:border-box}.vr h2,.vr h3,.vr p{margin:0}.vr-hero{padding:28px;border:1px solid #eadde4;border-radius:18px;background:#f8edf2;margin-bottom:18px}
.vr-kicker{font-size:11px;letter-spacing:1.3px;text-transform:uppercase;color:#87536b;margin-bottom:10px!important}.vr h2{font-size:27px;line-height:1.25;letter-spacing:-.6px;margin-bottom:10px}
.vr-note{font-size:13px;line-height:1.6;color:#66606a}.vr-legend{display:flex;flex-wrap:wrap;gap:18px;font-size:12px;margin:18px 0}.vr-legend span{display:flex;align-items:center;gap:7px}.vr-dot{display:inline-block;width:10px;height:10px;border-radius:3px;background:#8c929e}.vr-dot-milp{background:#ae2f67}
.vr-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.vr-card{padding:22px;border:1px solid #e8e2e6;border-radius:16px;background:white;min-width:0}.vr h3{font-size:17px;line-height:1.3;margin-bottom:8px}.vr-card svg{display:block;width:100%;height:auto}.vr-scale{font-size:11px;color:#747079;margin-bottom:6px!important}
.vr-delta{display:inline-block;border-radius:7px;padding:6px 10px;font-size:12px;font-weight:650;margin-top:5px!important;background:#f0edf0;color:#625b65}.vr-good{background:#e9f4ee;color:#246348}.vr-caution{background:#fff2df;color:#86510d}.vr-method{font-size:13px;color:#625b65}.vr-foot{margin-top:8px!important;font-size:12px;color:#747079;line-height:1.5}
.vr-details{margin-top:20px;border:1px solid #e8e2e6;border-radius:12px;padding:17px;background:#fff}.vr-details>summary{cursor:pointer;color:#8b2050;font-size:14px;font-weight:650}.vr-details .vr-grid{margin-top:18px}.vr-event{margin-top:20px;padding:20px;border-left:4px solid #ae2f67;background:#faf7f9;border-radius:8px}.vr-event h3{margin-bottom:10px}.vr-event p+p{margin-top:8px}.vr-print{padding:11px 16px;border:0;border-radius:9px;background:#ae2f67;color:white;cursor:pointer;font:inherit}
.vr-empty{min-height:135px;display:flex;align-items:center;color:#747079;font-size:14px;line-height:1.6;background:#faf8fa;border-radius:10px;padding:18px}
.vr-goal{display:inline-block;padding:6px 10px;border-radius:7px;font-size:13px;font-weight:650;margin-bottom:13px!important;background:#e9f4ee;color:#246348}.vr-goal-warning{background:#fff2df;color:#86510d}.vr-compromise{margin-top:12px!important;font-size:13px;color:#86510d;line-height:1.6}
@media(max-width:650px){.vr-grid{grid-template-columns:1fr}.vr-hero{padding:21px}.vr h2{font-size:23px}.vr-card{padding:18px}.vr-legend{gap:12px}}
@media print{.vr-print{display:none}.vr-card,.vr-hero{break-inside:avoid}.vr-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.vr{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def _known(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _num(value):
    if not _known(value):
        return "не определено"
    return (
        (f"{value:,.0f}" if value == int(value) else f"{value:,.2f}")
        .replace(",", " ")
        .replace(".", ",")
    )


def _chart(title, unit, values, *, scale_floor=0):
    """One zero-based scale for the two bars; unknown is never drawn as zero."""
    if not any(_known(v) for v in values):
        return '<div class="vr-empty">Нет подтверждённых значений для диаграммы. Неизвестные показатели не заменяются нулями.</div>'
    maximum = max([scale_floor, *(v for v in values if _known(v)), 1])
    parts = [f'<p class="vr-scale">Общая шкала: 0–{_num(maximum)} {unit}</p>']
    label = f"{title}. READY: {_num(values[0])} {unit}. MILP: {_num(values[1])} {unit}."
    parts.append(f'<svg viewBox="0 0 520 135" role="img" aria-label="{escape(label)}">')
    for i, (name, value, color) in enumerate(
        zip(("READY", "MILP"), values, ("#8c929e", "#ae2f67"))
    ):
        y = 15 + i * 63
        parts.append(f'<text x="0" y="{y}" font-size="13" fill="#625b65">{name}</text>')
        parts.append(
            f'<text x="520" y="{y}" text-anchor="end" font-size="16" font-weight="650" fill="#202024">{_num(value)} {unit}</text>'
        )
        parts.append(f'<rect x="0" y="{y + 10}" width="520" height="15" rx="4" fill="#f1edf0"/>')
        if _known(value):
            width = 520 * (max(0, value) / maximum)
            parts.append(
                f'<rect x="0" y="{y + 10}" width="{width:.3f}" height="15" rx="4" fill="{color}"><title>{name}: {_num(value)} {unit}</title></rect>'
            )
    return "".join(parts) + "</svg>"


def _cards(comparison, costs=None):
    a, b = comparison["rows"]["READY"], comparison["rows"]["MILP"]
    fields = [
        ("accepted_t", "Принято топлива", "т", "Больше — лучше", True),
        ("trip_count", "Количество рейсов", "", "Меньше — при равном объёме", False),
        ("last_receipt_h", "Завершение приёмки", "ч", "Раньше — при равном объёме", False),
        ("cost", "Сценарные затраты", "₽", "Дешевле — при равном объёме", False),
    ]
    output = []
    for field, title, unit, hint, bigger in fields:
        if field == "cost":
            values = [
                (costs or {}).get(key, {}).get("total_rub")
                if isinstance((costs or {}).get(key), dict) and row["valid"]
                else None
                for key, row in (("baseline", a), ("planner", b))
            ]
        else:
            values = [a.get(field), b.get(field)]
        known = all(_known(v) for v in values)
        comparable = field == "accepted_t" or comparison["equal_volume"]
        if field == "cost":
            comparable = comparable and bool((costs or {}).get("equal_volume"))
        tone = ""
        if not known:
            badge = "Стоимость не рассчитана" if field == "cost" else "Нет подтверждённых данных"
        elif not comparable:
            badge = "Разный объём — прямого выигрыша не заявляем"
        else:
            change = values[1] - values[0]
            tone = (
                "vr-good" if change and (change > 0) == bigger else "vr-caution" if change else ""
            )
            if change == 0:
                badge = "Без изменения"
            elif field == "last_receipt_h":
                badge = f"На {_num(abs(change))} ч {'раньше' if change < 0 else 'позже'}"
            elif field == "cost":
                badge = f"На {_num(abs(change))} ₽ {'дешевле' if change < 0 else 'дороже'}"
            else:
                badge = f"На {_num(abs(change))}{' ' + unit if unit else ''} {'больше' if change > 0 else 'меньше'}"
        note = (
            "Зависит от введённых ставок и правил оплаты. Фактическая экономия не установлена."
            if field == "cost"
            else "Часы от начала условий, а не длительность работы компьютера."
            if field == "last_receipt_h"
            else hint
        )
        chart = _chart(title, unit, values, scale_floor=comparison["target_t"] if bigger else 0)
        output.append(
            f'<article class="vr-card"><h3>{title}</h3>{chart}<p class="vr-delta {tone}">{badge}</p><p class="vr-foot">{note}</p></article>'
        )
    return '<div class="vr-grid">' + "".join(output) + "</div>"


def _headline(c, strategy, costs):
    a, b = c["rows"]["READY"], c["rows"]["MILP"]
    if not b["valid"]:
        return "Сначала требуется подтвердить допустимый план"
    if not b["target_met"]:
        return f"Цель пока не выполнена: недопоставка {_num(b['shortfall_t'])} т"
    if not a["valid"]:
        return "MILP выполнил задание; план READY не подтверждён"
    if not c["equal_volume"]:
        return "Объёмы различаются: сначала сравните выполнение задания"
    if strategy == "cost" and costs and costs.get("equal_volume"):
        delta = costs.get("scenario_difference_rub")
        if _known(delta) and delta > 0:
            return f"Сценарные затраты ниже на {_num(delta)} ₽"
    d = c["differences"]
    trips, hours = d.get("trip_count"), d.get("last_receipt_h")
    if _known(trips) and trips > 0 and _known(hours) and hours > 0:
        return "Меньше рейсов, приёмка завершена раньше"
    if _known(trips) and trips > 0:
        return "Для того же объёма найдено меньше рейсов"
    if _known(hours) and hours > 0:
        return "Тот же объём принят раньше"
    if trips == 0 and hours == 0:
        return "Основные показатели планов совпали"
    return "Сравните найденные планы по выбранному приоритету"


def visual_summary(result, *, include_style=True):
    groups = comparisons(result)
    c = groups.get("После события", groups.get("Расчёт"))
    costs = result.get("costs")
    strategy = result.get("planning_strategy", "trips")
    stage = (
        "После события · READY и MILP"
        if "initial" in result
        else "READY и MILP · одинаковые условия"
    )
    headline = _headline(c, strategy, costs)
    priority = "сценарные затраты" if strategy == "cost" else "число рейсов"
    plan = c["rows"]["MILP"]
    goal = (
        f"Задание выполнено · принято {_num(plan['accepted_t'])} из {_num(c['target_t'])} т"
        if plan["target_met"]
        else f"Задание пока не выполнено · недопоставка {_num(plan['shortfall_t'])} т"
        if plan["valid"]
        else "Результат требует проверки · допустимость не подтверждена"
    )
    goal_class = "" if plan["target_met"] else " vr-goal-warning"
    notice = []
    for key, label in (("last_receipt_h", "Приёмка"), ("trip_count", "Рейсов")):
        delta = c["differences"].get(key)
        if _known(delta) and delta < 0:
            notice.append(
                f"{label} на {_num(-delta)} {'ч позже' if key == 'last_receipt_h' else 'больше'}"
            )
    compromise = (
        f'<p class="vr-compromise">Учтите: {escape("; ".join(notice))}. Проверьте, приемлем ли этот вариант для задачи.</p>'
        if notice
        else ""
    )
    parts = [f"<style>{VISUAL_STYLE}</style>" if include_style else ""]
    parts.append(
        f'<section class="vr" data-visual-summary="1"><header class="vr-hero"><p class="vr-kicker">Результат за минуту · {stage}</p><p class="vr-goal{goal_class}">{escape(goal)}</p><h2>{escape(headline)}</h2><p class="vr-note">{escape(c["conclusion"])}</p>{compromise}<p class="vr-foot">Задание: {_num(c["target_t"])} т · Приёмка до {_num(result["deadline_h"])} ч.</p><p class="vr-foot">Приоритет: объём → {priority} → ранняя последняя приёмка. {escape(c["search_status"])}.</p></header>'
    )
    parts.append(
        '<div class="vr-legend"><span><i class="vr-dot"></i>READY — последовательный план</span><span><i class="vr-dot vr-dot-milp"></i>MILP — математический планировщик</span></div>'
    )
    parts.append(_cards(c, costs))
    for warning in c["warnings"]:
        parts.append(f'<p class="vr-delta vr-caution">{escape(warning)}</p>')
    if "initial" in result:
        initial = result["initial"]
        before, after = initial["planner"], result["revised_planner"]["result"]
        parts.append(
            '<aside class="vr-event"><h3>Как событие повлияло на план MILP</h3><p class="vr-note">Это изменение условий одного метода; оно отличается от сравнения READY и MILP выше.</p>'
        )
        for key, label, unit in (
            ("accepted_t", "Принято", "т"),
            ("last_receipt_h", "Последняя приёмка", "ч"),
        ):
            x = before.get(key) if before.get("status") == "valid_simulation" else None
            y = after.get(key) if after.get("status") == "valid_simulation" else None
            parts.append(f"<p>{label}: {_num(x)} {unit} → {_num(y)} {unit}</p>")
        parts.append(
            '</aside><details class="vr-details"><summary>Сравнение методов до события</summary>'
        )
        parts.append(_cards(groups["До события"], initial.get("costs")))
        parts.append("</details>")
    return "".join(parts) + "</section>"


def summary_page(result, title):
    from .presentation import event_card, schedule_sections

    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)} — наглядный отчёт</title><style>{VISUAL_STYLE}"
        'body{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f7f5f6;color:#202024;max-width:1100px;margin:30px auto;padding:0 20px}h1{font-size:30px;line-height:1.2}.timeline>svg{width:100%}.schedule-panel{background:#fff;border-radius:14px;padding:20px;margin-bottom:20px}.timeline p{font-size:12px}.timeline span svg{width:12px;height:12px}.schedule-grid{display:grid;grid-template-columns:1fr;gap:16px}@media print{body{background:white;margin:0;padding:0}}</style>'
        f'<h1>{escape(title)}</h1><button class="vr-print" onclick="window.print()">Печать / сохранить PDF</button>'
        + visual_summary(result, include_style=False)
        + event_card(result)
        + "<h2>Предложенное расписание</h2>"
        + schedule_sections(result)
        + "<p>Расчёт относится к заданным условиям. Реальная выполнимость и фактическая экономия требуют подтверждения. READY/MILP сравниваются внутри одного состояния условий.</p></html>"
    )
