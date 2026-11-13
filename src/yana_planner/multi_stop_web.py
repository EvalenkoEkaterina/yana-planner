"""Multi-stop JSON API and read-only saved views, independent of legacy report shape."""

import hashlib
import json
import sqlite3
from html import escape

from flask import Blueprint, abort, current_app, jsonify, request, url_for

from . import multi_stop_storage as store
from .domain import InputError
from .web_worker import CalculationTimeout, calculate_multi_stop_with_timeout

bp = Blueprint("multi_stop", __name__)
LABELS = {
    "goal_met": "Обязательные поставки выполнены",
    "infeasible": "Условия невыполнимы в модели",
    "no_feasible_solution_found": "Допустимый план пока не найден",
}


POINT_NAMES = {
    "nizhneyansk": "Нижнеянск",
    "kazachye": "Казачье",
    "ust-kuyga": "Усть-Куйга",
    "saydy-verkhoyansky": "Сайды",
    "batagay": "Батагай",
}


def _name(ident):
    return POINT_NAMES.get(ident, ident)


def _links(ident):
    return dict(
        page=url_for("multi_stop.page", ident=ident),
        json=url_for("multi_stop.download", ident=ident),
        api=url_for("multi_stop.get_run", ident=ident),
        history=url_for("multi_stop.history_page"),
    )


def _run(ident):
    row = store.load(current_app.config["DATABASE"], ident)
    if row is None:
        abort(404)
    row["links"] = _links(ident)
    return row


@bp.post("/api/multi-stop/plan")
def calculate():
    payload = request.get_json(silent=True)
    try:
        json.dumps(payload, allow_nan=False)
        submitted = payload
    except (TypeError, ValueError):
        submitted = None
    try:
        inputs, result = calculate_multi_stop_with_timeout(
            payload, total_timeout=current_app.config["CALCULATION_TIMEOUT_S"]
        )
        inputs["settings"]["total_web_timeout_s"] = current_app.config["CALCULATION_TIMEOUT_S"]
        result["input_sha256"] = hashlib.sha256(
            json.dumps(inputs, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        ).hexdigest()
        ident = store.save(current_app.config["DATABASE"], inputs, result)
        return jsonify(id=ident, inputs=inputs, result=result, links=_links(ident)), 201
    except CalculationTimeout as exc:
        return jsonify(
            code="calculation_timeout", error=str(exc), result=None, submitted_request=submitted
        ), 408
    except InputError as exc:
        return jsonify(
            code="invalid_input", error=str(exc), result=None, submitted_request=submitted
        ), 400
    except (OSError, sqlite3.Error):
        current_app.logger.exception("Multi-stop calculation could not be saved")
        return jsonify(
            code="server_error",
            error="Не удалось выполнить или сохранить расчёт",
            result=None,
            submitted_request=submitted,
        ), 500


@bp.get("/api/multi-stop/runs")
def history():
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        return jsonify(code="invalid_input", error="limit: целое число от 1 до 100"), 400
    if not 1 <= limit <= 100:
        return jsonify(code="invalid_input", error="limit: целое число от 1 до 100"), 400
    rows = store.history(current_app.config["DATABASE"], limit)
    for row in rows:
        row["links"] = _links(row["id"])
    return jsonify(items=rows)


@bp.get("/api/multi-stop/runs/<int:ident>")
def get_run(ident):
    return jsonify(_run(ident))


@bp.get("/multi-stop/runs/<int:ident>/results.json")
def download(ident):
    response = jsonify(_run(ident))
    response.headers["Content-Disposition"] = f'attachment; filename="multi-stop-{ident}.json"'
    return response


def _document(title, body):
    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>body{{font:16px/1.6 system-ui;max-width:1100px;margin:30px auto;padding:0 20px;color:#202024}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:10px;text-align:left}}.scroll{{overflow-x:auto}}a{{color:#9a2864}}</style>"
        f"<h1>{escape(title)}</h1>" + body + "</html>"
    )


@bp.get("/multi-stop/history")
def history_page():
    rows = store.history(current_app.config["DATABASE"])
    items = "".join(
        f'<li><a href="{escape(_links(r["id"])["page"], quote=True)}">#{r["id"]} · {escape(_name(r["origin_id"]))} → {escape(" → ".join(_name(i) for i in r["stop_ids"]))}</a> · {escape(LABELS.get(r["status"], r["status"]))} · {escape(r["created_at"])}</li>'
        for r in rows
    )
    return _document(
        "История многопунктовых расчётов",
        '<p><a href="/">Главная</a></p><ul>' + items + "</ul>"
        if rows
        else '<p>Расчётов пока нет. <a href="/">Главная</a></p>',
    )


@bp.get("/multi-stop/runs/<int:ident>")
def page(ident):
    row = _run(ident)
    r = row["result"]
    cfg = row["inputs"]["scenario"]
    plan = r["plan"]
    body = f'<p><a href="{escape(row["links"]["history"], quote=True)}">История</a> · <a href="{escape(row["links"]["json"], quote=True)}">Скачать условия и результат JSON</a></p>'
    body += "<p>Учебные условия. Реальная выполнимость и экономия требуют подтверждения.</p>"
    body += f"<h2>{escape(LABELS.get(r['status'], r['status']))}</h2>"
    body += (
        "<p>Порядок посещения: "
        + escape(" → ".join(_name(i) for i in r["visit_order"]))
        + ". Порядок важности: "
        + escape(" → ".join(_name(i) for i in r["receipt_priority"]))
        + ".</p>"
    )
    body += (
        "<p>Оптимальность всех этапов: "
        + ("подтверждена" if r.get("lexicographic_optimal") else "не подтверждена")
        + ".</p>"
    )
    if plan:
        deadlines = {s["id"]: s["deadline_h"] for s in cfg["stops"]}
        body += '<div class="scroll"><table><tr><th>Получатель</th><th>Принято, т</th><th>Срок, ч</th><th>Приёмка, ч</th><th>Остаток на борту, т</th></tr>'
        for d in plan["deliveries"]:
            body += (
                "<tr>"
                + "".join(
                    f"<td>{escape(str(v))}</td>"
                    for v in [
                        _name(d["stop_id"]),
                        d["accepted_t"],
                        deadlines[d["stop_id"]],
                        d["receipt_h"],
                        d["cargo_after_t"],
                    ]
                )
                + "</tr>"
            )
        body += f"</table></div><p>Полная готовность после возврата: {plan['ready_h']} ч.</p>"
    else:
        body += "<p>Успешная поставка не подтверждена; частичные объёмы не выданы за выполнение заказа.</p>"
    costs = r.get("costs")
    if costs:
        body += "<h2>Сценарные расходы</h2>"
        if costs.get("total_rub") is None:
            body += "<p>Полная стоимость не определена.</p>"
            if costs.get("missing"):
                body += (
                    "<p>Не заданы: "
                    + escape(
                        ", ".join(
                            ("Сбор за заход: " + _name(i.split(".", 1)[1]))
                            if i.startswith("stop_fees_rub.")
                            else {
                                "rates.separate_trip_operations_rub": "Ставка за рейс",
                                "rates.active_vessel_hour_rub": "Ставка активности",
                                "rates.waiting_vessel_hour_rub": "Ставка ожидания",
                            }.get(i, i)
                            for i in costs["missing"]
                        )
                    )
                    + ".</p>"
                )
            if costs.get("known_subtotal_rub") is not None:
                body += f"<p>Известная часть: {costs['known_subtotal_rub']} ₽.</p>"
        else:
            body += f"<p>Итого: {costs['total_rub']} ₽.</p>"
        body += "<p>Стоимость оценивается после планирования; минимум расходов не заявляется.</p>"
    return _document(f"Многопунктовый расчёт #{ident}", body)


@bp.errorhandler(404)
def not_found(error):
    return jsonify(code="not_found", error="Многопунктовый расчёт не найден"), 404
