"""Reproduce one historical graph → weather → transport report check, offline."""

import json
from html import escape
from pathlib import Path

from yana_planner.rate_import import load_vessel_rates
from yana_planner.river_graph import load_network, shortest_path
from yana_planner.route_weather import assess_route_weather
from yana_planner.service import calculate_package
from yana_planner.visual_report import summary_page

ROOT = Path(__file__).resolve().parents[1]


def run(output=None):
    output = Path(output or ROOT / "output/graph-weather-check")
    output.mkdir(parents=True, exist_ok=True)
    path = shortest_path(load_network(), "nizhneyansk", "ust-kuyga")
    weather = assess_route_weather(path, t0="2025-07-01T00:00:00+00:00", horizon_h=120)
    if weather["status"] != "ok" or weather["closures"] != []:
        raise RuntimeError("Этот опыт ожидает полный набор без превышений; не подменяйте результат")
    package = json.loads((ROOT / "src/yana_planner/static/data/r03.package.json").read_text())
    package["route"] = dict(
        id="graph-weather-historical-check",
        origin=path["origin"],
        destination=path["destination"],
        length_km=path["length_km"],
        graph_path=path,
    )
    package["sources"] = path["network_snapshot"]["sources"]
    package["facts"] = [
        dict(field="route.length_km", value=324, status="documented", source_id="S1"),
        dict(
            field="weather_assessment",
            value="Исторический прогноз за 1–5 июля 2025; порог 8 м/с не превышен. Время выпуска неизвестно; не оперативный опыт.",
            status="scenario_assumption",
        ),
        dict(
            field="transport",
            value="Учебные 400 т и ручные времена; не реальная перевозка",
            status="scenario_assumption",
        ),
    ]
    cfg = package["scenario"]
    cfg.update(
        id="GRAPH-WEATHER-CHECK",
        t0=weather["t0"],
        timezone="UTC",
        target_t=400,
        initial_source_t=0,
        destination_capacity_t=400,
        return_cutoff_h=120,
        arrivals=[dict(h=0, t=400)],
        closures=[],
    )
    cfg["provenance"] = dict(
        kind="synthetic_scenario",
        origin="Historical weather integration check",
        operating_parameters="All freight, fleet and times are scenario assumptions",
    )
    base_terms = json.loads((ROOT / "examples/ushanova/terms.json").read_text())
    base_terms["horizon_h"] = 120
    terms = load_vessel_rates(cfg, base_terms, ROOT / "examples/ushanova/vessel_rates.csv")
    package["facts"].append(
        dict(
            field="cost_terms",
            value="Ставки Лизы: учебные SIM-A/SIM-B; оплата всего флота до 120 ч, не рыночные тарифы",
            status="scenario_assumption",
        )
    )
    package.update(
        deadline_h=100, strategy="trips", cost_terms=terms, event=None, weather_assessment=weather
    )
    inputs, result = calculate_package(package, search_seconds=2)
    # Empty closures mean no event; never weaken the forecast-issuance checks in the core.
    for name, value in [
        ("package.json", package),
        ("weather.json", weather),
        ("results.json", dict(inputs=inputs, result=result)),
    ]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    html = summary_page(result, "Проверка графа и исторической погоды", route=package["route"])
    note = "<p><strong>Историческая проверка подключения.</strong> 1–5 июля 2025, 120 часов, две точки. Порог 8 м/с не превышен; погодное событие не добавлено. Время выпуска неизвестно. Перевозка 400 т и все времена учебные. Это не доказательство качества прогноза, экономии или переноса на другие маршруты.</p>"
    html = html.replace("<h1>", note + "<h1>", 1)
    rows = []
    for key, label in [("baseline", "READY"), ("planner", "MILP")]:
        for row in result["costs"][key]["per_vessel"]:
            values = [
                label,
                row["vessel_id"],
                row["trip_count"],
                row["active_vessel_h"],
                row["paid_waiting_h"],
                row["components"]["separate_trip_operations_rub"],
                row["components"]["active_time_rub"],
                row["components"]["waiting_time_rub"],
                row["total_rub"],
            ]
            rows.append("<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in values) + "</tr>")
    headers = [
        "План",
        "Судно",
        "Рейсы",
        "Активность, ч",
        "Ожидание, ч",
        "За рейсы, ₽",
        "За активность, ₽",
        "За ожидание, ₽",
        "Итого, ₽",
    ]
    economy = (
        "<section><h2>Из чего складываются расходы</h2>"
        "<p><strong>C = N × F + H × R + W × P</strong> — сумма по всем судам.</p>"
        "<p>Сценарные ставки для SIM-A/SIM-B: 25 000 ₽/рейс, 1 000 ₽/активный час, 500 ₽/час ожидания. Не подтверждены перевозчиком.</p>"
        "<p>Оплачивается весь заявленный флот: SIM-A с 0 до 120 ч, SIM-B с 48 до 120 ч, включая судно без рейсов. Подготовка после возврата входит в активность; каждый час учитывается один раз. Приёмка до 100 ч — отдельный срок.</p>"
        '<div style="overflow-x:auto"><table><thead><tr>'
        + "".join(f"<th>{h}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        "<p>Приоритет расчёта: объём → число рейсов → срок. Стоимость оценена для найденных планов, минимум стоимости не заявляется. Совпадение затрат не является доказательством экономии.</p></section>"
    )
    html = html.replace("</html>", economy + "</html>")
    (output / "report.html").write_text(html)
    return inputs, result, weather


if __name__ == "__main__":
    _, result, weather = run()
    print(
        json.dumps(
            dict(
                weather_status=weather["status"],
                closures=weather["closures"],
                ready=result["baseline"]["accepted_t"],
                milp=result["planner"]["accepted_t"],
            ),
            ensure_ascii=False,
        )
    )
