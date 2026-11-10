"""Select verified plans and the actual conditions used for their cost phase."""

from __future__ import annotations

from copy import deepcopy

from .compat import normalize_result
from .domain import ConfirmationRequired, InputError, Scenario, integer, proposals
from .replanning import apply_event
from .validation import check_schedule


def _operating_input(cfg):
    normalized = Scenario.from_dict(cfg).to_dict()
    keys = [
        "mode",
        "target_t",
        "initial_source_t",
        "destination_capacity_t",
        "return_cutoff_h",
        "navigation_assumed",
        "reception_assumed",
        "fleet_assumed",
        "supply_assumed",
        "arrivals",
        "withdrawals",
        "closures",
        "payload_calendar",
        "vessels",
        "t0",
        "timezone",
    ]
    view = {key: deepcopy(normalized.get(key)) for key in keys}
    for vessel in view["vessels"]:
        vessel.setdefault("unavailable_intervals", [])
    return view


def _same_input(left, right):
    return _operating_input(left) == _operating_input(right)


def _verdict(value, label):
    if not isinstance(value, dict) or value.get("status") != "valid_simulation":
        raise InputError(f"{label}: экономика требует status=valid_simulation")
    if not isinstance(value.get("trips"), list):
        raise InputError(f"{label}: отсутствует список trips")
    return value


def _updated(section):
    if not isinstance(section, dict) or section.get("updated_input") is None:
        raise ConfirmationRequired("Для фазы после события нужен updated_input обоих методов")
    return Scenario.from_dict(section["updated_input"]).to_dict()


def select_economic_comparison(cfg: dict, result: dict, deadline: int, *, phase=None):
    """Return effective input, two plans and provenance, without changing inputs.

    The caller may supply the original scenario or the actual updated scenario.
    Old PR #1 results are adapted, but missing original input is never guessed.
    """
    supplied = Scenario.from_dict(cfg).to_dict()
    result = normalize_result(result)
    deadline = integer(deadline, "deadline_h")
    if (
        result.get("deadline_h") is not None
        and integer(result["deadline_h"], "result.deadline_h") != deadline
    ):
        raise InputError("Срок экономики должен совпадать со сроком сравнения расписаний")
    online = "initial" in result or any(
        k in result for k in ["revised_baseline", "revised_planner"]
    )
    if online:
        selected = phase or "after"
        if selected not in ("before", "after"):
            raise InputError("Фаза должна быть before или after")
        if selected == "after":
            a, b = result.get("revised_baseline"), result.get("revised_planner")
            left = _verdict(a.get("result") if isinstance(a, dict) else None, "READY")
            right = _verdict(b.get("result") if isinstance(b, dict) else None, "MILP")
            effective, other = _updated(a), _updated(b)
            if not _same_input(effective, other):
                raise InputError("Методы рассчитаны при разных updated_input")
            if result.get("initial_input") is not None:
                original = Scenario.from_dict(result["initial_input"]).to_dict()
                if not _same_input(apply_event(original, result.get("event")), effective):
                    raise InputError("updated_input не соответствует исходному входу и событию")
            if not _same_input(supplied, effective):
                if not _same_input(apply_event(supplied, result.get("event")), effective):
                    raise InputError("Переданный вход не соответствует условиям сравнения")
        else:
            initial = result.get("initial")
            if not isinstance(initial, dict):
                raise InputError("Для фазы before нужен initial")
            left = _verdict(initial.get("baseline"), "READY")
            right = _verdict(initial.get("planner"), "MILP")
            if result.get("initial_input") is not None:
                effective = Scenario.from_dict(result["initial_input"]).to_dict()
                if not _same_input(supplied, effective):
                    if not _same_input(supplied, _updated(result.get("revised_baseline"))):
                        raise InputError("Переданный вход не относится к выбранному сравнению")
            else:
                after = _updated(result.get("revised_baseline"))
                if _same_input(supplied, after):
                    raise ConfirmationRequired(
                        "Неизвестен исходный вход before; не восстанавливаем его из after"
                    )
                if not _same_input(apply_event(supplied, result.get("event")), after):
                    raise InputError("Нельзя подтвердить исходный вход выбранной фазы")
                effective = supplied
    else:
        if phase is not None:
            raise InputError("Фазы before/after применяются к результату перепланирования")
        selected, effective = "static", supplied
        left = _verdict(result.get("baseline"), "READY")
        right = _verdict(result.get("planner"), "MILP")

    plans, accepted = [], []
    for label, value in [("READY", left), ("MILP", right)]:
        plan = proposals(value["trips"])
        checked = check_schedule(effective, plan, deadline)
        if checked["status"] != "valid_simulation":
            raise InputError(f"{label}: рейсы недопустимы при условиях выбранной фазы")
        if integer(value.get("accepted_t"), f"{label}.accepted_t") != checked["accepted_t"]:
            raise InputError(f"{label}: сохранённый тоннаж не совпадает с проверкой рейсов")
        plans.append(plan)
        accepted.append(checked["accepted_t"])
    if accepted[0] != accepted[1]:
        raise InputError("Экономическое сравнение требует одинакового доставленного объёма")
    return (
        effective,
        plans[0],
        plans[1],
        dict(
            phase=selected,
            scenario_id=effective["id"],
            deadline_h=deadline,
            evaluated_input_sha256=Scenario.from_dict(effective).fingerprint,
            accepted_t=accepted[0],
            target_t=effective["target_t"],
            target_met=accepted[0] == effective["target_t"],
            source_event=deepcopy(result.get("source_event", result.get("event"))),
        ),
    )
