"""Display saved schedules only: no planning or scenario-file reads."""

from html import escape
from math import ceil

from .experiments import event_effects

STATUS = {
    "completed": "Расчёт завершён",
    "invalid_schedule": "Расписание не прошло проверку",
    "valid_simulation": "Допустимое учебное расписание",
    "requires_intervention": "Требуется вмешательство",
    "no_feasible_solution_found": "Допустимое решение не найдено",
    "infeasible": "Задача недопустима",
}


def event_card(result, cfg=None):
    """Explain a saved event without inventing weather observations or forecasts."""
    event = result.get("event")
    if not event:
        return ""
    if event.get("type") == "weather_closure":
        intervals = (
            "; ".join(f"[{a}, {b}) ч" for a, b in event.get("closures", [])) or "новых запретов нет"
        )
        kinds = {
            "synthetic": "Синтетический пример",
            "reanalysis": "Исторический реанализ",
            "forecast": "Прогноз",
        }
        kind = kinds.get(event.get("data_kind"), "Тип данных не указан")
        note = (
            "Исторический реанализ используется для условного испытания расписания. Это не проверка качества прогноза и не подтверждение доступности будущей погоды диспетчеру."
            if event.get("data_kind") == "reanalysis"
            else "Интервалы заданы для проверки формата и алгоритма; реальные погодные события не подтверждены."
            if event.get("data_kind") == "synthetic"
            else "Проверено время выпуска прогноза относительно момента объявления. Порог погодного правила остаётся сценарным."
        )
        return (
            "<aside class=event-card><h2>Погодный запрет движения</h2><p>"
            + escape(intervals)
            + f". Условное сообщение с {event['announced_at_h']} ч.</p><p>"
            + escape(kind)
            + ". Запрет действует на движение с грузом и возвращение. "
            + "Начало интервала включено, конец не включён.</p><p>"
            + escape(note)
            + "</p><p>Правило воздействия сценарное. Начатые рейсы сохраняются; "
            + "если новый запрет им противоречит, требуется вмешательство диспетчера.</p></aside>"
        )
    # Replanning keeps an immutable original input in each snapshot.
    if cfg is None:
        cfg = result.get("original_input", {})
    arrivals = cfg.get("arrivals", [])
    index = event["arrival_index"]
    arrival = arrivals[index] if index < len(arrivals) else None
    original = (
        f"{arrival['t']:g} т: ожидались в {arrival['h']:g} ч; "
        if arrival
        else "Поступление топлива: "
    )
    return (
        "<aside class=event-card><h2>Задержка поступления</h2><p>"
        + escape(original)
        + f"поступят в {event['new_arrival_h']} ч. Известно с {event['announced_at_h']} ч.</p>"
        + "<p>Начатые рейсы каждого метода зафиксированы; меняются только будущие отправления.</p></aside>"
    )


def timeline(verdict, deadline, committed=(), announcement=None, *, horizon=None):
    """Draw per-vessel operation bars using checked trip times, in hours."""
    if verdict.get("status") != "valid_simulation":
        return "<p>Диаграмма недоступна: расписание не подтверждено.</p>"
    trips = verdict.get("trips", [])
    if not trips:
        return "<p>Рейсов нет.</p>"
    vessels = sorted({t["vessel_id"] for t in trips})
    horizon = horizon or max(deadline, *(t["ready_h"] for t in trips), 1)

    def x(h):
        return 130 + 820 * h / horizon

    locked = {(t["vessel_id"], t["start_h"]) for t in committed}
    phases = [
        ("start_h", "load_end_h", "Погрузка", "#d88c22"),
        ("load_end_h", "arrival_h", "Перевозка", "#087e83"),
        ("arrival_h", "receipt_h", "Приёмка", "#7058a3"),
        ("receipt_h", "return_h", "Возврат", "#537aab"),
        ("return_h", "ready_h", "Подготовка", "#b0bbc4"),
    ]
    height = 115 + 65 * len(vessels)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {height}" role="img" aria-label="Расписание по судам; время в часах от начала сценария"><rect width="1000" height="{height}" fill="white"/>'
    ]
    for i in range(6):
        h = horizon * i / 5
        svg.append(
            f'<path d="M{x(h)} 35 V{height - 35}" stroke="#dde5ea"/><text x="{x(h)}" y="22" font-size="12" fill="#183044">{h:g}</text>'
        )
    for i, vessel in enumerate(vessels):
        y = 60 + i * 65
        svg.append(
            f'<text x="5" y="{y + 20}" font-size="14" fill="#183044">{escape(vessel)}</text>'
        )
        for number, t in enumerate(trips, 1):
            if t["vessel_id"] != vessel:
                continue
            svg.append(
                f'<text x="{x(t["start_h"])}" y="{y - 7}" font-size="14" fill="#183044">№{number} · {t["quantity_t"]:g} т</text>'
            )
            for a, b, label, color in phases:
                title = f"{vessel}: {label}, {t[a]:g}–{t[b]:g} ч; {t['quantity_t']:g} т"
                svg.append(
                    f'<rect x="{x(t[a])}" y="{y}" width="{max(0, x(t[b]) - x(t[a]))}" height="26" fill="{color}"><title>{escape(title)}</title></rect>'
                )
            if (vessel, t["start_h"]) in locked:
                svg.append(
                    f'<rect x="{x(t["start_h"])}" y="{y - 3}" width="{x(t["ready_h"]) - x(t["start_h"])}" height="32" fill="none" stroke="#111" stroke-width="2"><title>Начатый рейс — изменять нельзя</title></rect>'
                )
    for h, label, color in [
        (deadline, "Срок приёмки", "#be4747"),
        (announcement, "Событие", "#111"),
    ]:
        if h is not None:
            svg.append(
                f'<path d="M{x(h)} 32 V{height - 32}" stroke="{color}" stroke-dasharray="4 3"/><text x="{x(h) - 5}" y="{height - 15}" text-anchor="end" font-size="12" fill="{color}">{label}: {h} ч</text>'
            )
    svg.append("</svg>")
    legend = " · ".join(
        f'<span><svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" aria-hidden="true"><rect width="12" height="12" fill="{c}"/></svg> {label}</span>'
        for _, _, label, c in phases
    )
    return (
        '<div class="timeline">'
        + "".join(svg)
        + "<p>"
        + legend
        + "</p><p>Часы от начала сценария. Чёрная рамка — начатый неизменяемый рейс. Наведите на участок для точных значений.</p></div>"
    )


def schedule_sections(result):
    """Use one diagram renderer for the website and portable HTML report."""
    if "initial" in result:
        rows = [
            ("До события — READY", result["initial"]["baseline"], ()),
            ("До события — MILP", result["initial"]["planner"], ()),
        ]
        rows += [
            (label, result[key]["result"], result[key]["committed"])
            for label, key in [
                ("После события — READY", "revised_baseline"),
                ("После события — MILP", "revised_planner"),
            ]
        ]
    else:
        rows = [("READY", result["baseline"], ()), ("MILP", result["planner"], ())]
    horizon = (
        ceil(
            max(
                result["deadline_h"],
                *(t["ready_h"] for _, v, _ in rows for t in v.get("trips", [])),
                1,
            )
            / 60
        )
        * 60
    )
    return (
        '<div class="schedule-grid">'
        + "".join(
            '<article class="schedule-panel"><h3>'
            + label
            + "</h3>"
            + timeline(
                v,
                result["deadline_h"],
                fixed,
                result.get("event", {}).get("announced_at_h"),
                horizon=horizon,
            )
            + "</article>"
            for label, v, fixed in rows
        )
        + "</div>"
    )


def effects_html(result):
    """Render one shared before/after block for web and portable reports."""
    effects = event_effects(result)
    if not effects:
        return ""
    labels = {
        "accepted_t": "Принято, т",
        "trip_count": "Рейсов",
        "last_receipt_h": "Завершение приёмки, ч",
        "weighted_mean_receipt_h": "Средняя приёмка по тоннам, ч",
    }
    page = "<section><h2>Что изменилось после задержки</h2><p>Изменение = после − до. Это сравнение планов после сообщения о задержке, а не оценка реального ущерба. Плюс у времени означает более позднюю приёмку.</p>"

    def number(value, signed=False):
        if value is None:
            return "не определено"
        return f"{value:+.2f}" if signed else f"{value:.2f}"

    for name, item in effects.items():
        page += f"<h3>{name}</h3><div class=table-scroll><table><tr><th>Показатель</th><th>До</th><th>После</th><th>Изменение</th></tr>"
        for field, values in item["metrics"].items():
            page += f"<tr><th>{labels[field]}</th><td>{number(values['before'])}</td><td>{number(values['after'])}</td><td>{number(values['change'], True)}</td></tr>"
        page += "</table></div>"
        if item["warning"]:
            page += '<p class="warning">' + escape(item["warning"]) + "</p>"
    return page + "</section>"
