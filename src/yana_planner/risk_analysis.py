"""Stress-test one saved plan against independent, explicitly supplied events."""

import csv
import hashlib
import html
import json
from copy import deepcopy
from decimal import Decimal
from math import isfinite
from pathlib import Path

from .domain import ConfirmationRequired, InputError, Scenario, integer, proposals
from .economics import cost, resolve_cost_terms
from .io import write_json
from .presentation import STATUS, event_card
from .replanning import apply_event, replan
from .validation import check_schedule


def _metrics(verdict, deadline, priced=None, solver=None):
    valid = verdict.get("status") == "valid_simulation"
    accepted = verdict.get("accepted_t") if valid else None
    last = verdict.get("last_receipt_h") if valid else None
    return dict(
        status=verdict["status"],
        valid=valid,
        accepted_t=accepted,
        shortfall_t=verdict.get("shortfall_t") if valid else None,
        target_met=valid and verdict.get("shortfall_t") == 0,
        trip_count=verdict.get("trip_count") if valid else None,
        last_receipt_h=last,
        deadline_slack_h=deadline - last if last is not None else None,
        total_rub=priced.get("total_rub") if valid and priced else None,
        optimal=bool(solver) and all(s.get("optimal") is True for s in solver)
        if solver is not None
        else None,
        requires_intervention=verdict["status"] == "requires_intervention",
    )


def _difference(left, right):
    if (
        left["valid"]
        and right["valid"]
        and left["accepted_t"] == right["accepted_t"]
        and left["total_rub"] is not None
        and right["total_rub"] is not None
    ):
        return float(Decimal(str(left["total_rub"])) - Decimal(str(right["total_rub"])))
    return None


def analyze_risks(
    cfg,
    initial_plan,
    cases,
    deadline,
    *,
    strategy="trips",
    cost_terms=None,
    time_limit=3,
    adaptive=False,
):
    """Each event starts from exactly the same nominal plan, never a fresh initial solve.

    Counts describe only the supplied scenario set, not real-world probabilities.
    """
    scenario = Scenario.from_dict(cfg)
    cfg = scenario.to_dict()
    deadline = integer(deadline, "deadline_h")
    initial_plan = proposals(initial_plan)
    nominal = check_schedule(cfg, initial_plan, deadline)
    if nominal["status"] != "valid_simulation":
        raise InputError("Для анализа рисков нужен допустимый исходный план")
    if strategy not in ("trips", "cost"):
        raise InputError("strategy: выберите trips или cost")
    if (
        isinstance(time_limit, bool)
        or not isinstance(time_limit, (int, float))
        or not isfinite(time_limit)
        or time_limit <= 0
    ):
        raise InputError("time_limit должен быть положительным числом секунд")
    raw_cases = cases.get("cases") if isinstance(cases, dict) else cases
    if not isinstance(raw_cases, list) or not raw_cases:
        raise InputError("Нужен непустой список cases")
    ids = set()
    prepared = []
    for case in raw_cases:
        if not isinstance(case, dict):
            raise InputError("Каждый опыт cases должен быть объектом")
        ident = case.get("id")
        if not isinstance(ident, str) or not ident.strip() or ident == "nominal" or ident in ids:
            raise InputError("Нужен уникальный непустой ID опыта, отличный от nominal")
        if "label" in case and not isinstance(case["label"], str):
            raise InputError("Название опыта должно быть текстом")
        ids.add(ident)
        # Validate all inputs before beginning any of the new optimization calls.
        prepared.append((deepcopy(case), apply_event(cfg, case.get("event"))))
    if strategy == "cost":
        resolved = resolve_cost_terms(cfg, cost_terms)
        if resolved["missing"]:
            raise ConfirmationRequired(
                "Для денежного режима заполните: " + ", ".join(resolved["missing"])
            )
    nominal_cost = cost(cfg, initial_plan, deadline, cost_terms) if cost_terms is not None else None
    rows = []
    for case, changed in prepared:
        unchanged = check_schedule(changed, initial_plan, deadline)
        unchanged_cost = (
            cost(changed, initial_plan, deadline, cost_terms)
            if cost_terms is not None and unchanged["status"] == "valid_simulation"
            else None
        )
        revised = replan(
            cfg,
            initial_plan,
            case["event"],
            deadline,
            method="milp",
            strategy=strategy,
            cost_terms=cost_terms,
            time_limit=time_limit,
            adaptive=adaptive,
        )
        old = _metrics(unchanged, deadline, unchanged_cost)
        new = _metrics(revised["result"], deadline, revised.get("cost"), revised["solver"])
        rows.append(
            dict(
                id=case["id"],
                label=case.get("label", case["id"]),
                event=deepcopy(case["event"]),
                unchanged=old,
                replanned=new,
                replanning_cost_difference_rub=_difference(old, new),
                unchanged_validation=unchanged,
                replanning=revised,
            )
        )
    full_slacks = [
        r["replanned"]["deadline_slack_h"]
        for r in rows
        if r["replanned"]["target_met"] and r["replanned"]["deadline_slack_h"] is not None
    ]
    known_shortfalls = [r["replanned"]["shortfall_t"] for r in rows if r["replanned"]["valid"]]
    return dict(
        schema_version="1.0",
        analysis="independent_single_event_stress_test",
        scenario_id=cfg["id"],
        scenario_sha256=scenario.fingerprint,
        original_input=deepcopy(cfg),
        deadline_h=deadline,
        target_t=cfg["target_t"],
        initial_plan=deepcopy(initial_plan),
        initial_validation=nominal,
        initial_plan_sha256=hashlib.sha256(
            json.dumps(initial_plan, sort_keys=True).encode()
        ).hexdigest(),
        nominal=_metrics(nominal, deadline, nominal_cost),
        case_set=deepcopy(cases),
        cases=rows,
        settings=dict(
            strategy=strategy,
            cost_terms=deepcopy(cost_terms),
            time_limit_per_stage_s=time_limit,
            adaptive_search=adaptive,
        ),
        summary=dict(
            case_count=len(rows),
            unchanged_valid_cases=sum(r["unchanged"]["valid"] for r in rows),
            unchanged_full_delivery_cases=sum(r["unchanged"]["target_met"] for r in rows),
            replanned_valid_cases=sum(r["replanned"]["valid"] for r in rows),
            replanned_full_delivery_cases=sum(r["replanned"]["target_met"] for r in rows),
            intervention_cases=sum(r["replanned"]["requires_intervention"] for r in rows),
            unconfirmed_cases=sum(not r["replanned"]["valid"] for r in rows),
            max_known_shortfall_t=max(known_shortfalls, default=None),
            min_slack_full_delivery_h=min(full_slacks, default=None),
            probability_of_success=None,
        ),
        interpretation="Coverage of supplied scenarios, not an estimated probability; the initial plan is not reoptimized per case",
        actual_savings_rub=None,
    )


def export_risk_report(directory, result):
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "results.json", result)
    rows = [
        dict(case_id="nominal", label="Исходные условия", method="nominal", **result["nominal"])
    ]
    for case in result["cases"]:
        for method in ["unchanged", "replanned"]:
            rows.append(
                dict(case_id=case["id"], label=case["label"], method=method, **case[method])
            )
    with (out / "risk-summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    def cell(value):
        if value is None:
            return "не определено"
        if isinstance(value, bool):
            return "Да" if value else "Нет"
        return f"{value:.2f}" if isinstance(value, float) else str(value)

    headings = [
        "Опыт",
        "Действие",
        "Статус",
        "Принято, т",
        "Недопоставка, т",
        "Рейсы",
        "Приёмка, ч",
        "Запас до срока, ч",
        "Стоимость, руб.",
        "Оптимум пересчёта",
    ]
    table = "<tr>" + "".join(f"<th>{h}</th>" for h in headings) + "</tr>"
    methods = dict(
        nominal="Исходный план", unchanged="Без изменения плана", replanned="После перепланирования"
    )
    for row in rows:
        values = [
            row["label"],
            methods[row["method"]],
            {
                **STATUS,
                "invalid": "Недопустимый план",
                "requires_confirmation": "Нужны подтверждённые условия",
            }.get(row["status"], row["status"]),
            *[
                row[k]
                for k in [
                    "accepted_t",
                    "shortfall_t",
                    "trip_count",
                    "last_receipt_h",
                    "deadline_slack_h",
                    "total_rub",
                    "optimal",
                ]
            ],
        ]
        table += "<tr>" + "".join(f"<td>{html.escape(cell(v))}</td>" for v in values) + "</tr>"
    events = "".join(
        "<details><summary>"
        + html.escape(case["label"])
        + "</summary>"
        + event_card(dict(event=case["event"], original_input=result["original_input"]))
        + "<p>Проверка без изменений: "
        + html.escape(
            "; ".join(case["unchanged_validation"].get("errors", [])) or "ограничения соблюдены"
        )
        + "</p><p>Перепланирование: "
        + html.escape(
            "; ".join(case["replanning"]["result"].get("errors", [])) or "ограничения соблюдены"
        )
        + "</p></details>"
        for case in result["cases"]
    )
    s = result["summary"]
    page = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Проверка устойчивости</title>
<style>body{{font:16px Arial,sans-serif;line-height:1.5;margin:32px;color:#202024}}h1{{font-size:30px}}table{{border-collapse:collapse;width:100%}}th,td{{padding:9px;border:1px solid #ddd;text-align:left}}th{{background:#f6edf2}}.table{{overflow:auto}}details{{margin-top:18px}}a{{color:#ae2f67}}</style>
<h1>Проверка устойчивости: {html.escape(result["scenario_id"])}</h1>
<p>Один исходный план. Цель {result["target_t"]} т, приёмка до {result["deadline_h"]} ч. Каждый опыт независимый, события не складываются.</p>
<p>Полный объём без изменения плана: {s["unchanged_full_delivery_cases"]} из {s["case_count"]}. После пересчёта: {s["replanned_full_delivery_cases"]} из {s["case_count"]}. Требуется вмешательство: {s["intervention_cases"]}.</p>
<p>Это покрытие заданного набора сценариев, а не вероятность успешной доставки. Неизвестные результаты не считаются нулевой доставкой. Новый поиск не строит единственный план, оптимальный сразу для всех сценариев.</p>
<div class="table"><table>{table}</table></div>
<p>Стоимость сценарная. Разные объёмы не сравниваются как экономия одинаковой работы. Источник тарифов, события, фиксированная история и статусы решателя сохранены в JSON. Номинальный план здесь оценивается, а не решается заново, поэтому его оптимальность не устанавливается.</p>
<p><a href="results.json">Полный JSON</a> · <a href="risk-summary.csv">Сводка CSV</a></p>{events}</html>"""
    (out / "report.html").write_text(page, encoding="utf-8")
