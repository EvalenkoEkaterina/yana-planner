"""R03 resource controls with declared bounds; no changes to the planner."""

import argparse
import csv
import html
import json
from copy import deepcopy
from pathlib import Path

from yana_planner.domain import Scenario, proposals
from yana_planner.io import export_result, write_json
from yana_planner.replanning import run_replanning
from yana_planner.validation import check_schedule

ROOT = Path(__file__).resolve().parents[1]


def cases(base):
    supply = deepcopy(base)
    supply["arrivals"] = [dict(h=0, t=500), dict(h=120, t=500)]
    storage = deepcopy(base)
    storage["destination_capacity_t"] = 1000
    withdrawal = deepcopy(storage)
    withdrawal["withdrawals"] = [dict(h=200, t=650, confirmed_in_scenario=True)]
    blocked = deepcopy(base)
    for vessel in blocked["vessels"]:
        vessel["unavailable_intervals"] = [[0, 493]]
    return [
        dict(id="C1", label="R03 без готового исходного плана", cfg=deepcopy(base), bound_t=1650),
        dict(id="C2", label="Всего поступает 1000 т при задании 1650 т", cfg=supply, bound_t=1000),
        dict(id="C3", label="Склад 1000 т, отбора нет", cfg=storage, bound_t=1000),
        dict(id="C4", label="Склад 1000 т; отбор 650 т в час 200", cfg=withdrawal, bound_t=1650),
        dict(id="C5", label="Оба судна недоступны во всём горизонте", cfg=blocked, bound_t=0),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--time-limit", type=float, default=5)
    args = parser.parse_args()
    folder = Path(args.out)
    base = json.loads((ROOT / "examples/r03.json").read_text())
    event = json.loads((ROOT / "examples/arrival_delay.json").read_text())
    selected = cases(base)
    write_json(
        folder / "protocol.json",
        dict(
            scenario="SIM_ONLY resource controls",
            deadline_h=328,
            event=event,
            initial_reference=None,
            time_limit_per_stage_s=args.time_limit,
            cases=selected,
            attribution="Prepared and executed with AI assistance",
        ),
    )
    rows = []
    for case in selected:
        cfg = case["cfg"]
        original = deepcopy(cfg)
        result = run_replanning(cfg, event, 328, time_limit=args.time_limit)
        assert cfg == original, "Caller input was mutated"
        assert result["initial"]["input_sha256"] == Scenario.from_dict(cfg).fingerprint
        row = dict(
            case=case["id"], description=case["label"], physical_upper_bound_t=case["bound_t"]
        )
        for method, key in [("READY", "revised_baseline"), ("MILP", "revised_planner")]:
            item = result[key]
            checked = check_schedule(item["updated_input"], proposals(item["result"]["trips"]), 328)
            assert checked["status"] == "valid_simulation", checked["errors"]
            assert all(t in proposals(checked["trips"]) for t in item["committed"])
            q = checked["accepted_t"]
            assert 0 <= q <= case["bound_t"], "Physical upper bound exceeded"
            assert checked["shortfall_t"] == 1650 - q
            for state in checked["ledger"]:
                assert (
                    state["free_t"]
                    == cfg["destination_capacity_t"] - state["destination_t"] - state["reserved_t"]
                )
                assert state["free_t"] >= 0
            row[f"{method}_t"] = q
            row[f"{method}_shortfall_t"] = checked["shortfall_t"]
            row[f"{method}_trips"] = checked["trip_count"]
        row["checks"] = "passed"
        rows.append(row)
        export_result(folder / case["id"], result)
        print(
            f"{case['id']}: READY {row['READY_t']} т / MILP {row['MILP_t']} т; верхний предел {case['bound_t']} т",
            flush=True,
        )
    write_json(
        folder / "results.json",
        dict(
            cases=rows,
            scenario_only=True,
            actual_savings_rub=None,
            checks="Independent schedule reconstruction, declared bounds, own history and dynamic storage",
        ),
    )
    with (folder / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    body = "".join(
        f'<tr><td><a href="{r["case"]}/report.html">{r["case"]}</a></td>'
        f"<td>{html.escape(r['description'])}</td><td>{r['physical_upper_bound_t']}</td>"
        f"<td>{r['READY_t']}</td><td>{r['READY_shortfall_t']}</td>"
        f"<td>{r['MILP_t']}</td><td>{r['MILP_shortfall_t']}</td></tr>"
        for r in rows
    )
    page = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Яна — контроль ресурсов</title><style>body{{font:16px system-ui;max-width:1100px;margin:32px auto;padding:0 20px;color:#17354a;background:#f5f7fb}}p{{line-height:1.6}}table{{border-collapse:collapse;background:white;width:100%}}th,td{{padding:12px;border-bottom:1px solid #dde4ec;text-align:left}}th{{background:#274b69;color:white}}.table{{overflow:auto}}a{{color:#087e65}}</style>
<h1>Яна: контроль физических ограничений</h1>
<p>Пять сценарных проверок R03. Задание 1650 т, срок 328 ч. В час 60 сообщают
о переносе второго поступления с 120 на 168 ч. Другие изменения в каждом случае
известны до построения первоначального плана. Готовый исходный кандидат не передаётся.</p>
<div class="table"><table><tr><th>Опыт</th><th>Условия</th><th>Физический предел, т</th>
<th>READY, т</th><th>Недопоставка READY, т</th><th>MILP, т</th><th>Недопоставка MILP, т</th></tr>{body}</table></div>
<p>В C4 отбор 650 т в час 200 задан условием сценария. Это не подтверждённый факт
работы нефтебазы. Освобождение места допускается только с этого момента; кумулятивная
приёмка может превысить 1000 т, а одновременное хранение вместе с резервом — не может.</p>
<p>Здесь проверяются физические ограничения. Статусы решателя сохранены в подробных результатах;
допустимый план не объявляется доказанно самым ранним.</p>
<p>Нулевой объём в C5 означает известное отсутствие судов в сценарии. Неизвестные
параметры обрабатываются иначе — требуют подтверждения. Проверки не устанавливают
фактическую экономию и не заменяют верификацию оператором.</p>
<p><a href="protocol.json">Зафиксированные входы</a> · <a href="results.json">Итоги проверок</a> · <a href="comparison.csv">Таблица CSV</a></p></html>"""
    (folder / "report.html").write_text(page, encoding="utf-8")
    print(f"Отчёт: {folder / 'report.html'}")


if __name__ == "__main__":
    main()
