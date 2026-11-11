"""Small adapter over existing planners; no independent scheduling logic."""

import hashlib
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from .domain import InputError, Scenario
from .experiments import comparisons, event_effects
from .io import _finite, export_result, read_json, read_plan
from .planning import compare
from .replanning import run_replanning

ROOT = Path(__file__).resolve().parents[2]
NAMES = {"r03": "R03 — обычный расчёт", "delay": "R03 — задержка топлива"}


def calculate(scenario, deadline, *, max_deadline=492, time_limit=2):
    """Validate bounded hours, run existing core and retain complete inputs."""
    if scenario not in NAMES:
        raise InputError("Выберите R03 или R03 с задержкой")
    if (
        not isinstance(deadline, str)
        or not deadline.isascii()
        or not deadline.isdecimal()
        or len(deadline) > 3
        or not 0 < int(deadline) <= max_deadline
    ):
        raise InputError(f"Срок: целое число от 1 до {max_deadline} часов")
    deadline = int(deadline)
    cfg = Scenario.from_dict(read_json(ROOT / "examples/r03.json")).to_dict()
    if deadline > cfg["return_cutoff_h"]:
        raise InputError("Срок превышает горизонт учебного сценария (492 ч)")
    event = read_json(ROOT / "examples/arrival_delay.json") if scenario == "delay" else None
    reference = read_plan(ROOT / "examples/r03.reference.json") if deadline == 328 else None
    kwargs = dict(time_limit=time_limit, incumbent=reference)
    result = (
        run_replanning(cfg, event, deadline, **kwargs)
        if event
        else compare(cfg, deadline, **kwargs)
    )
    result["comparisons"] = comparisons(result)
    result["event_effects"] = event_effects(result)
    with TemporaryDirectory() as directory:
        export_result(directory, result)
        result["report_html"] = re.sub(
            r' · <a href="(?:comparison|schedule|events)\.csv">.*?</a>',
            "",
            (Path(directory) / "report.html").read_text(),
        )
    result["code_version"] = hashlib.sha256(
        b"".join(f.read_bytes() for f in sorted(Path(__file__).parent.glob("*.py")))
    ).hexdigest()
    inputs = dict(
        scenario=cfg,
        event=event,
        deadline_h=deadline,
        settings=dict(time_limit_per_stage_s=time_limit, reference=reference),
    )
    return inputs, _finite(result)
