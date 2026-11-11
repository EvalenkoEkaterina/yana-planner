"""Russian Flask demo with persistent, refresh-safe results."""

import os
import sqlite3

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from . import storage
from .domain import InputError
from .service import NAMES, ROOT, calculate


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("YANA_DATABASE", str(ROOT / "instance/runs.sqlite3")),
        MAX_DEADLINE_H=492,
        SOLVER_TIME_LIMIT=2,
    )
    app.config.update(config or {})

    @app.route("/", methods=["GET", "POST"])
    def index():
        error = None
        if request.method == "POST":
            try:
                key = request.form.get("scenario", "")
                inputs, result = calculate(
                    key,
                    request.form.get("deadline", ""),
                    max_deadline=app.config["MAX_DEADLINE_H"],
                    time_limit=app.config["SOLVER_TIME_LIMIT"],
                )
                ident = storage.save(
                    app.config["DATABASE"],
                    NAMES[key],
                    inputs["deadline_h"],
                    inputs,
                    result,
                    result.get("code_version", "unknown"),
                )
                return redirect(url_for("result_page", ident=ident), code=303)
            except (InputError, OSError, sqlite3.Error) as exc:
                error = str(exc)
        return render_template(
            "index.html", names=NAMES, error=error, maximum=app.config["MAX_DEADLINE_H"]
        ), 400 if error else 200

    def get_run(ident):
        run = storage.load(app.config["DATABASE"], ident)
        if run is None:
            abort(404)
        return run

    @app.get("/runs/<int:ident>")
    def result_page(ident):
        return render_template("result.html", run=get_run(ident))

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
