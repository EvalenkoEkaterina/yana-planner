"""Self-contained read-only HTML report for the weather experiment."""

from html import escape
from pathlib import Path

LABELS = {
    "no_replan_required": "План остаётся допустимым",
    "replanned": "План пересчитан",
    "requires_intervention": "Нужно вмешательство диспетчера",
}


def report(data, destination):
    parts = [
        """<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Yana Planner — погодный опыт</title>
<style>body{font:17px/1.6 system-ui;max-width:1120px;margin:40px auto;padding:0 24px;color:#173042;background:#fafcfd}h1{font-size:36px;line-height:1.2}h2{margin-top:40px}table{border-collapse:collapse;width:100%;margin:20px 0}th,td{text-align:left;padding:10px;border-bottom:1px solid #cbd5df}th{background:#eaf1f5}svg{width:100%;height:auto;background:white}small{color:#485c6b}code{background:#eaf1f5;padding:3px}a{color:#07588a}</style>
<h1>Как ветер влияет на расписание</h1>
<p>Учебный R03 · Нижнеянск — Усть-Куйга · 1650 т · срок приёмки 328 ч</p>
<p><b>Исторический реанализ ERA5, не прогноз.</b> Начало сценария — 10 августа 2026, 08:00 UTC+10.
Ветер на высоте 10 м из двух ячеек модели. Дождь, видимость, глубины, лёд и предупреждения об опасных явлениях не подключены.</p>
<p>Условное правило: максимум ветра в двух точках ≥ порога закрывает движение с грузом и возврат.
Пороги 8/10/12 м/с не являются нормативами судоходства. Погрузка и приёмка не закрываются.</p>"""
    ]
    winds = data["experiments"][0]["event"]["hourly_max_wind_ms"]
    coords = " ".join(f"{50 + h * 900 / 492:.1f},{230 - v * 12:.1f}" for h, v in enumerate(winds))
    parts.append(
        '<h2>Погодный ряд на горизонте оборота</h2><svg viewBox="0 0 1000 280" role="img" aria-label="Максимальная скорость ветра в двух точках, м/с">'
    )
    for v in (0, 8, 10, 12, 16):
        y = 230 - v * 12
        parts.append(
            f'<line x1="50" x2="950" y1="{y}" y2="{y}" stroke="#ccd6df"/><text x="10" y="{y + 5}" font-size="14">{v}</text>'
        )
    parts.append(f'<polyline points="{coords}" fill="none" stroke="#137f96" stroke-width="2"/>')
    for h in (0, 60, 120, 240, 328, 400, 492):
        x = 50 + h * 900 / 492
        parts.append(f'<text x="{x}" y="258" text-anchor="middle" font-size="13">{h}</text>')
    parts.append('<text x="450" y="277" font-size="13">Часы от начала сценария</text></svg>')
    parts.append(
        "<h2>Два способа использования той же информации</h2><p><b>Заблаговременный сценарий:</b> ограничения переданы до выбора рейсов. Это расчёт при условном знании будущей погоды, а не оценка качества прогноза.</p><p><b>Позднее сообщение:</b> исходный план уже начал исполняться; на 60-м часу раскрываются будущие ограничения. Начатые рейсы изменять нельзя. Эти опыты различаются доступной информацией и не доказывают превосходство алгоритма.</p>"
    )
    for e in data["experiments"]:
        parts.append(
            f"<h2>Условный порог {e['threshold_ms']} м/с</h2><table><tr><th>Опыт</th><th>Метод</th><th>Результат</th><th>Принято, т</th><th>Недопоставка, т</th></tr>"
        )
        for name, results in [
            ("Без погодных ограничений", data["initial"]),
            ("Условия известны до планирования", e["weather_known_before_start"]),
        ]:
            for key, label in [("baseline", "READY"), ("planner", "MILP")]:
                r = results[key]
                parts.append(
                    f"<tr><td>{name}</td><td>{label}</td><td>{escape(r['status'])}</td><td>{r.get('accepted_t', '—')}</td><td>{r.get('shortfall_t', '—')}</td></tr>"
                )
        for method, run in e["runs"].items():
            r = run["result"]
            parts.append(
                f"<tr><td>Сообщение на 60-м часу</td><td>{method.upper()}</td><td>{LABELS[run['action']]}</td><td>{r.get('accepted_t') if r.get('accepted_t') is not None else 'не определено'}</td><td>{r.get('shortfall_t') if r.get('shortfall_t') is not None else 'не определено'}</td></tr>"
            )
        parts.append(
            "</table><details><summary>Интервалы после сообщения и значения ветра</summary><table><tr><th>Начало, ч</th><th>Конец, ч</th><th>Максимум, м/с</th><th>Начало UTC</th></tr>"
        )
        for ev in e["event"]["evidence"]:
            parts.append(
                f"<tr><td>{ev['from_h']}</td><td>{ev['to_h']}</td><td>{ev['peak_wind_ms']}</td><td>{ev['start_utc']}</td></tr>"
            )
        parts.append("</table></details>")
    parts.append(
        '<h2>Как читать результат</h2><p>Допустимый план может доставить только часть груза. Выполнение задания проверяется по недопоставке, оптимальность — по журналу решателя в JSON. При конфликте с начатым рейсом тоннаж не заменяется нулём. Остановка судна внутри плеча не моделируется: решение требует участия диспетчера.</p><p>Полные расписания, входы и статусы: <a href="results.json">results.json</a>.</p><h2>Источники</h2>'
    )
    for source in data["experiments"][0]["event"]["sources"]:
        parts.append(
            f'<p>{escape(source["point"]["name"])} · <a href="{escape(source["url"], quote=True)}">Запрос Open-Meteo / ECMWF ERA5</a><br><small>Получено: {escape(source["retrieved_at"])}</small></p>'
        )
    parts.append(
        "<p><small>Атрибуция: Open-Meteo и ECMWF ERA5, CC BY 4.0. Это исследовательский сценарий, не эксплуатационное разрешение.</small></p></html>"
    )
    Path(destination).write_text("\n".join(parts))
