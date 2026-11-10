"""Portable JSON/CSV/HTML outputs. Never embeds host filesystem paths in reports."""

from __future__ import annotations

import csv
import html
import json
import math
import re
from pathlib import Path

from .domain import InputError, proposals

PLAIN_REPORT_STYLE = """body{font:14px Arial,sans-serif;line-height:1.4;max-width:1200px;margin:24px auto;padding:0 16px;color:#000;background:#fff}h1{font-size:18px}h2{font-size:16px;margin-top:20px}table{border-collapse:collapse;width:100%;margin:12px 0;background:#fff}th,td{border:1px solid #000;padding:6px;text-align:left;font-weight:normal}th{font-weight:bold}a,a:visited{color:#000;text-decoration:none}strong,b{font-weight:normal}.event{margin:16px 0;padding:0;border:0;background:none}.table{overflow:auto}img{max-width:100%}"""


def _plain_report_markup(page: str) -> str:
    """Apply plain presentation without changing values or link addresses."""
    page = re.sub(r"<style\b[^>]*>.*?</style>", "", page, flags=re.I | re.S)
    page = re.sub(r'\sstyle=("[^"]*"|\'[^\']*\')', "", page, flags=re.I)
    page = re.sub(
        r"(?<=>)[^<]+(?=<)",
        lambda match: match[0].replace("—", "-").replace("–", "-"),
        page,
    )
    return page.replace("</title>", f"</title><style>{PLAIN_REPORT_STYLE}</style>", 1)


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
    event = (
        f"<p>Новая информация: {html.escape(json.dumps(result['event'], ensure_ascii=False))}</p>"
        if "event" in result
        else ""
    )
    page = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Яна: расчёт</title>
<style>{PLAIN_REPORT_STYLE}</style>
<h1>Доставка НКТ: {html.escape(result["scenario_id"])}</h1><p>Сценарный расчёт. Срок приёмки: {result["deadline_h"]} ч от T0. Реальная допустимость требует подтверждения. Экономия предприятия не установлена.</p>{event}<table>{table}</table>
<p>Средняя приёмка взвешена по тоннам. Цели: сначала больше принято, затем меньше рейсов, затем раньше последняя приёмка. Числа разных объёмов не означают одинаковый уровень выполнения задания.</p>
<p><a href="results.json">Полные результаты и статусы решателя</a> · <a href="comparison.csv">Сравнение</a> · <a href="schedule.csv">Расписание</a> · <a href="events.csv">Баланс событий</a></p>{schedules}</html>"""
    (out / "report.html").write_text(_plain_report_markup(page), encoding="utf-8")


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
        _plain_report_markup(
            f'<!doctype html><html lang="ru"><meta charset="utf-8"><title>Расчёт не выполнен</title><h1>Расчёт не выполнен</h1><p>{message}</p><p>Подтверждённый доставленный объём не определён.</p></html>'
        ),
        encoding="utf-8",
    )
