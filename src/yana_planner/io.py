"""Portable JSON/CSV/HTML outputs. Never embeds host filesystem paths in reports."""

from __future__ import annotations

import csv
import html
import json
import math
from pathlib import Path

from .domain import InputError, proposals


def read_json(path: str | Path):
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8-sig"),
            parse_constant=lambda x: (_ for _ in ()).throw(InputError(f"Недопустимое число {x}")),
        )
    except (OSError, json.JSONDecodeError) as e:
        raise InputError(f"Не удалось прочитать JSON: {e}") from e


def _finite(value):
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: str | Path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(_finite(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def read_plan(path: str | Path, method: str = "planner") -> list[dict]:
    value = read_json(path)
    if isinstance(value, list):
        return proposals(value)
    if not isinstance(value, dict):
        raise InputError("Файл расписания должен содержать список или объект результата")
    if "trips" in value:
        return proposals(value["trips"])
    if isinstance(value.get(method), dict) and "trips" in value[method]:
        return proposals(value[method]["trips"])
    raise InputError("В файле не найдено расписание")


def _rows(result: dict):
    if "revised_baseline" in result:
        return [
            ("Первоначально, READY", result["initial"]["baseline"]),
            ("Первоначально, MILP", result["initial"]["planner"]),
            ("После события, READY", result["revised_baseline"]["result"]),
            ("После события, MILP", result["revised_planner"]["result"]),
        ]
    return [("READY", result["baseline"]), ("MILP", result["planner"])]


def _event_notice(result: dict) -> str:
    event = result.get("event")
    if not isinstance(event, dict):
        return ""

    def safe(value):
        return html.escape(str(value))

    at = safe(event.get("announced_at_h", "не определено"))
    if event.get("type") == "vessel_unavailable":
        detail = (
            f"Судно <strong>{safe(event.get('vessel_id'))}</strong> недоступно "
            f"с <strong>{safe(event.get('from_h'))}-го</strong> до "
            f"<strong>{safe(event.get('to_h'))}-го часа</strong>. "
            "Полный оборот нового рейса не должен пересекать этот интервал."
        )
    elif event.get("type") == "arrival_delay":
        new = safe(event.get("new_arrival_h"))
        original = result.get("initial_input", {}).get("arrivals", [])
        index = event.get("arrival_index")
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(original):
            arrival = original[index]
            detail = (
                f"Партия <strong>{safe(arrival['t'])} т</strong>: поступление топлива "
                f"в Нижнеянск перенесено <strong>{safe(arrival['h'])} ч → {new} ч</strong>."
            )
        else:
            # Older result files do not retain original input; do not infer an
            # arrival time from the departure schedule or a hardcoded example.
            detail = f"Новый момент поступления топлива в Нижнеянск — <strong>{new} ч</strong>."
    else:
        detail = f"Тип сообщения: {safe(event.get('type', 'не определено'))}."
    return (
        '<section class="event" id="event-change"><h2>Что изменилось</h2>'
        f"<p>На <strong>{at}-м часу</strong> получено сообщение.</p><p>{detail}</p>"
        "<p>Начатые рейсы каждого метода сохраняются. Будущие рейсы перестраиваются. "
        "Если новое условие конфликтует с начатым рейсом, требуется вмешательство. "
        "Все времена — часы от общего начала сценария T0.</p></section>"
    )


def export_result(directory: str | Path, result: dict):
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "results.json", result)
    rows = _rows(result)
    metrics = []
    trips = []
    ledgers = []
    for label, value in rows:
        metrics.append(
            dict(
                method=label,
                status=value["status"],
                accepted_t=value.get("accepted_t"),
                shortfall_t=value.get("shortfall_t"),
                trips=value.get("trip_count"),
                last_receipt_h=value.get("last_receipt_h"),
                busy_vessel_h=value.get("busy_vessel_h"),
                weighted_mean_receipt_h=value.get("weighted_mean_receipt_h"),
            )
        )
        trips.extend(dict(method=label, **t) for t in value.get("trips", []))
        ledgers.extend(dict(method=label, **t) for t in value.get("ledger", []))
    for name, data in [
        ("comparison.csv", metrics),
        ("schedule.csv", trips),
        ("events.csv", ledgers),
    ]:
        with (out / name).open("w", encoding="utf-8-sig", newline="") as f:
            if data:
                writer = csv.DictWriter(f, fieldnames=list(data[0]))
                writer.writeheader()
                writer.writerows(data)

    def cell(x):
        if x is None:
            return "не определено"
        return f"{x:.2f}" if isinstance(x, float) else str(x)

    headings = [
        "Метод",
        "Статус",
        "Принято, т",
        "Недопоставка, т",
        "Рейсы",
        "Последняя приёмка, ч",
        "Занятость, судно·ч",
        "Средняя приёмка, ч",
    ]
    table = "<tr>" + "".join(f"<th>{h}</th>" for h in headings) + "</tr>"
    for row in metrics:
        table += (
            "<tr>" + "".join(f"<td>{html.escape(cell(v))}</td>" for v in row.values()) + "</tr>"
        )
    schedules = ""
    for label, value in rows:
        schedules += (
            f"<h2>{html.escape(label)}</h2><p>{html.escape('; '.join(value.get('errors', [])))}</p>"
        )
        if value["status"] != "valid_simulation":
            continue
        schedules += "<table><tr><th>Судно</th><th>Тонны</th><th>Погрузка с</th><th>Прибытие</th><th>Принято</th><th>Возврат</th><th>Готовность</th></tr>"
        for trip in value["trips"]:
            schedules += (
                "<tr>"
                + "".join(
                    f"<td>{html.escape(str(trip[k]))}</td>"
                    for k in [
                        "vessel_id",
                        "quantity_t",
                        "start_h",
                        "arrival_h",
                        "receipt_h",
                        "return_h",
                        "ready_h",
                    ]
                )
                + "</tr>"
            )
        schedules += "</table>"
    event = _event_notice(result)
    page = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Яна: расчёт</title>
<style>body{{font:16px system-ui;max-width:1200px;margin:32px auto;padding:0 20px;color:#17354a;background:#f5f7fb}}table{{width:100%;border-collapse:collapse;background:white;overflow:auto;display:block}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #dde4ec}}th{{background:#274b69;color:white}}h1{{font-size:28px}}h2{{margin-top:32px;font-size:21px}}p{{line-height:1.6}}a{{color:#167764}}.event{{background:#e8f3ff;border-left:5px solid #2374b8;padding:16px 20px;margin:20px 0;border-radius:8px}}.event h2{{margin-top:0}}</style>
<h1>Доставка НКТ: {html.escape(result["scenario_id"])}</h1><p>Сценарный расчёт. Срок приёмки: {result["deadline_h"]} ч от T0. Реальная допустимость требует подтверждения. Экономия предприятия не установлена.</p>{event}<table>{table}</table>
<p>Средняя приёмка взвешена по тоннам. Цели: сначала больше принято, затем меньше рейсов, затем раньше последняя приёмка. Числа разных объёмов не означают одинаковый уровень выполнения задания.</p>
<p><a href="results.json">Полные результаты и статусы решателя</a> · <a href="comparison.csv">Сравнение</a> · <a href="schedule.csv">Расписание</a> · <a href="events.csv">Баланс событий</a></p>{schedules}</html>"""
    (out / "report.html").write_text(page, encoding="utf-8")


def export_failure(directory: str | Path, failure: dict):
    """Do not leave a previous successful result looking like the failed new run."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "results.json", failure)
    for name, header in [
        ("comparison.csv", "status,accepted_t"),
        ("schedule.csv", "method,vessel_id,start_h,quantity_t"),
        ("events.csv", "method,h,event,quantity_t"),
    ]:
        (out / name).write_text(header + "\n", encoding="utf-8-sig")
    message = html.escape(failure["message"])
    (out / "report.html").write_text(
        f'<!doctype html><html lang="ru"><meta charset="utf-8"><title>Расчёт не выполнен</title><h1>Расчёт не выполнен</h1><p>{message}</p><p>Подтверждённый доставленный объём не определён.</p></html>',
        encoding="utf-8",
    )
