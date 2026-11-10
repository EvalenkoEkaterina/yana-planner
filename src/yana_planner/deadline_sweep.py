"""Independent deadline experiments; every selected point is retained."""

from __future__ import annotations

import csv
import html
from copy import deepcopy
from importlib.util import find_spec
from math import isfinite
from pathlib import Path

from .domain import InputError, Scenario, integer, proposals
from .experiments import summarize_experiment
from .io import _event_notice, export_failure, export_result, write_json
from .replanning import apply_event, run_replanning
from .validation import check_schedule


def _proof(logs, stage):
    matches = [entry for entry in logs if entry.get("stage") == stage]
    return matches[-1].get("optimal") if matches else None


def _audit(result, cfg, event, deadline):
    """Reconstruct execution and own committed prefix for each method."""
    changed = apply_event(cfg, event)
    if result["initial"]["input_sha256"] != Scenario.from_dict(cfg).fingerprint:
        raise InputError("Первоначальный вход отличается от зафиксированного")
    if result["event"] != event or result["deadline_h"] != deadline:
        raise InputError("Сообщение или срок отличаются от протокола")
    for source, revised in [("baseline", "revised_baseline"), ("planner", "revised_planner")]:
        item = result[revised]
        fixed = proposals(
            [
                t
                for t in result["initial"][source]["trips"]
                if t["start_h"] <= event["announced_at_h"]
            ]
        )
        if item["updated_input"] != changed or proposals(item["committed"]) != fixed:
            raise InputError("Изменён вход или зафиксированная история метода")
        if item["result"]["status"] != "valid_simulation":
            continue
        plan = proposals(item["result"]["trips"])
        if not all(t in plan for t in fixed) or any(
            t not in fixed and t["start_h"] <= event["announced_at_h"] for t in plan
        ):
            raise InputError("Переписано прошлое или назначено отправление задним числом")
        checked = check_schedule(changed, plan, deadline)
        if checked["status"] != "valid_simulation":
            raise InputError("Расписание не прошло повторную независимую проверку")
        item["result"] = checked


def run_deadline_sweep(
    cfg, event, deadlines, *, time_limit=3, incumbent=None, on_start=None, on_result=None
):
    scenario = Scenario.from_dict(cfg)
    cfg = scenario.to_dict()
    apply_event(cfg, event)
    if not isinstance(deadlines, (list, tuple)) or not deadlines:
        raise InputError("Нужен непустой список сроков")
    grid = [integer(d, "deadline_h", minimum=1) for d in deadlines]
    if grid != sorted(set(grid)) or grid[0] <= event["announced_at_h"]:
        raise InputError("Сроки должны возрастать без повторов и быть позже сообщения")
    if (
        isinstance(time_limit, bool)
        or not isinstance(time_limit, (int, float))
        or not isfinite(time_limit)
        or time_limit <= 0
    ):
        raise InputError("time_limit должен быть положительным числом секунд")
    protocol = dict(
        scenario_id=cfg["id"],
        input=deepcopy(cfg),
        input_sha256=scenario.fingerprint,
        event=deepcopy(event),
        deadlines_h=grid,
        time_limit_per_solver_stage_s=time_limit,
        initial_reference=proposals(incumbent) if incumbent is not None else None,
        method="Independent replan per deadline; initial plan does not know the event",
        selection="Fixed grid around prior examples; synthetic sensitivity study, not historical sampling",
        boundary="First success on this grid is not the exact continuous or hourly threshold",
    )
    if on_start is not None:
        on_start(deepcopy(protocol))
    rows = []
    for deadline in grid:
        result = None
        try:
            result = run_replanning(
                deepcopy(cfg),
                deepcopy(event),
                deadline,
                time_limit=time_limit,
                incumbent=deepcopy(incumbent),
            )
            _audit(result, cfg, event, deadline)
            row = summarize_experiment(result)[1]
            row.update(experiment_status="completed", error=None)
            logs = result["revised_planner"]["solver"]
            for label, stage in [
                ("tonnage", "maximize_tonnes"),
                ("trips", "minimize_trips_at_fixed_tonnes"),
                ("last_receipt", "minimize_last_receipt_at_fixed_tonnes_trips"),
            ]:
                row[f"MILP_{label}_solver_proven"] = _proof(logs, stage)
        except InputError as error:
            result = dict(
                status="experiment_failed",
                message=str(error),
                accepted_t=None,
                unverified_result=result,
            )
            failed = dict(status="experiment_failed")
            row = summarize_experiment(
                dict(scenario_id=cfg["id"], deadline_h=deadline, baseline=failed, planner=failed)
            )[0]
            row.update(phase="after", experiment_status="failed", error=str(error))
            for label in ["tonnage", "trips", "last_receipt"]:
                row[f"MILP_{label}_solver_proven"] = None
        rows.append(row)
        if on_result is not None:
            on_result(deepcopy(row), deepcopy(result))
    first = {
        method: next((r["deadline_h"] for r in rows if r[f"{method}_target_met"] is True), None)
        for method in ["READY", "MILP"]
    }
    return dict(
        protocol=protocol,
        rows=rows,
        first_full_on_grid_h=first,
        actual_savings_rub=None,
        real_admissibility="requires_confirmation",
    )


def require_plotting():
    if find_spec("matplotlib") is None:
        raise InputError("Для графика установите пакет проекта с дополнением plots")


def plot_deadline_sweep(result, directory):
    require_plotting()
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    rows = result["rows"]
    target = result["protocol"]["input"]["target_t"]
    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )
    colors = {"READY": "#2563a6", "MILP": "#087e65"}
    for method in ["READY", "MILP"]:
        values = [r[f"{method}_t"] if r[f"{method}_t"] is not None else float("nan") for r in rows]
        top.plot(
            [r["deadline_h"] for r in rows],
            values,
            "o--",
            color=colors[method],
            label=method,
            linewidth=1.8,
            markersize=5,
        )
    top.axhline(target, color="#64748b", linestyle=":", label=f"Задание: {target} т")
    top.set_ylim(0, target * 1.08 if target else 1)
    top.set_ylabel("Принято к сроку, т")
    top.set_title("Завершение доставки после сообщения о новых условиях", loc="left", pad=12)
    top.legend(loc="lower right", frameon=False, ncol=3)
    grid = result["protocol"]["deadlines_h"]
    width = min((b - a for a, b in zip(grid, grid[1:])), default=8) * 0.65
    known = [r for r in rows if r["delta_t"] is not None]
    bottom.bar(
        [r["deadline_h"] for r in known],
        [r["delta_t"] for r in known],
        width=width,
        color=[colors["MILP"] if r["delta_t"] >= 0 else colors["READY"] for r in known],
    )
    for row in known:
        if row["delta_t"]:
            bottom.annotate(
                f"{row['delta_t']:+g}",
                (row["deadline_h"], row["delta_t"]),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                fontsize=9,
            )
    bottom.axhline(0, color="#64748b", linewidth=0.8)
    bottom.margins(y=0.25)
    bottom.set_ylabel("MILP − READY, т")
    bottom.set_xlabel("Срок завершения приёмки, часы от T0")
    bottom.set_xticks(grid)
    for axis in [top, bottom]:
        axis.grid(axis="y", color="#e2e8f0")
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
    fig.suptitle(f"{result['protocol']['scenario_id']}: чувствительность к сроку", fontsize=16)
    fig.text(
        0.5,
        0.01,
        "Сценарные данные. Линии соединяют проверенные точки; точная граница между ними не вычислена.",
        ha="center",
        fontsize=9,
        color="#475569",
    )
    fig.tight_layout(rect=(0, 0.035, 1, 0.96))
    folder = Path(directory)
    for extension in ["png", "svg"]:
        fig.savefig(
            folder / f"deadline_sweep.{extension}", dpi=160, metadata={"Creator": "Yana Planner"}
        )
    plt.close(fig)


def export_deadline_sweep(directory, result, *, plot=False):
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "sweep.json", result)
    rows = result["rows"]
    with (folder / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if plot:
        plot_deadline_sweep(result, folder)
    columns = [
        ("deadline_h", "Срок, ч"),
        ("READY_t", "READY, т"),
        ("READY_shortfall_t", "Недопоставка READY, т"),
        ("READY_trips", "Рейсы READY"),
        ("MILP_t", "MILP, т"),
        ("MILP_shortfall_t", "Недопоставка MILP, т"),
        ("MILP_trips", "Рейсы MILP"),
        ("delta_t", "Разница, т"),
        ("MILP_tonnage_solver_proven", "Оптимум тоннажа доказан решателем"),
    ]

    def cell(value):
        if value is None:
            return "не определено"
        if isinstance(value, bool):
            return "да" if value else "нет"
        return html.escape(str(value))

    body = "".join(
        "<tr>"
        + "".join(f"<td>{cell(row[key])}</td>" for key, _ in columns)
        + f'<td><a href="runs/{row["deadline_h"]}/report.html">Отчёт</a></td></tr>'
        for row in rows
    )
    headings = "".join(f"<th>{title}</th>" for _, title in columns) + "<th>Расписание</th>"
    first = result["first_full_on_grid_h"]
    notice = _event_notice(
        dict(event=result["protocol"]["event"], initial_input=result["protocol"]["input"])
    )
    image = (
        '<img src="deadline_sweep.svg" alt="Доставленные тонны в зависимости от срока">'
        if plot
        else ""
    )
    page = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Яна — серия сроков</title><style>body{{font:16px system-ui;max-width:1200px;margin:32px auto;padding:0 20px;color:#17354a;background:#f5f7fb}}p{{line-height:1.6}}img{{width:100%;background:white}}table{{border-collapse:collapse;background:white;width:100%}}th,td{{padding:10px;border-bottom:1px solid #dde4ec;text-align:left}}th{{background:#274b69;color:white}}.table{{overflow:auto}}a{{color:#087e65}}</style>
<h1>Яна: чувствительность к сроку доставки</h1><p>Сценарный эксперимент. Все {len(rows)} выбранных сроков сохранены.
Объём, суда, поступления и новое сообщение одинаковы. Для каждого срока первоначальный и обновлённый планы строятся заново.</p>{notice}
<p>Первый проверенный срок полного выполнения: READY — <strong>{cell(first["READY"])} ч</strong>, MILP — <strong>{cell(first["MILP"])} ч</strong>.
Это результат на выбранной сетке, не точная минимальная граница. Сетка задана вокруг ранее рассмотренных примеров и не является исторической выборкой.</p>
{image}<div class="table"><table><tr>{headings}</tr>{body}</table></div>
<p>Нулевой эффект сохранён. Неизвестные и ошибочные результаты не заменены нулями. Времена и рейсы как улучшения сравниваются только при равном тоннаже.
При лимите решения допустимый найденный план не объявляется доказанным оптимумом. Частота преимущества на сетке не является вероятностью успеха на практике.</p>
<p><a href="comparison.csv">Таблица CSV</a> · <a href="sweep.json">Все показатели и статусы</a> · <a href="protocol.json">Протокол и исходные условия</a></p></html>"""
    (folder / "report.html").write_text(page, encoding="utf-8")


def save_sweep_point(directory, row, result):
    folder = Path(directory) / "runs" / str(row["deadline_h"])
    if row["experiment_status"] == "completed":
        export_result(folder, result)
    else:
        export_failure(folder, result)
