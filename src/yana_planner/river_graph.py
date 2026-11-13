"""Small river-network router. Distances do not imply vessel admissibility or time."""

import json
from copy import deepcopy
from heapq import heappop, heappush
from math import isfinite
from pathlib import Path

from .domain import InputError

NETWORK_FILE = Path(__file__).parent / "static/data/river-network.demo.json"


def load_network():
    return json.loads(NETWORK_FILE.read_text(encoding="utf-8"))


def validate_network(raw):
    if not isinstance(raw, dict) or raw.get("network_version") != "yana-river/1":
        raise InputError("Сеть: ожидается yana-river/1")
    data = deepcopy(raw)
    for key in ("id", "label"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise InputError(f"Сеть: нужен {key}")
    if not isinstance(data.get("nodes"), list) or not 1 <= len(data["nodes"]) <= 500:
        raise InputError("Сеть: требуется от 1 до 500 пунктов")
    nodes = set()
    for node in data["nodes"]:
        if not isinstance(node, dict) or any(
            not isinstance(node.get(k), str) or not node[k].strip() for k in ("id", "name")
        ):
            raise InputError("Пункт: нужны id и name")
        if node["id"] in nodes:
            raise InputError("Пункт: повторный id")
        nodes.add(node["id"])
    if not isinstance(data.get("edges"), list) or len(data["edges"]) > 2000:
        raise InputError("Сеть: допускается до 2000 участков")
    edge_ids = set()
    for edge in data["edges"]:
        if not isinstance(edge, dict):
            raise InputError("Участок: нужен объект")
        ident = edge.get("id")
        if not isinstance(ident, str) or not ident.strip() or ident in edge_ids:
            raise InputError("Участок: нужен уникальный id")
        edge_ids.add(ident)
        if any(not isinstance(edge.get(k), str) or edge[k] not in nodes for k in ("from", "to")):
            raise InputError("Участок: неизвестный пункт")
        if edge["from"] == edge["to"]:
            raise InputError("Участок: начало совпадает с концом")
        length = edge.get("length_km")
        if (
            isinstance(length, bool)
            or not isinstance(length, (int, float))
            or not isfinite(length)
            or length <= 0
        ):
            raise InputError("Участок: length_km должен быть положительным конечным числом")
        if not isinstance(edge.get("bidirectional"), bool):
            raise InputError("Участок: явно задайте bidirectional")
        if edge.get("status") not in ("documented", "scenario_assumption"):
            raise InputError("Участок: нужен статус расстояния")
        if not isinstance(edge.get("source"), str) or not edge["source"].strip():
            raise InputError("Участок: нужен источник или описание тестовых данных")
    return data


def shortest_path(network, origin, destination):
    """Dijkstra over explicit directed/bidirectional edges; never geodesic distance."""
    data = validate_network(network)
    names = {n["id"]: n["name"] for n in data["nodes"]}
    if not isinstance(origin, str) or origin not in names:
        raise InputError("Выберите пункт отправления из сети")
    if not isinstance(destination, str) or destination not in names:
        raise InputError("Выберите пункт назначения из сети")
    if origin == destination:
        raise InputError("Выберите разные пункты")
    adjacency = {ident: [] for ident in names}
    for edge in data["edges"]:
        adjacency[edge["from"]].append((edge["to"], edge))
        if edge["bidirectional"]:
            adjacency[edge["to"]].append((edge["from"], edge))
    distances, previous, queue = {origin: 0}, {}, [(0, origin)]
    while queue:
        distance, node = heappop(queue)
        if distance != distances[node]:
            continue
        if node == destination:
            break
        for neighbour, edge in adjacency[node]:
            candidate = distance + edge["length_km"]
            if candidate < distances.get(neighbour, float("inf")):
                distances[neighbour] = candidate
                previous[neighbour] = (node, edge)
                heappush(queue, (candidate, neighbour))
    if destination not in distances:
        return {"status": "no_path", "origin_id": origin, "destination_id": destination}
    segments, node_ids = [], [destination]
    cursor = destination
    while cursor != origin:
        parent, edge = previous[cursor]
        segments.append({**edge, "from": parent, "to": cursor})
        node_ids.append(parent)
        cursor = parent
    segments.reverse()
    node_ids.reverse()
    return {
        "status": "found",
        "network_id": data["id"],
        "origin_id": origin,
        "destination_id": destination,
        "origin": names[origin],
        "destination": names[destination],
        "nodes": [{"id": ident, "name": names[ident]} for ident in node_ids],
        "segments": segments,
        "length_km": distances[destination],
        "distance_status": "documented"
        if all(e["status"] == "documented" for e in segments)
        else "scenario_assumption",
        "navigation_status": "requires_confirmation",
        "network_snapshot": data,
    }


def validate_route_graph(route):
    """Check attached path against its saved network; keep historical runs reproducible."""
    graph = route.get("graph_path")
    if graph is None:
        return
    if not isinstance(graph, dict):
        raise InputError("Маршрут графа: нужен объект")
    expected = shortest_path(
        graph.get("network_snapshot"), graph.get("origin_id"), graph.get("destination_id")
    )
    if expected["status"] != "found" or graph != expected:
        raise InputError("Маршрут графа не соответствует сохранённой сети")
    if any(route.get(key) != expected[key] for key in ("origin", "destination", "length_km")):
        raise InputError("Пункты или расстояние маршрута не совпадают с графом")
