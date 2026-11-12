"""Russian Flask demo with persistent, refresh-safe results."""

import os
import sqlite3

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from . import storage
from .domain import InputError
from .experiments import comparisons
from .presentation import STATUS, effects_html, event_card, schedule_sections
from .service import NAMES, ROOT, VESSEL_FIELDS, calculate, form_defaults


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("YANA_DATABASE", str(ROOT / "instance/runs.sqlite3")),
        MAX_DEADLINE_H=492,
        SOLVER_TIME_LIMIT=2,
    )
    app.config.update(config or {})
    app.jinja_env.filters["run_status"] = lambda value: STATUS.get(value, "Статус не определён")

    @app.route("/", methods=["GET", "POST"])
    def index():
        error = None
        values = form_defaults()
        if request.method == "POST":
            values = request.form.to_dict(flat=False)
            for field in ("announced_at_h", "delayed_arrival", "new_arrival_h"):
                values.setdefault(field, form_defaults()[field])
            try:
                key = request.form.get("scenario", "")
                search_seconds = request.form.get("search_seconds")
                if search_seconds is not None and search_seconds not in {"2", "10", "30"}:
                    raise InputError("Время поиска: выберите 2, 10 или 30 секунд на этап")
                inputs, result = calculate(
                    key,
                    request.form.get("deadline", ""),
                    max_deadline=app.config["MAX_DEADLINE_H"],
                    time_limit=(
                        int(search_seconds)
                        if search_seconds is not None
                        else app.config["SOLVER_TIME_LIMIT"]
                    ),
                    custom=values if request.form.get("customize") == "1" else None,
                )
                result["report_html"] = result["report_html"].replace(
                    "<body>", "<body>" + render_template("conditions.html", inputs=inputs), 1
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
            except (InputError, OSError, sqlite3.Error) as exc:
                error = str(exc)
        return render_template(
            "index.html",
            names=NAMES,
            error=error,
            maximum=app.config["MAX_DEADLINE_H"],
            values=values,
            vessel_fields=VESSEL_FIELDS,
        ), 400 if error else 200

    def get_run(ident):
        run = storage.load(app.config["DATABASE"], ident)
        if run is None:
            abort(404)
        return run

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
