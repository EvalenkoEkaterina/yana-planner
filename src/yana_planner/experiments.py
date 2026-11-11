"""Shared interpretation of independently validated results (hours and tonnes)."""


def summarize(ready, milp, target_t, solver):
    """Compare verified plans without equating different delivered volumes."""
    rows = {}
    for name, value in [("READY", ready), ("MILP", milp)]:
        valid = value.get("status") == "valid_simulation"
        accepted = value.get("accepted_t") if valid else None
        rows[name] = dict(
            accepted_t=accepted,
            shortfall_t=value.get("shortfall_t") if valid else None,
            trip_count=value.get("trip_count") if valid else None,
            last_receipt_h=value.get("last_receipt_h") if valid else None,
            valid=valid,
            target_met=valid and accepted is not None and accepted >= target_t,
        )
    optimal = bool(solver) and all(x.get("optimal") is True for x in solver)
    valid = all(x["valid"] for x in rows.values())
    same = (
        valid
        and rows["READY"]["accepted_t"] is not None
        and rows["READY"]["accepted_t"] == rows["MILP"]["accepted_t"]
    )
    warnings = []
    if not valid:
        warnings.append("Есть недопустимое расписание; показатели такого плана не подтверждены.")
    if not all(x["target_met"] for x in rows.values()):
        warnings.append("Целевой объём выполнен не обоими методами.")
    if valid and not same:
        warnings.append(
            "Объёмы доставки различаются; число рейсов и время несопоставимы как улучшение."
        )
    conclusion = (
        "Одинаковый принятый объём: можно сравнить число рейсов и завершение приёмки."
        if same
        else "Сопоставимость объёмов не подтверждена."
    )
    search = (
        "Оптимальность подтверждена по всем этапам поиска"
        if optimal
        else "Найден допустимый план; оптимальность не подтверждена"
        if rows["MILP"]["valid"]
        else "Допустимый план MILP не подтверждён"
    )
    return dict(
        rows=rows,
        equal_volume=same,
        optimal=optimal,
        search_status=search,
        conclusion=conclusion,
        warnings=warnings,
        target_t=target_t,
    )


def comparisons(result):
    """Return before/after interpretations using the same comparison rules."""
    if "initial" in result:
        return {
            "До события": comparisons(result["initial"])["Расчёт"],
            "После события": summarize(
                result["revised_baseline"]["result"],
                result["revised_planner"]["result"],
                result["target_t"],
                result["revised_planner"]["solver"],
            ),
        }
    return {
        "Расчёт": summarize(
            result["baseline"], result["planner"], result["target_t"], result["solver"]
        )
    }
