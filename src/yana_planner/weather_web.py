"""Weather experiment UI with immutable saved results and bounded calculations."""
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for

from .storage import connection

bp = Blueprint('weather', __name__)
LABELS = {
    'requires_intervention': 'Требуется вмешательство диспетчера',
    'replanned': 'Расписание перестроено',
    'no_replan_required': 'Перепланирование не требуется',
}


def database():
    return connection(current_app.config['DATABASE'])


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS weather_runs '
               '(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, result_json TEXT NOT NULL)')


def render(experiment=None, **kwargs):
    with database() as db:
        setup(db)
        history = [dict(row) for row in db.execute(
            'SELECT id,created_at FROM weather_runs ORDER BY id DESC LIMIT 10')]
    return render_template('weather.html', experiment=experiment,
                           thresholds=[8, 10, 12], labels=LABELS, history=history, **kwargs)


@bp.get('/weather')
def page():
    threshold = request.args.get('threshold', '10')
    if threshold not in {'8', '10', '12'}:
        abort(400, description='Выберите порог 8, 10 или 12 м/с')
    path = Path(current_app.config['WEATHER_RESULTS'])
    if not path.is_file():
        return render()
    data = json.loads(path.read_text())
    selected = next((e for e in data['experiments'] if str(e['threshold_ms']) == threshold), None)
    return render(selected)


@bp.post('/weather/calculate')
def calculate():
    threshold = request.form.get('threshold', '')
    if threshold not in {'8', '10', '12'}:
        return render(error='Выберите порог 8, 10 или 12 м/с'), 400
    try:
        response = subprocess.run(
            [sys.executable, '-m', 'yana_planner.weather_worker'],
            input=json.dumps({'threshold': int(threshold)}),
            capture_output=True, text=True,
            timeout=current_app.config['CALCULATION_TIMEOUT_S'],
        )
        if response.returncode:
            current_app.logger.error('Weather worker failed: %s', response.stderr)
            return render(error='Не удалось выполнить погодный расчёт. Проверьте исходные данные.'), 500
        result = json.loads(response.stdout)
        with database() as db:
            setup(db)
            row = db.execute('INSERT INTO weather_runs (created_at,result_json) VALUES (?,?)',
                             (datetime.now(timezone.utc).isoformat(),
                              json.dumps(result, ensure_ascii=False, allow_nan=False)))
            ident = row.lastrowid
    except subprocess.TimeoutExpired:
        return render(error='Расчёт остановлен по лимиту времени. Можно повторить попытку.'), 408
    except (OSError, ValueError, sqlite3.Error):
        current_app.logger.exception('Weather calculation could not be saved')
        return render(error='Не удалось рассчитать или сохранить результат.'), 500
    return redirect(url_for('weather.saved', ident=ident), code=303)


def load(ident):
    with database() as db:
        setup(db)
        row = db.execute('SELECT result_json FROM weather_runs WHERE id=?', (ident,)).fetchone()
    if row is None:
        abort(404)
    return json.loads(row['result_json'])


@bp.get('/weather/runs/<int:ident>')
def saved(ident):
    return render(load(ident), run_id=ident)


@bp.get('/weather/runs/<int:ident>/result.json')
def download(ident):
    response = jsonify(load(ident))
    response.headers['Content-Disposition'] = 'attachment; filename="weather-result.json"'
    return response
