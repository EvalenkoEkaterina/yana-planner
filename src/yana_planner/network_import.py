"""Adapt the team's reference network without inferring operational parameters."""

from copy import deepcopy

from .domain import InputError
from .river_graph import validate_network


def import_yana_network(raw):
    if not isinstance(raw, dict) or raw.get("schema_version") != "yana-network/1-proposal":
        raise InputError("Ожидается yana-network/1-proposal")
    if raw.get("distance_unit") != "km":
        raise InputError("Расстояния сети должны быть в километрах")
    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        raise InputError("Нужен реестр источников сети")
    by_id = {}
    for source in sources:
        if not isinstance(source, dict) or any(
            not isinstance(source.get(k), str) or not source[k].strip()
            for k in ("id", "title", "url", "accessed_at")
        ):
            raise InputError("Неполный источник сети")
        if source["id"] in by_id or not source["url"].startswith("https://"):
            raise InputError("Нужны уникальные источники HTTPS")
        by_id[source["id"]] = source
    if not isinstance(raw.get("nodes"), list) or not isinstance(raw.get("edges"), list):
        raise InputError("Нужны nodes и edges")
    nodes = []
    for node in raw["nodes"]:
        if not isinstance(node, dict):
            raise InputError("Пункт: нужен объект")
        n = deepcopy(node)
        n["routing_role"] = (
            "settlement_candidate" if n.get("type") == "settlement" else "waypoint_only"
        )
        n["terminal_status"] = "requires_confirmation"
        # These registry extracts have not established a CRS or verified berths.
        n["weather_coordinate_usable"] = False
        nodes.append(n)
    edges = []
    for edge in raw["edges"]:
        if not isinstance(edge, dict):
            raise InputError("Участок: нужен объект")
        source_id = edge.get("source_id")
        if source_id not in by_id:
            raise InputError("Источник участка не найден")
        if edge.get("distance_status") != "documented_in_initial_2026_plan":
            raise InputError("Ожидается расстояние из исходного плана 2026")
        if edge.get("topology") != "physical_section_not_one_way_rule":
            raise InputError("Не установлена физическая связь участка")
        edges.append(
            dict(
                id=edge.get("id"),
                **{"from": edge.get("from_id"), "to": edge.get("to_id")},
                length_km=edge.get("distance_km"),
                bidirectional=True,
                status="documented",
                source=by_id[source_id]["url"],
                source_id=source_id,
                source_row=edge.get("source_row"),
                reference=deepcopy(edge),
                navigation_status="requires_confirmation",
            )
        )
    return validate_network(
        dict(
            network_version="yana-river/1",
            id="yana-reference-2026-v1",
            label="Яна · расстояния из исходного плана 2026",
            nodes=nodes,
            edges=edges,
            sources=deepcopy(sources),
            checked_at=raw.get("checked_at"),
            topology_note="Цепочка без альтернативных ветвей. Расстояния — суммы участков, не точная геометрия русла.",
            navigation_status="requires_confirmation",
            coordinate_note="Координаты предварительные; для автоматической погодной привязки не используются.",
        )
    )
