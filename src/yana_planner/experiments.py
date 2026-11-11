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
            weighted_mean_receipt_h=value.get("weighted_mean_receipt_h") if valid else None,
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
    delta = {}
    if same:
        for field in ["trip_count", "last_receipt_h"]:
            a, b = rows["READY"][field], rows["MILP"][field]
            delta[field] = a - b if a is not None and b is not None else None
        conclusion = f"При одинаковом объёме {rows['READY']['accepted_t']:g} т: "
        parts = []
        trips, hours = delta["trip_count"], delta["last_receipt_h"]
        if trips is not None:
            parts.append(
                f"у MILP рейсов на {abs(trips):g} {'меньше' if trips > 0 else 'больше'}"
                if trips
                else "число рейсов одинаково"
            )
        if hours is not None:
            parts.append(
                f"приёмка MILP завершена на {abs(hours):g} ч {'раньше' if hours > 0 else 'позже'}"
                if hours
                else "приёмка завершена одновременно"
            )
        conclusion += (
            "; ".join(parts) + "." if parts else "разница числа рейсов и времени не определена."
        )
    mean_a = rows["READY"]["weighted_mean_receipt_h"]
    mean_b = rows["MILP"]["weighted_mean_receipt_h"]
    tradeoff = "Среднее время приёмки взвешено по тоннам; оно не входит в цели оптимизации."
    if same and mean_a is not None and mean_b is not None:
        change = mean_b - mean_a
        if abs(change) > 1e-7:
            tradeoff += f" У MILP оно на {abs(change):.2f} ч {'позже' if change > 0 else 'раньше'}, чем у READY."
        else:
            tradeoff += " У обоих методов оно одинаково."
    tradeoff += (
        " Приоритеты MILP: больше принято топлива → меньше рейсов → раньше завершена приёмка."
    )
    search = (
        "Оптимальность подтверждена по всем этапам поиска"
        if optimal
        else "Найден допустимый план; оптимальность не подтверждена"
        if rows["MILP"]["valid"]
        else "Допустимый план MILP не подтверждён"
    )
    return dict(
        tradeoff=tradeoff,
        differences=delta,
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


def event_effects(result):
    """Describe revised-minus-original metrics per method, not a causal estimate."""
    if "initial" not in result:
        return {}
    output = {}
    for name, original_key, revised_key in [
        ("READY", "baseline", "revised_baseline"),
        ("MILP", "planner", "revised_planner"),
    ]:
        before = result["initial"][original_key]
        after = result[revised_key]["result"]
        valid = all(v.get("status") == "valid_simulation" for v in [before, after])
        metrics = {}
        for field in ["accepted_t", "trip_count", "last_receipt_h", "weighted_mean_receipt_h"]:
            a, b = before.get(field), after.get(field)
            metrics[field] = dict(
                before=a if valid else None,
                after=b if valid else None,
                change=b - a if valid and a is not None and b is not None else None,
            )
        same = (
            valid
            and before.get("accepted_t") is not None
            and before.get("accepted_t") == after.get("accepted_t")
        )
        warning = (
            ""
            if same
            else "Объёмы до и после различаются или не подтверждены: изменения времени и рейсов не означают улучшение при одинаковом объёме."
        )
        output[name] = dict(metrics=metrics, warning=warning)
    return output
