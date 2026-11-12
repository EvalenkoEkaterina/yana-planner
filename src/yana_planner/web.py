"""Russian Flask demo with persistent, refresh-safe results."""

import os
import sqlite3

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from . import storage
from .domain import InputError
from .experiments import comparisons
from .presentation import STATUS, effects_html, event_card, schedule_sections
from .service import NAMES, ROOT, VESSEL_FIELDS, form_defaults
from .web_worker import CalculationTimeout, calculate_request_with_timeout
from .web_worker import calculate_with_timeout as calculate


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("YANA_DATABASE", str(ROOT / "instance/runs.sqlite3")),
        MAX_DEADLINE_H=492,
        SOLVER_TIME_LIMIT=2,
        CALCULATION_TIMEOUT_S=30,
        MAX_CONTENT_LENGTH=1_048_576,
    )
    app.config.update(config or {})
    app.jinja_env.filters["run_status"] = lambda value: STATUS.get(value, "Статус не определён")

    @app.route("/", methods=["GET", "POST"])
    def index():
        error = None
        timed_out = False
        values = form_defaults()
        preset_name = None
        if request.method == "GET" and "preset" in request.args:
            try:
                preset = request.args["preset"]
                values = form_defaults(preset)
                preset_name = NAMES[preset]
            except InputError as exc:
                error = str(exc)
        if request.method == "POST":
            values = request.form.to_dict(flat=False)
            for field in ("announced_at_h", "delayed_arrival", "new_arrival_h"):
                values.setdefault(field, form_defaults()[field])
            try:
                key = request.form.get("scenario", "")
                search_seconds = request.form.get("search_seconds")
                if search_seconds is not None and search_seconds not in {"auto", "2", "10", "30"}:
                    raise InputError("Время поиска: выберите автоматический режим, 2, 10 или 30 с")
                inputs, result = calculate(
                    key,
                    request.form.get("deadline", ""),
                    max_deadline=app.config["MAX_DEADLINE_H"],
                    time_limit=(
                        (30 if search_seconds == "auto" else int(search_seconds))
                        if search_seconds is not None
                        else app.config["SOLVER_TIME_LIMIT"]
                    ),
                    custom=values if request.form.get("customize") == "1" else None,
                    adaptive=search_seconds == "auto",
                    total_timeout=app.config["CALCULATION_TIMEOUT_S"],
                )
                inputs["settings"]["total_web_timeout_s"] = app.config["CALCULATION_TIMEOUT_S"]
                result["report_html"] = result["report_html"].replace(
                    "<h1>",
                    render_template("conditions.html", inputs=inputs, search_result=result)
                    + "<h1>",
                    1,
                )
                ident = storage.save(
                    app.config["DATABASE"],
                    (
                        "Свои условия" + (" — задержка топлива" if key == "delay" else "")
                        if inputs["scenario"]["id"] == "CUSTOM"
                        else NAMES[key]
                    ),
                    inputs["deadline_h"],
                    inputs,
                    result,
                    result.get("code_version", "unknown"),
                )
                return redirect(url_for("result_page", ident=ident), code=303)
            except CalculationTimeout as exc:
                error = str(exc)
                timed_out = True
            except (InputError, OSError, sqlite3.Error) as exc:
                error = str(exc)
        return render_template(
            "index.html",
            names=NAMES,
            error=error,
            maximum=app.config["MAX_DEADLINE_H"],
            values=values,
            vessel_fields=VESSEL_FIELDS,
            total_timeout=app.config["CALCULATION_TIMEOUT_S"],
            preset_name=preset_name,
        ), 408 if timed_out else 400 if error else 200

    def get_run(ident):
        run = storage.load(app.config["DATABASE"], ident)
        if run is None:
            abort(404)
        return run

    @app.post("/api/plan")
    def api_plan():
        """Shared core for a new frontend; saved runs use the existing report pages."""
        try:
            inputs, result = calculate_request_with_timeout(
                request.get_json(silent=True),
                max_deadline=app.config["MAX_DEADLINE_H"],
                total_timeout=app.config["CALCULATION_TIMEOUT_S"],
            )
            inputs["settings"]["total_web_timeout_s"] = app.config["CALCULATION_TIMEOUT_S"]
            ident = storage.save(
                app.config["DATABASE"],
                inputs["scenario"].get("label", inputs["scenario"]["id"]),
                inputs["deadline_h"],
                inputs,
                result,
                result.get("code_version", "unknown"),
            )
            return jsonify(
                id=ident,
                inputs=inputs,
                result=result,
                links=dict(
                    page=url_for("result_page", ident=ident),
                    report=url_for("report", ident=ident),
                    json=url_for("download", ident=ident),
                ),
            ), 201
        except CalculationTimeout as exc:
            return jsonify(error=str(exc), code="calculation_timeout", result=None), 408
        except InputError as exc:
            return jsonify(error=str(exc), code="invalid_input", result=None), 400
        except (OSError, sqlite3.Error):
            app.logger.exception("API calculation or persistence failed")
            return jsonify(
                error="Не удалось выполнить или сохранить расчёт", code="server_error"
            ), 500

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(
            error="Размер запроса не должен превышать 1 МиБ", code="request_too_large"
        ), 413

    @app.get("/runs/<int:ident>")
    def result_page(ident):
        run = get_run(ident)
        return render_template(
            "result.html",
            run=run,
            comparison=comparisons(run["result"]),
            event_html=event_card(run["result"], run["inputs"]["scenario"]),
            diagrams=schedule_sections(run["result"]),
            effects=effects_html(run["result"]),
        )

    @app.get("/runs/<int:ident>/results.json")
    def download(ident):
        response = jsonify(get_run(ident))
        response.headers["Content-Disposition"] = 'attachment; filename="results.json"'
        return response

    @app.get("/runs/<int:ident>/report.html")
    def report(ident):
        return get_run(ident)["result"]["report_html"]

    @app.get("/history")
    def history_page():
        return render_template("history.html", runs=storage.history(app.config["DATABASE"]))

    @app.errorhandler(404)
    def missing(error):
        return render_template("base.html", message="Расчёт не найден"), 404

    return app
