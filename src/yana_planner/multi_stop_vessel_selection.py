"""Enumerate alternative vessels for one fixed-order multi-stop voyage."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .domain import InputError


def calculate_vessel_selection(payload):
    from .multi_stop_service import calculate_multi_stop_request

    allowed = {
        "schema_version",
        "scenario",
        "cost_terms",
        "search_seconds",
        "vessel_candidates",
        "selection_rule",
    }
    if set(payload) - allowed:
        raise InputError("Неподдерживаемые поля выбора судна")
    candidates = payload["vessel_candidates"]
    if not isinstance(candidates, list) or not 2 <= len(candidates) <= 4:
        raise InputError("Для выбора нужны 2–4 альтернативных судна")
    rule = payload.get("selection_rule", "earliest_receipts")
    if rule not in ("earliest_receipts", "lowest_cost"):
        raise InputError("Критерий: earliest_receipts или lowest_cost")
    if payload.get("cost_terms") is not None:
        raise InputError("При выборе судна задавайте ставки отдельно в каждом кандидате")
    scenario = payload.get("scenario")
    if not isinstance(scenario, dict) or not isinstance(scenario.get("stops"), list):
        raise InputError("Нужны общие условия рейса и получатели")
    # Bound aggregate solver work, leaving room under the existing process timeout.
    budget = 12 / (len(candidates) * (len(scenario["stops"]) + 1))
    results, snapshots, seen = [], [], set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != {"vessel", "cost_terms"}:
            raise InputError("Кандидат: нужны vessel и cost_terms (можно null)")
        vessel = candidate["vessel"]
        if not isinstance(vessel, dict) or not isinstance(vessel.get("id"), str):
            raise InputError("Кандидату нужен идентификатор судна")
        if vessel["id"] in seen:
            raise InputError("Идентификаторы судов-кандидатов должны различаться")
        seen.add(vessel["id"])
        single = {
            k: deepcopy(v)
            for k, v in payload.items()
            if k in {"schema_version", "scenario", "search_seconds"}
        }
        single["scenario"]["vessel"] = deepcopy(vessel)
        single["cost_terms"] = deepcopy(candidate["cost_terms"])
        inputs, result = calculate_multi_stop_request(single, stage_limit_override=budget)
        snapshots.append(inputs)
        results.append(result)
    feasible = [i for i, r in enumerate(results) if r["goal_met"]]

    def time_key(i):
        r = results[i]
        receipts = {d["stop_id"]: d["receipt_h"] for d in r["plan"]["deliveries"]}
        return (*[receipts[s] for s in r["receipt_priority"]], r["plan"]["ready_h"], i)

    def known_cost(i):
        return results[i].get("costs", {}).get("total_rub") is not None

    all_costs_known = all(known_cost(i) for i in feasible)
    horizons = {c["cost_terms"]["horizon_h"] for c in candidates if c["cost_terms"] is not None}
    if len(horizons) > 1:
        raise InputError("Для сравнения задайте одинаковый горизонт оплаты всем кандидатам")
    chosen = (
        min(
            feasible,
            key=(lambda i: (results[i]["costs"]["total_rub"], *time_key(i)))
            if rule == "lowest_cost" and all_costs_known
            else time_key,
        )
        if feasible
        else 0
    )
    result, inputs = deepcopy(results[chosen]), deepcopy(snapshots[chosen])
    unresolved = any(r["status"] == "no_feasible_solution_found" for r in results)
    if not feasible and unresolved:
        result["status"] = "no_feasible_solution_found"
        result["reason"] = (
            "Для части кандидатов поиск не завершён; общая невыполнимость не доказана"
        )
        result.pop("reason_code", None)
    status = "selected" if feasible else "no_plan_found" if unresolved else "all_infeasible"
    if feasible and rule == "lowest_cost" and not all_costs_known:
        status = "missing_cost_inputs"
    recommended = candidates[chosen]["vessel"]["id"] if status == "selected" else None
    selection = dict(
        rule=rule,
        status=status,
        recommended_vessel_id=recommended,
        displayed_vessel_id=candidates[chosen]["vessel"]["id"] if feasible else None,
        scope="one_alternative_vessel_one_voyage",
        selection_proven=status == "selected"
        and not unresolved
        and all(
            r["status"] == "infeasible" or (r["goal_met"] and r["lexicographic_optimal"])
            for r in results
        ),
        candidate_results=results,
    )
    result["vessel_selection"] = selection
    inputs["vessel_candidates"] = deepcopy(candidates)
    inputs["settings"].update(
        selection_rule=rule, selection_stage_limit_s=min(payload.get("search_seconds", 2), budget)
    )
    result["input_sha256"] = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    result["code_version"] = hashlib.sha256(
        (result["code_version"] + Path(__file__).read_text()).encode()
    ).hexdigest()
    return inputs, result
