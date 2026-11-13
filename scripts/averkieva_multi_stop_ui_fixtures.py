"""Save real module outputs and explicitly labelled transport mocks for UI review."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yana_planner.multi_stop import plan_multi_stop

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/yana_planner/static/data/multi-stop-ui"


def run():
    cfg = json.loads((ROOT / "examples/multi_stop/four-recipients.json").read_text())
    cfg["receipt_priority"] = ["ust-kuyga", "kazachye", "batagay", "saydy-verkhoyansky"]
    cfg["cost_terms"] = json.loads(
        (ROOT / "examples/multi_stop/four-recipients.costs.json").read_text()
    )
    full = deepcopy(cfg)
    full["cost_terms"]["stop_fees_rub"] = {p["id"]: 1000 for p in cfg["stops"]}
    full["cost_terms"]["source"] += " Все сборы по 1000 ₽ также вымышлены."
    impossible = deepcopy(full)
    impossible["stops"][0]["deadline_h"] = 20
    OUT.mkdir(exist_ok=True)
    cases = {"success": full, "missing-costs": cfg, "infeasible": impossible}
    for ident, raw in cases.items():
        save(ident, dict(result=plan_multi_stop(raw)))
    with patch("yana_planner.multi_stop.milp", return_value=SimpleNamespace(status=1, x=None)):
        save("search-limit", dict(result=plan_multi_stop(full), solver_timeout_injected=True))
    for ident, code, message in [
        ("input-error", "validation_error", "В запросе отсутствует длительность третьего плеча."),
        ("timeout", "timeout", "Серверный лимит времени истёк. Введённые поля сохранены."),
    ]:
        save(ident, dict(error=dict(code=code, message=message)), transport_mock=True)
    (OUT / "form-input.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


def save(ident, payload, *, transport_mock=False):
    payload.update(
        fixture_id=ident,
        saved_example=True,
        provenance="Учебный сохранённый пример; не ответ на новый запрос формы.",
        transport_mock=transport_mock,
        base_core_commit="1c8a938",
    )
    (OUT / f"{ident}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    run()
