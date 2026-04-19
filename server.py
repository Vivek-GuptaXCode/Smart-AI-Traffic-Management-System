"""
V2X Central Server
------------------
Runs on laptop (or Raspberry Pi later).

RSU graph:
  - Nodes  = RSU junction IDs (e.g. "C", "E", "M1" ...)
  - Edges  = road-level connectivity between RSUs

Events (SocketIO):
  Client → Server : rsu_register      { "nodes": [...], "edges": [[u,v], ...] }
  Client → Server : congestion_alert  { "from_rsu": "C", "score": 0.75, "metrics": {...} }
  Server → All    : congestion_broadcast  (same payload, minus sender)

HTTP:
  GET /graph   → JSON snapshot of the RSU graph
  GET /status  → JSON list of known congestion events
"""

from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit
try:
    from flask_cors import CORS
except ModuleNotFoundError:
    def CORS(*_args, **_kwargs):
        return None
import networkx as nx
from datetime import datetime, timezone, timedelta
import threading
import os
import sqlite3
import re
from pathlib import Path
import time
import uuid

# ─── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
app.config["SECRET_KEY"] = "v2x-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ─── RSU Graph (server-side) ──────────────────────────────────────────────────
rsu_graph = nx.Graph()

def _preload_rsu_graph():
    config_path = Path("data/rsu_config_kolkata.json")
    if config_path.exists():
        import json
        import math
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                rsu_list = config.get("rsus", [])

                # Load nodes
                for rsu in rsu_list:
                    rsu_graph.add_node(rsu["id"], x=rsu.get("x"), y=rsu.get("y"), display_name=rsu.get("display_name"))

                # Prefer explicit graph edges from config to keep topology static.
                raw_edges = config.get("graph_edges")
                fixed_edge_count = 0
                if isinstance(raw_edges, list):
                    known_ids = {
                        str(rsu.get("id", "")).strip()
                        for rsu in rsu_list
                        if str(rsu.get("id", "")).strip()
                    }

                    for raw_edge in raw_edges:
                        if isinstance(raw_edge, (list, tuple)) and len(raw_edge) >= 2:
                            u = str(raw_edge[0]).strip()
                            v = str(raw_edge[1]).strip()
                        elif isinstance(raw_edge, dict):
                            u = str(raw_edge.get("from", raw_edge.get("source", raw_edge.get("u", "")))).strip()
                            v = str(raw_edge.get("to", raw_edge.get("target", raw_edge.get("v", "")))).strip()
                        else:
                            continue

                        if u and v and u != v and u in known_ids and v in known_ids:
                            rsu_graph.add_edge(u, v)
                            fixed_edge_count += 1

                # Backward-compatible fallback when fixed graph edges are not provided.
                if fixed_edge_count == 0:
                    for rsuA in rsu_list:
                        xA, yA = rsuA.get("x"), rsuA.get("y")
                        if xA is None or yA is None:
                            continue

                        neighbors = []
                        for rsuB in rsu_list:
                            if rsuA["id"] == rsuB["id"]:
                                continue
                            xB, yB = rsuB.get("x"), rsuB.get("y")
                            if xB is None or yB is None:
                                continue

                            dist = math.sqrt((xA - xB)**2 + (yA - yB)**2)
                            neighbors.append((dist, rsuB["id"]))

                        neighbors.sort()
                        # Connect to 3 nearest neighbors to match prior behaviour.
                        for _, idB in neighbors[:3]:
                            rsu_graph.add_edge(rsuA["id"], idB)

        except Exception as e:
            print(f"Failed to preload config: {e}")

# Always preload RSU graph from config so the dashboard works before SUMO connects.
# SUMO will override with live topology (including coordinates) when it registers.
_preload_rsu_graph()

# sid → rsu_node mapping for connected simulator clients
connected_clients = {}   # sid → "simulator" (one simulator client for now)

# Congestion event log
congestion_log = []      # list of dicts
log_lock = threading.Lock()
congestion_state_lock = threading.Lock()
rsu_congestion_state = {}  # rsu_id -> bool

# Active green-corridor policies keyed by corridor_id.
green_corridor_lock = threading.Lock()
active_green_corridors = {}

_forecast_engine = None
_forecast_engine_error = None
_route_audit_logger = None
_route_audit_logger_error = None
_gnn_reroute_engine = None
_gnn_reroute_engine_error = None
_rsu_display_name_map = None

# ─── Helpers ──────────────────────────────────────────────────────────────────
def ts():
    return datetime.now(timezone.utc).isoformat()


def log(msg):
    print(f"[{ts()}] {msg}", flush=True)


# ─── Database Persistence ─────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "data" / "mission_control.db"

def db_init():
    """Initialize the SQLite database and create tables if they do not exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                rsu_id TEXT,
                message TEXT,
                vehicle_count INTEGER,
                avg_wait INTEGER,
                timestamp TEXT NOT NULL
            )
        """)
        # Index on type and rsu_id for faster hotspot analysis
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_type ON events(type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_rsu ON events(rsu_id)")
        conn.commit()
    log(f"[DB] Initialized persistent storage: {DB_PATH}")


def db_reset_for_fresh_start():
    """Clear persisted runtime event history so every server start begins fresh."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM events")
            cursor.execute("DELETE FROM sqlite_sequence WHERE name = 'events'")
            conn.commit()
        log("[DB] Cleared archived events for fresh startup")
    except Exception as e:
        log(f"[DB_ERROR] Failed to clear archived events on startup: {e}")

def db_log_event(event_type, rsu_id=None, message=None, vehicle_count=None, avg_wait=None):
    """Archive an event into the persistent database."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO events (type, rsu_id, message, vehicle_count, avg_wait, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (event_type, rsu_id, message, vehicle_count, avg_wait, ts()))
            conn.commit()
    except Exception as e:
        log(f"[DB_ERROR] Failed to archive event: {e}")


# Ensure DB schema exists whenever the module is loaded (not only in __main__)
db_init()


def _is_truthy_env(name: str) -> bool:
    value = str(os.getenv(name, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _is_forecast_artifact_enabled() -> bool:
    return _is_truthy_env("HYBRID_ENABLE_FORECAST_MODEL")


def _is_phase3_routing_enabled() -> bool:
    return _is_truthy_env("HYBRID_ENABLE_PHASE3_ROUTING")


def _is_gnn_routing_enabled() -> bool:
    return _is_truthy_env("HYBRID_ENABLE_GNN_ROUTING")


def _load_forecast_engine():
    global _forecast_engine, _forecast_engine_error
    if _forecast_engine is not None or _forecast_engine_error is not None:
        return _forecast_engine

    artifact_path = os.getenv(
        "HYBRID_FORECAST_ARTIFACT",
        "models/forecast/artifacts/latest/forecast_artifact.json",
    )
    try:
        from models.forecast.inference import ForecastInferenceEngine

        _forecast_engine = ForecastInferenceEngine.from_artifact_path(artifact_path)
        log(f"[FORECAST] Loaded inference artifact: {artifact_path}")
    except Exception as exc:
        _forecast_engine_error = str(exc)
        log(f"[FORECAST] Artifact unavailable, fallback to deterministic stub: {exc}")
    return _forecast_engine


def _load_gnn_reroute_engine():
    global _gnn_reroute_engine, _gnn_reroute_engine_error
    if _gnn_reroute_engine is not None or _gnn_reroute_engine_error is not None:
        return _gnn_reroute_engine

    try:
        from routing.gnn_reroute_engine import GNNRerouteConfig, GNNRerouteEngine

        _gnn_reroute_engine = GNNRerouteEngine(config=GNNRerouteConfig.from_env())
        log("[GNN] Graph reroute engine enabled: routing.gnn_reroute_engine")
    except Exception as exc:
        _gnn_reroute_engine_error = str(exc)
        log(f"[GNN] Graph reroute engine unavailable, fallback to current policy: {exc}")
    return _gnn_reroute_engine


def _load_route_audit_logger():
    global _route_audit_logger, _route_audit_logger_error
    if _route_audit_logger is not None or _route_audit_logger_error is not None:
        return _route_audit_logger

    output_path = Path(
        os.getenv(
            "HYBRID_ROUTE_AUDIT_PATH",
            "data/raw/route_audit/route_decisions.jsonl",
        )
    )
    try:
        from routing.route_audit_logger import RouteAuditLogger

        _route_audit_logger = RouteAuditLogger(output_path)
        log(f"[PHASE3] Route audit logger enabled: {output_path}")
    except Exception as exc:
        _route_audit_logger_error = str(exc)
        log(f"[PHASE3] Route audit logger unavailable: {exc}")
    return _route_audit_logger


def _validate_optional_forecast_payload(payload_value):
    if payload_value is None:
        return {}, []

    if not isinstance(payload_value, dict):
        return {}, ["forecast must be an object when provided"]

    errors = []
    normalized = {}
    for key in ("p_congestion", "confidence", "uncertainty"):
        if key not in payload_value:
            continue
        raw_value = payload_value.get(key)
        try:
            parsed_value = float(raw_value)
        except (TypeError, ValueError):
            errors.append(f"forecast.{key} must be numeric")
            continue
        if parsed_value < 0.0 or parsed_value > 1.0:
            errors.append(f"forecast.{key} must be in [0, 1]")
            continue
        normalized[key] = parsed_value

    if "model" in payload_value and payload_value.get("model") is not None:
        normalized["model"] = str(payload_value.get("model"))

    return normalized, errors


def _now_epoch_seconds() -> float:
    return time.time()


def _iso_utc(epoch_seconds: float) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _unique_str_list(raw_values) -> list[str]:
    out = []
    seen = set()
    for raw in raw_values:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        out.append(value)
        seen.add(value)
    return out


def _prune_expired_green_corridors_locked(now_epoch: float) -> None:
    for corridor_id, corridor in list(active_green_corridors.items()):
        try:
            expires_at_epoch = float(corridor.get("expires_at_epoch", 0.0))
        except (TypeError, ValueError):
            expires_at_epoch = 0.0
        if expires_at_epoch <= now_epoch:
            active_green_corridors.pop(corridor_id, None)


def _build_corridor_scope(anchor_rsu_id: str, radius_hops: int) -> list[str]:
    if anchor_rsu_id not in rsu_graph:
        return []

    safe_radius = max(0, int(radius_hops))
    if safe_radius == 0:
        return [anchor_rsu_id]

    try:
        reach = nx.single_source_shortest_path_length(
            rsu_graph,
            source=anchor_rsu_id,
            cutoff=safe_radius,
        )
        scoped_rsus = sorted(str(node_id) for node_id in reach.keys())
        return scoped_rsus or [anchor_rsu_id]
    except Exception:
        return [anchor_rsu_id]


def _build_corridor_path_scope(source_rsu_id: str, destination_rsu_id: str, avoid_edges: list[tuple[str, str]] | None = None) -> list[str]:
    source = str(source_rsu_id).strip()
    destination = str(destination_rsu_id).strip()
    if not source or not destination:
        return []
    if source not in rsu_graph or destination not in rsu_graph:
        return []
    if source == destination:
        return [source]

    try:
        if avoid_edges:
            # Build a subgraph excluding the avoided edges to find an alternative path
            g = rsu_graph.copy()
            for u, v in avoid_edges:
                if g.has_edge(u, v):
                    g.remove_edge(u, v)
            try:
                path = nx.shortest_path(g, source=source, target=destination)
                return [str(node_id) for node_id in path if str(node_id).strip()]
            except nx.NetworkXNoPath:
                # No alternative exists — fall back to the original graph
                pass
        path = nx.shortest_path(rsu_graph, source=source, target=destination)
        return [str(node_id) for node_id in path if str(node_id).strip()]
    except Exception:
        return []


def _to_public_green_corridor(corridor: dict, now_epoch: float) -> dict:
    expires_at_epoch = float(corridor.get("expires_at_epoch", now_epoch))
    remaining_seconds = max(0.0, expires_at_epoch - now_epoch)
    return {
        "corridor_id": str(corridor.get("corridor_id", "")),
        "anchor_rsu_id": str(corridor.get("anchor_rsu_id", "")),
        "source_rsu_id": str(corridor.get("source_rsu_id", "")),
        "destination_rsu_id": str(corridor.get("destination_rsu_id", "")),
        "rsu_ids": [str(r) for r in corridor.get("rsu_ids", [])],
        "radius_hops": int(corridor.get("radius_hops", 0)),
        "hold_seconds": float(corridor.get("hold_seconds", 0.0)),
        "created_at": str(corridor.get("created_at", "")),
        "expires_at": str(corridor.get("expires_at", "")),
        "remaining_seconds": round(remaining_seconds, 3),
        "reason": str(corridor.get("reason", "manual_override")),
        "created_by": str(corridor.get("created_by", "dashboard")),
        "persistent": bool(corridor.get("persistent", False)),
        "strategy": str(corridor.get("strategy", "rsu_hop_expansion_v1")),
        "emergency_vehicle_ids": [
            str(v) for v in corridor.get("emergency_vehicle_ids", [])
        ],
    }


def _list_active_green_corridors(now_epoch: float | None = None) -> list[dict]:
    now = _now_epoch_seconds() if now_epoch is None else float(now_epoch)
    with green_corridor_lock:
        _prune_expired_green_corridors_locked(now)
        corridors = [_to_public_green_corridor(c, now) for c in active_green_corridors.values()]
    corridors.sort(key=lambda item: item.get("expires_at", ""))
    return corridors


def _active_green_corridors_for_rsu(rsu_id: str, now_epoch: float | None = None) -> list[dict]:
    now = _now_epoch_seconds() if now_epoch is None else float(now_epoch)
    with green_corridor_lock:
        _prune_expired_green_corridors_locked(now)
        corridors = []
        for corridor in active_green_corridors.values():
            rsu_ids = [str(r) for r in corridor.get("rsu_ids", [])]
            if rsu_id in rsu_ids:
                corridors.append(_to_public_green_corridor(corridor, now))
    corridors.sort(key=lambda item: item.get("expires_at", ""))
    return corridors


def _append_system_event(event: dict) -> None:
    with log_lock:
        congestion_log.append(event)

    # Persistent archive
    db_log_event(
        event_type=event.get("type", "system"),
        rsu_id=event.get("rsu_id", "SYSTEM"),
        message=event.get("message", ""),
    )


def _sync_rsu_congestion_state(
    rsu_id: str,
    *,
    vehicle_count: int,
    avg_speed_mps: float,
    force_congested: bool = False,
) -> None:
    """Emit congestion transition events per RSU for dashboard consumers.

    A transition from clear->congested emits junction_broadcast, while
    congested->clear emits junction_clear_broadcast.
    """
    rsu_key = str(rsu_id).strip()
    if not rsu_key or rsu_key == "global_stub":
        return

    # Keep dashboard semantics aligned with runtime logger thresholds.
    is_congested = bool(force_congested) or (int(vehicle_count) >= 5 and float(avg_speed_mps) <= 5.0)

    with congestion_state_lock:
        was_congested = bool(rsu_congestion_state.get(rsu_key, False))
        if was_congested == is_congested:
            return
        rsu_congestion_state[rsu_key] = is_congested

    event_ts = ts()
    if is_congested:
        avg_wait = max(0, int(round((5.0 - float(avg_speed_mps)) * 12)))
        event = {
            "type": "congestion",
            "from_rsu": rsu_key,
            "vehicle_count": int(vehicle_count),
            "avg_wait": avg_wait,
            "timestamp": event_ts,
        }
        with log_lock:
            congestion_log.append(event)

        socketio.emit(
            "junction_broadcast",
            {
                "from_rsu": rsu_key,
                "vehicle_count": int(vehicle_count),
                "avg_wait": avg_wait,
                "timestamp": event_ts,
            },
        )
        # Persistent archive
        db_log_event(
            event_type="congestion",
            rsu_id=rsu_key,
            vehicle_count=int(vehicle_count),
            avg_wait=avg_wait
        )
    else:
        event = {
            "type": "clear",
            "from_rsu": rsu_key,
            "timestamp": event_ts,
        }
        with log_lock:
            congestion_log.append(event)

        socketio.emit(
            "junction_clear_broadcast",
            {
                "from_rsu": rsu_key,
                "timestamp": event_ts,
            },
        )
        # Persistent archive
        db_log_event(
            event_type="clear",
            rsu_id=rsu_key
        )


def _sync_rsu_congestion_snapshot(
    rsu_batch_metrics: list[dict],
    forced_congested_rsus: list[str],
) -> None:
    """Synchronize RSU congestion transitions from a full per-RSU snapshot."""
    forced_set = {
        str(rsu_id).strip()
        for rsu_id in forced_congested_rsus
        if str(rsu_id).strip() and str(rsu_id).strip() != "global_stub"
    }

    metric_by_rsu: dict[str, dict[str, float | int]] = {}
    for row in rsu_batch_metrics:
        if not isinstance(row, dict):
            continue
        rsu_key = str(row.get("rsu_id", "")).strip()
        if not rsu_key or rsu_key == "global_stub":
            continue

        try:
            count = int(row.get("vehicle_count", 0))
        except (TypeError, ValueError):
            count = 0

        try:
            speed = float(row.get("avg_speed_mps", 13.89))
        except (TypeError, ValueError):
            speed = 13.89

        metric_by_rsu[rsu_key] = {
            "vehicle_count": max(0, count),
            "avg_speed_mps": max(0.0, speed),
        }

    with congestion_state_lock:
        known_rsus = set(rsu_congestion_state.keys())

    target_rsus = set(metric_by_rsu.keys()) | forced_set | known_rsus
    for rsu_key in sorted(target_rsus):
        metrics = metric_by_rsu.get(rsu_key)
        if metrics is None:
            vehicle_count = 0
            avg_speed_mps = 13.89
        else:
            vehicle_count = int(metrics.get("vehicle_count", 0))
            avg_speed_mps = float(metrics.get("avg_speed_mps", 13.89))

        _sync_rsu_congestion_state(
            rsu_key,
            vehicle_count=vehicle_count,
            avg_speed_mps=avg_speed_mps,
            force_congested=rsu_key in forced_set,
        )


def _apply_green_corridor_override(response: dict, rsu_id: str) -> None:
    all_active_corridors = _list_active_green_corridors()
    if all_active_corridors:
        response["green_corridor_global"] = {
            "active": True,
            "active_count": len(all_active_corridors),
            "corridors": all_active_corridors,
            "rsu_ids": _unique_str_list(
                rsu
                for corridor in all_active_corridors
                for rsu in corridor.get("rsu_ids", [])
            ),
        }

    active_for_rsu = _active_green_corridors_for_rsu(rsu_id)
    if not active_for_rsu:
        return

    corridor_ids = [str(c["corridor_id"]) for c in active_for_rsu]
    anchor_rsus = _unique_str_list(c.get("anchor_rsu_id", "") for c in active_for_rsu)
    scoped_rsus = _unique_str_list(r for c in active_for_rsu for r in c.get("rsu_ids", []))
    emergency_vehicle_ids = _unique_str_list(
        v for c in active_for_rsu for v in c.get("emergency_vehicle_ids", [])
    )
    max_remaining_seconds = max(
        float(c.get("remaining_seconds", 0.0)) for c in active_for_rsu
    )

    recommended_action = response.get("recommended_action")
    if not isinstance(recommended_action, dict):
        recommended_action = {}

    existing_fraction = recommended_action.get("reroute_fraction", 0.0)
    try:
        existing_fraction = float(existing_fraction)
    except (TypeError, ValueError):
        existing_fraction = 0.0

    recommended_action.update({
        "signal_priority": "green_corridor",
        "reroute_enabled": True,
        "reroute_mode": "dijkstra" if emergency_vehicle_ids else str(recommended_action.get("reroute_mode", "gnn_effort")),
        "reroute_fraction": 1.0 if emergency_vehicle_ids else max(existing_fraction, 0.35),
        "min_confidence": 0.0,
        "green_corridor_active": True,
        "green_corridor_ids": corridor_ids,
        "green_corridor_rsu_ids": scoped_rsus,
    })
    response["recommended_action"] = recommended_action
    response["risk_level"] = "high"

    emergency_action = response.get("emergency_action")
    if not isinstance(emergency_action, dict):
        emergency_action = {}

    existing_emergency_ids = emergency_action.get("vehicle_ids", [])
    if not isinstance(existing_emergency_ids, list):
        existing_emergency_ids = []
    merged_emergency_ids = _unique_str_list([*existing_emergency_ids, *emergency_vehicle_ids])
    emergency_active = bool(emergency_action.get("active", False)) or bool(merged_emergency_ids)

    emergency_action.update({
        "active": emergency_active,
        "vehicle_ids": merged_emergency_ids,
        "strategy": "optimal_route_plus_corridor_preemption" if emergency_active else "none",
        "traffic_control": "stop_non_emergency_on_corridor" if emergency_active else "normal_hybrid_control",
    })
    response["emergency_action"] = emergency_action

    route_directives = response.get("route_directives", [])
    if not isinstance(route_directives, list):
        route_directives = []

    existing_route_directive_ids = {
        str(item.get("vehicle_id", ""))
        for item in route_directives
        if isinstance(item, dict)
    }
    for vehicle_id in merged_emergency_ids:
        if vehicle_id in existing_route_directive_ids:
            continue
        route_directives.append({
            "vehicle_id": vehicle_id,
            "mode": "dijkstra",
            "reason": "green_corridor",
        })
    if route_directives:
        response["route_directives"] = route_directives

    response["green_corridor"] = {
        "active": True,
        "corridor_ids": corridor_ids,
        "anchor_rsu_ids": anchor_rsus,
        "rsu_ids": scoped_rsus,
        "max_remaining_seconds": round(max_remaining_seconds, 3),
        "corridors": active_for_rsu,
    }


# ─── HTTP endpoints ────────────────────────────────────────────────────────────
@app.route("/graph")
def graph_endpoint():
    """Return the current RSU graph as JSON."""
    data = {
        "nodes": [{"id": n, **d} for n, d in rsu_graph.nodes(data=True)],
        "edges": [{"from": u, "to": v} for u, v in rsu_graph.edges()],
    }
    return jsonify(data)


@app.route("/signals/<string:rsu_id>", methods=["GET"])
def rsu_spotlight_endpoint(rsu_id: str):
    """Return real-time spotlight data for a single RSU.

    Includes corridor membership and live metrics from the last batch.
    """
    rsu_id = str(rsu_id).strip()
    if rsu_id not in rsu_graph:
        return jsonify({"status": "error", "message": f"Unknown RSU: {rsu_id}"}), 404

    now_epoch = _now_epoch_seconds()
    active_for_rsu = _active_green_corridors_for_rsu(rsu_id, now_epoch)
    all_active = _list_active_green_corridors(now_epoch)

    response: dict = {
        "status": "ok",
        "rsu_id": rsu_id,
        "server_timestamp": ts(),
        "green_corridor_active": len(active_for_rsu) > 0,
        "green_corridor_count": len(active_for_rsu),
        "active_corridors": [c["corridor_id"] for c in active_for_rsu],
    }
    if all_active:
        response["green_corridor_global"] = {
            "active": True,
            "active_count": len(all_active),
        }
    return jsonify(response)


@app.route("/graph/register", methods=["POST"])
def graph_register_endpoint():
    """Register RSU topology via HTTP (mirrors the rsu_register SocketIO event).

    Allows external clients (e.g. the SUMO pipeline) to seed the RSU graph used
    by the GNN rerouting engine without needing a SocketIO connection.

        Payload:
            {
                "nodes": [
                    "jid1",
                    {"id": "jid2", "x": 123.4, "y": 567.8, "display_name": "Sealdah"}
                ],
                "edges": [["jid1","jid2"], ...]
            }
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Expected JSON payload"}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "Malformed payload"}), 400

    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    replace_graph = payload.get("replace_graph", True)

    if not isinstance(nodes, list):
        return jsonify({"status": "error", "message": "nodes must be a list"}), 400
    if not isinstance(edges, list):
        return jsonify({"status": "error", "message": "edges must be a list"}), 400

    if isinstance(replace_graph, str):
        replace_graph = replace_graph.strip().lower() in {"1", "true", "yes", "on"}
    else:
        replace_graph = bool(replace_graph)

    if replace_graph:
        rsu_graph.clear()

    for raw_node in nodes:
        node_id = ""
        node_attrs = {}

        if isinstance(raw_node, dict):
            node_id = str(raw_node.get("id", "")).strip()

            x = raw_node.get("x")
            y = raw_node.get("y")
            display_name = str(raw_node.get("display_name", "")).strip()

            if x is not None:
                node_attrs["x"] = x
            if y is not None:
                node_attrs["y"] = y
            if display_name:
                node_attrs["display_name"] = display_name
        else:
            node_id = str(raw_node).strip()

        if node_id:
            rsu_graph.add_node(node_id, **node_attrs)

    for raw_edge in edges:
        if isinstance(raw_edge, (list, tuple)) and len(raw_edge) >= 2:
            u = str(raw_edge[0]).strip()
            v = str(raw_edge[1]).strip()
            if u and v and u != v:
                rsu_graph.add_edge(u, v)

    log(
        f"[GRAPH] RSU graph updated via HTTP (replace={replace_graph}): "
        f"nodes={rsu_graph.number_of_nodes()} edges={rsu_graph.number_of_edges()}"
    )
    return jsonify({
        "status": "ok",
        "replace_graph": replace_graph,
        "node_count": rsu_graph.number_of_nodes(),
        "edge_count": rsu_graph.number_of_edges(),
    })


@app.route("/signals/green-corridor", methods=["GET", "POST"])
def green_corridor_endpoint():
    """Manage manually-triggered green corridors from the dashboard.

    GET: list active corridors
    POST action=activate: create a corridor
    POST action=clear: remove one/many active corridors
    """
    now_epoch = _now_epoch_seconds()

    if request.method == "GET":
        active = _list_active_green_corridors(now_epoch)
        return jsonify({
            "status": "ok",
            "active_count": len(active),
            "active_corridors": active,
            "server_timestamp": ts(),
        })

    if not request.is_json:
        return jsonify({
            "status": "error",
            "message": "Expected JSON payload",
        }), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({
            "status": "error",
            "message": "Malformed payload: expected a JSON object",
        }), 400

    action = str(payload.get("action", "activate")).strip().lower()
    # "deactivate" is an alias for "clear" with a specific corridor_id
    if action == "deactivate":
        action = "clear"
    if action not in {"activate", "clear"}:
        return jsonify({
            "status": "error",
            "message": "action must be one of: activate, deactivate, clear",
        }), 400

    if action == "clear":
        corridor_id = str(payload.get("corridor_id", "")).strip()
        anchor_rsu_id = str(payload.get("anchor_rsu_id", "")).strip()

        cleared = []
        with green_corridor_lock:
            _prune_expired_green_corridors_locked(now_epoch)
            for current_id, corridor in list(active_green_corridors.items()):
                matches = False
                if corridor_id:
                    matches = current_id == corridor_id
                elif anchor_rsu_id:
                    matches = str(corridor.get("anchor_rsu_id", "")) == anchor_rsu_id
                else:
                    matches = True
                if not matches:
                    continue
                removed = active_green_corridors.pop(current_id, None)
                if isinstance(removed, dict):
                    cleared.append(_to_public_green_corridor(removed, now_epoch))

            remaining = [_to_public_green_corridor(c, now_epoch) for c in active_green_corridors.values()]

        if cleared:
            broadcast_payload = {
                "action": "cleared",
                "corridors": cleared,
                "active_count": len(remaining),
                "timestamp": ts(),
            }
            socketio.emit("green_corridor_broadcast", broadcast_payload)
            _first = cleared[0] if cleared else {}
            _clear_src = _first.get("source_rsu_id") or _first.get("anchor_rsu_id", "SYSTEM")
            _clear_dst = _first.get("destination_rsu_id", "")
            _clear_msg = (
                f"Green corridor CLEARED: {_clear_src} → {_clear_dst}"
                if _clear_dst else f"Green corridor CLEARED: {_clear_src}"
            )
            if len(cleared) > 1:
                _clear_msg = f"Green corridors CLEARED: {len(cleared)} plans"
            _append_system_event({
                "type": "green_corridor_clear",
                "rsu_id": _clear_src,
                "corridor_ids": [c.get("corridor_id", "") for c in cleared],
                "message": _clear_msg,
                "timestamp": ts(),
            })

        return jsonify({
            "status": "ok",
            "action": "clear",
            "cleared_count": len(cleared),
            "cleared_corridors": cleared,
            "active_count": len(remaining),
            "active_corridors": remaining,
            "server_timestamp": ts(),
        })

    source_rsu_id = str(payload.get("source_rsu_id", "")).strip()
    destination_rsu_id = str(payload.get("destination_rsu_id", "")).strip()
    anchor_rsu_id = str(payload.get("anchor_rsu_id", "")).strip()
    use_path_scope = bool(source_rsu_id and destination_rsu_id)

    if use_path_scope:
        if source_rsu_id not in rsu_graph:
            return jsonify({
                "status": "error",
                "message": f"Unknown source_rsu_id: {source_rsu_id}",
            }), 404
        if destination_rsu_id not in rsu_graph:
            return jsonify({
                "status": "error",
                "message": f"Unknown destination_rsu_id: {destination_rsu_id}",
            }), 404
        if source_rsu_id == destination_rsu_id:
            return jsonify({
                "status": "error",
                "message": "source_rsu_id and destination_rsu_id must be different",
            }), 400
        # Keep anchor semantics for backward-compatible clear/filter behaviour.
        anchor_rsu_id = source_rsu_id
    else:
        if not anchor_rsu_id:
            return jsonify({
                "status": "error",
                "message": "anchor_rsu_id is required for activate action",
            }), 400
        if anchor_rsu_id not in rsu_graph:
            return jsonify({
                "status": "error",
                "message": f"Unknown anchor_rsu_id: {anchor_rsu_id}",
            }), 404

    try:
        radius_hops = int(payload.get("radius_hops", 1))
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "radius_hops must be an integer",
        }), 400
    if radius_hops < 0 or radius_hops > 6:
        return jsonify({
            "status": "error",
            "message": "radius_hops must be in [0, 6]",
        }), 400

    try:
        hold_seconds = float(payload.get("hold_seconds", 30.0))
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "hold_seconds must be numeric",
        }), 400
    if hold_seconds <= 0.0 or hold_seconds > 3600.0:
        return jsonify({
            "status": "error",
            "message": "hold_seconds must be in (0, 3600]",
        }), 400

    raw_emergency_ids = payload.get("emergency_vehicle_ids", [])
    if raw_emergency_ids is None:
        raw_emergency_ids = []
    if not isinstance(raw_emergency_ids, list):
        return jsonify({
            "status": "error",
            "message": "emergency_vehicle_ids must be a list",
        }), 400

    emergency_vehicle_ids = _unique_str_list(raw_emergency_ids)
    persistent = bool(payload.get("persistent", False))
    reason = str(payload.get("reason", "manual_dashboard_trigger")).strip() or "manual_dashboard_trigger"
    created_by = str(payload.get("created_by", "dashboard")).strip() or "dashboard"

    # Parse avoid_edges: list of [u, v] pairs whose edges should be avoided
    raw_avoid = payload.get("avoid_edges")
    avoid_edges: list[tuple[str, str]] | None = None
    if isinstance(raw_avoid, list):
        avoid_edges = []
        for pair in raw_avoid:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                avoid_edges.append((str(pair[0]).strip(), str(pair[1]).strip()))

    if use_path_scope:
        rsu_ids = _build_corridor_path_scope(source_rsu_id, destination_rsu_id, avoid_edges=avoid_edges)
        strategy = "rsu_shortest_path_v1" if not avoid_edges else "rsu_alternative_path_v1"
    else:
        rsu_ids = _build_corridor_scope(anchor_rsu_id, radius_hops)
        strategy = "rsu_hop_expansion_v1"

    if not rsu_ids:
        return jsonify({
            "status": "error",
            "message": (
                "Unable to build shortest RSU path for source/destination"
                if use_path_scope
                else "Unable to build corridor scope for the provided anchor_rsu_id"
            ),
        }), 400

    created_at_epoch = _now_epoch_seconds()
    expires_at_epoch = created_at_epoch + (86400.0 if persistent else hold_seconds)
    corridor_id = f"gc_{uuid.uuid4().hex[:10]}"
    corridor = {
        "corridor_id": corridor_id,
        "anchor_rsu_id": anchor_rsu_id,
        "source_rsu_id": source_rsu_id if use_path_scope else "",
        "destination_rsu_id": destination_rsu_id if use_path_scope else "",
        "rsu_ids": rsu_ids,
        "radius_hops": 0 if use_path_scope else radius_hops,
        "hold_seconds": hold_seconds,
        "persistent": persistent,
        "created_at_epoch": created_at_epoch,
        "expires_at_epoch": expires_at_epoch,
        "created_at": _iso_utc(created_at_epoch),
        "expires_at": _iso_utc(expires_at_epoch),
        "reason": reason,
        "created_by": created_by,
        "strategy": strategy,
        "emergency_vehicle_ids": emergency_vehicle_ids,
    }

    with green_corridor_lock:
        _prune_expired_green_corridors_locked(created_at_epoch)
        active_green_corridors[corridor_id] = corridor
        active_count = len(active_green_corridors)

    corridor_public = _to_public_green_corridor(corridor, created_at_epoch)

    broadcast_payload = {
        "action": "activated",
        "corridor": corridor_public,
        "active_count": active_count,
        "timestamp": ts(),
    }
    socketio.emit("green_corridor_broadcast", broadcast_payload)

    _append_system_event({
        "type": "green_corridor",
        "corridor_id": corridor_id,
        "rsu_id": anchor_rsu_id,
        "rsu_ids": rsu_ids,
        "message": f"Green corridor ACTIVE {anchor_rsu_id} → {destination_rsu_id or anchor_rsu_id} ({len(rsu_ids)} RSUs)",
        "timestamp": ts(),
    })
    if use_path_scope:
        log(
            f"[GREEN_CORRIDOR] Activated id={corridor_id} source={source_rsu_id} "
            f"destination={destination_rsu_id} path_rsu_count={len(rsu_ids)} hold_seconds={hold_seconds:.1f}"
        )
    else:
        log(
            f"[GREEN_CORRIDOR] Activated id={corridor_id} anchor={anchor_rsu_id} "
            f"radius_hops={radius_hops} rsu_count={len(rsu_ids)} hold_seconds={hold_seconds:.1f}"
        )

    return jsonify({
        "status": "ok",
        "action": "activate",
        "corridor": corridor_public,
        "active_count": active_count,
        "server_timestamp": ts(),
    })


@app.route("/analytics/hotspots")
def analytics_hotspots():
    """Return the top 5 junctions with most congestion events archived."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # We look for the top 5 RSU IDs with the most 'congestion' alerts
            cursor.execute("""
                SELECT rsu_id, COUNT(*) as frequency
                FROM events
                WHERE type = 'congestion' AND rsu_id != 'SYSTEM'
                GROUP BY rsu_id
                ORDER BY frequency DESC
                LIMIT 5
            """)
            rows = cursor.fetchall()
            hotspots = [{"rsu_id": row["rsu_id"], "frequency": row["frequency"]} for row in rows]
            log(f"[ANALYTICS] Hotspots requested - Found {len(hotspots)}")
            return jsonify({
                "status": "ok",
                "hotspots": hotspots,
                "server_timestamp": ts()
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/analytics/summary")
def analytics_summary():
    """Return high-level mission archive statistics."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total FROM events")
            total_events = cursor.fetchone()["total"]

            cursor.execute("SELECT COUNT(*) as total FROM events WHERE type='congestion'")
            total_alerts = cursor.fetchone()["total"]

            return jsonify({
                "status": "ok",
                "total_events": total_events,
                "total_alerts": total_alerts,
                "server_timestamp": ts()
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/analytics/distributions")
def analytics_distributions():
    """Return the breakdown of congestion events by junction for percentage analysis."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Grouping specifically by junction for congestion events
            cursor.execute("""
                SELECT rsu_id, COUNT(*) as count
                FROM events
                WHERE type = 'congestion' AND rsu_id != 'SYSTEM'
                GROUP BY rsu_id
                ORDER BY count DESC
            """)
            rows = cursor.fetchall()
            distribution = [{"rsu_id": row["rsu_id"], "count": row["count"]} for row in rows]

            log(f"[ANALYTICS] Junction Distribution requested - {len(distribution)} nodes")
            return jsonify({
                "status": "ok",
                "distribution": distribution,
                "server_timestamp": ts()
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def _parse_event_timestamp(value: str | None):
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    normalized = raw_value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(raw_value, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _analytics_parse_int_arg(name: str, default: int, min_value: int, max_value: int) -> int:
    raw_value = request.args.get(name, default)
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def _analytics_truthy_arg(name: str, default: bool = False) -> bool:
    raw_value = str(request.args.get(name, str(default))).strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def _load_rsu_display_name_map() -> dict[str, str]:
    global _rsu_display_name_map
    if isinstance(_rsu_display_name_map, dict):
        return _rsu_display_name_map

    mapping: dict[str, str] = {}
    config_path = Path(__file__).parent / "data" / "rsu_config_kolkata.json"

    try:
        if config_path.exists():
            import json

            with open(config_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)

            rsu_rows = payload.get("rsus", []) if isinstance(payload, dict) else []
            for row in rsu_rows:
                if not isinstance(row, dict):
                    continue

                display_name = str(row.get("display_name") or row.get("id") or "").strip()
                junction_id = str(row.get("junction_id") or "").strip()
                rsu_id = str(row.get("id") or "").strip()

                if not display_name:
                    continue

                for key in (junction_id, rsu_id, display_name):
                    normalized = str(key or "").strip()
                    if not normalized:
                        continue
                    mapping[normalized] = display_name
                    mapping[normalized.lower()] = display_name
    except Exception as exc:
        log(f"[ANALYTICS] Failed to load RSU display-name mapping: {exc}")

    _rsu_display_name_map = mapping
    return _rsu_display_name_map


def _analytics_rsu_label(rsu_id: str) -> str:
    rsu_key = str(rsu_id).strip()
    if not rsu_key:
        return "unknown"

    display_name_map = _load_rsu_display_name_map()
    mapped_label = display_name_map.get(rsu_key) or display_name_map.get(rsu_key.lower())
    if mapped_label:
        return mapped_label

    if rsu_key in rsu_graph:
        attrs = rsu_graph.nodes.get(rsu_key, {})
        if isinstance(attrs, dict):
            display_name = str(attrs.get("display_name", "")).strip()
            if display_name:
                return display_name

    # Fallbacks when explicit display_name is not available.
    lowered = rsu_key.lower()
    if lowered.startswith("cluster_"):
        cluster_body = rsu_key[len("cluster_"):]
        tokens = [token for token in cluster_body.split("_") if token]
        more_hint = ""
        if tokens and re.fullmatch(r"#\d+more", tokens[-1].lower()):
            more_hint = tokens.pop()

        primary_token = tokens[0] if tokens else "node"
        if more_hint:
            # Example: #3more -> +3 more
            more_count = re.sub(r"[^0-9]", "", more_hint)
            if more_count:
                return f"Cluster Junction {primary_token} (+{more_count} more)"

        if len(tokens) > 1:
            return f"Cluster Junction {primary_token} (+{len(tokens) - 1} linked)"
        return f"Cluster Junction {primary_token}"

    if rsu_key.isdigit():
        return f"Junction {rsu_key}"

    prettified = re.sub(r"[_-]+", " ", rsu_key).strip()
    if not prettified:
        return rsu_key

    return " ".join(part.capitalize() for part in prettified.split())


def _new_bucket_stat() -> dict:
    return {
        "event_count": 0,
        "congestion_count": 0,
        "clear_count": 0,
        "avg_wait_sum": 0.0,
        "avg_wait_n": 0,
        "vehicle_sum": 0.0,
        "vehicle_n": 0,
    }


def _metric_value(bucket_stat: dict, metric: str) -> float:
    if metric == "event_count":
        return float(bucket_stat.get("event_count", 0))
    if metric == "congestion_count":
        return float(bucket_stat.get("congestion_count", 0))
    if metric == "clear_count":
        return float(bucket_stat.get("clear_count", 0))
    if metric == "avg_wait":
        n = int(bucket_stat.get("avg_wait_n", 0))
        if n <= 0:
            return 0.0
        return float(bucket_stat.get("avg_wait_sum", 0.0)) / float(n)
    if metric == "avg_vehicle_count":
        n = int(bucket_stat.get("vehicle_n", 0))
        if n <= 0:
            return 0.0
        return float(bucket_stat.get("vehicle_sum", 0.0)) / float(n)

    return float(bucket_stat.get("event_count", 0))


@app.route("/analytics/traffic-timeseries")
def analytics_traffic_timeseries():
    """Return per-time-bucket traffic metrics for one/many/all RSUs."""
    metric = str(request.args.get("metric", "congestion_count")).strip().lower()
    supported_metrics = {
        "event_count",
        "congestion_count",
        "clear_count",
        "avg_wait",
        "avg_vehicle_count",
    }
    if metric not in supported_metrics:
        return jsonify({
            "status": "error",
            "message": (
                "Unsupported metric. Use one of: "
                + ", ".join(sorted(supported_metrics))
            ),
        }), 400

    window_minutes = _analytics_parse_int_arg(
        name="window_minutes",
        default=180,
        min_value=10,
        max_value=1440,
    )
    bucket_minutes = _analytics_parse_int_arg(
        name="bucket_minutes",
        default=5,
        min_value=1,
        max_value=120,
    )
    bucket_minutes = min(bucket_minutes, window_minutes)

    include_all = _analytics_truthy_arg("include_all", default=True)
    requested_rsus = _unique_str_list(
        part.strip()
        for part in str(request.args.get("rsu_ids", "")).split(",")
        if part.strip()
    )

    now_dt = datetime.now(timezone.utc)
    start_dt = now_dt - timedelta(minutes=window_minutes)
    bucket_seconds = float(bucket_minutes * 60)
    bucket_count = max(1, (window_minutes + bucket_minutes - 1) // bucket_minutes)

    bucket_starts = [
        start_dt + timedelta(minutes=(bucket_minutes * idx))
        for idx in range(bucket_count)
    ]

    all_bucket_stats = [_new_bucket_stat() for _ in range(bucket_count)]
    per_rsu_bucket_stats: dict[str, list[dict]] = {}

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT rsu_id, type, vehicle_count, avg_wait, timestamp
                FROM events
                WHERE rsu_id IS NOT NULL
                  AND rsu_id != ''
                  AND rsu_id != 'SYSTEM'
                  AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (start_dt.isoformat(),),
            )
            rows = cursor.fetchall()
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500

    for row in rows:
        rsu_id = str(row["rsu_id"] or "").strip()
        if not rsu_id:
            continue

        event_ts = _parse_event_timestamp(row["timestamp"])
        if event_ts is None:
            continue

        elapsed_seconds = (event_ts - start_dt).total_seconds()
        bucket_index = int(elapsed_seconds // bucket_seconds)
        if bucket_index < 0 or bucket_index >= bucket_count:
            continue

        if rsu_id not in per_rsu_bucket_stats:
            per_rsu_bucket_stats[rsu_id] = [_new_bucket_stat() for _ in range(bucket_count)]

        bucket_stat = per_rsu_bucket_stats[rsu_id][bucket_index]
        all_stat = all_bucket_stats[bucket_index]

        event_type = str(row["type"] or "").strip().lower()
        for target in (bucket_stat, all_stat):
            target["event_count"] += 1
            if event_type == "congestion":
                target["congestion_count"] += 1
            elif event_type == "clear":
                target["clear_count"] += 1

            avg_wait_raw = row["avg_wait"]
            if isinstance(avg_wait_raw, (int, float)):
                target["avg_wait_sum"] += float(avg_wait_raw)
                target["avg_wait_n"] += 1

            vehicle_raw = row["vehicle_count"]
            if isinstance(vehicle_raw, (int, float)):
                target["vehicle_sum"] += float(vehicle_raw)
                target["vehicle_n"] += 1

    if requested_rsus:
        selected_rsus = requested_rsus
    else:
        totals_by_rsu = []
        for rsu_id, stats_by_bucket in per_rsu_bucket_stats.items():
            totals_by_rsu.append((
                rsu_id,
                sum(int(bucket.get("event_count", 0)) for bucket in stats_by_bucket),
            ))

        totals_by_rsu.sort(key=lambda item: item[1], reverse=True)
        selected_rsus = [item[0] for item in totals_by_rsu[:5] if item[0]]

    known_rsus = _chat_collect_known_rsus()
    available_rsus = sorted(
        set(per_rsu_bucket_stats.keys()) | set(known_rsus),
        key=lambda item: item.lower(),
    )

    series = []
    if include_all:
        all_values = [
            round(_metric_value(bucket_stat, metric), 3)
            for bucket_stat in all_bucket_stats
        ]
        series.append({
            "series_id": "all_rsus",
            "rsu_id": "ALL",
            "label": "All RSUs",
            "values": all_values,
            "total": round(sum(all_values), 3),
        })

    for rsu_id in selected_rsus:
        stats_by_bucket = per_rsu_bucket_stats.get(rsu_id)
        if stats_by_bucket is None:
            stats_by_bucket = [_new_bucket_stat() for _ in range(bucket_count)]

        values = [
            round(_metric_value(bucket_stat, metric), 3)
            for bucket_stat in stats_by_bucket
        ]
        series.append({
            "series_id": f"rsu::{rsu_id}",
            "rsu_id": rsu_id,
            "label": _analytics_rsu_label(rsu_id),
            "values": values,
            "total": round(sum(values), 3),
        })

    return jsonify({
        "status": "ok",
        "metric": metric,
        "window_minutes": window_minutes,
        "bucket_minutes": bucket_minutes,
        "bucket_labels": [dt.strftime("%H:%M") for dt in bucket_starts],
        "bucket_timestamps": [dt.isoformat() for dt in bucket_starts],
        "series": series,
        "available_rsus": [
            {
                "rsu_id": rsu_id,
                "label": _analytics_rsu_label(rsu_id),
            }
            for rsu_id in available_rsus
        ],
        "server_timestamp": ts(),
    })


def _chat_collect_known_rsus() -> set[str]:
    known_rsus = {
        str(node_id).strip()
        for node_id in rsu_graph.nodes
        if str(node_id).strip()
    }

    with congestion_state_lock:
        known_rsus.update(
            str(rsu_id).strip()
            for rsu_id in rsu_congestion_state.keys()
            if str(rsu_id).strip()
        )

    with log_lock:
        for event in congestion_log[-500:]:
            rsu_id = str(event.get("from_rsu", "")).strip()
            if rsu_id:
                known_rsus.add(rsu_id)

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT rsu_id
                FROM events
                WHERE rsu_id IS NOT NULL AND rsu_id != '' AND rsu_id != 'SYSTEM'
                LIMIT 500
                """
            )
            for row in cursor.fetchall():
                rsu_id = str(row["rsu_id"]).strip()
                if rsu_id:
                    known_rsus.add(rsu_id)
    except Exception:
        # Chat still works with live memory state when archive is unavailable.
        pass

    return known_rsus


def _chat_extract_rsu_id(message: str, explicit_rsu_id=None) -> str:
    known_rsus = _chat_collect_known_rsus()
    known_lookup = {rsu_id.lower(): rsu_id for rsu_id in known_rsus}

    if explicit_rsu_id is not None:
        candidate = str(explicit_rsu_id).strip()
        if candidate:
            return known_lookup.get(candidate.lower(), candidate)

    text = str(message or "").strip()
    if not text:
        return ""

    tokens = re.findall(r"[A-Za-z0-9_-]+", text)
    for token in tokens:
        lookup_hit = known_lookup.get(token.lower())
        if lookup_hit:
            return lookup_hit

    rsu_match = re.search(
        r"\b(?:rsu|ru)(?:[-_\s]+([a-z0-9]+)|([0-9][a-z0-9]*))\b",
        text,
        re.IGNORECASE,
    )
    if rsu_match:
        suffix = str(rsu_match.group(1) or rsu_match.group(2) or "").upper()
        if not suffix:
            return ""

        generic_suffixes = {
            "S",
            "ARE",
            "PRESENT",
            "NUMBER",
            "COUNT",
            "TOTAL",
            "NAMES",
            "NAME",
            "LIST",
            "ALL",
            "ANY",
            "AVAILABLE",
        }
        if suffix in generic_suffixes:
            return ""

        for candidate in (f"RSU-{suffix}", f"RSU_{suffix}", suffix):
            lookup_hit = known_lookup.get(candidate.lower())
            if lookup_hit:
                return lookup_hit

        # Only synthesize unknown RSU IDs for compact/code-like suffixes.
        if len(suffix) <= 3 or any(ch.isdigit() for ch in suffix):
            return f"RSU-{suffix}"

    return ""


def _chat_live_snapshot() -> dict:
    with log_lock:
        recent_events = list(congestion_log[-500:])

    with congestion_state_lock:
        state = dict(rsu_congestion_state)

    # Fallback for sessions where congestion transitions were only logged as events.
    if not state:
        for event in recent_events:
            rsu_id = str(event.get("from_rsu", "")).strip()
            if not rsu_id:
                continue
            event_type = str(event.get("type", "")).lower()
            if event_type == "congestion":
                state[rsu_id] = True
            elif event_type == "clear":
                state[rsu_id] = False

    last_event_by_rsu = {}
    for event in reversed(recent_events):
        rsu_id = str(event.get("from_rsu", "")).strip()
        if rsu_id and rsu_id not in last_event_by_rsu:
            last_event_by_rsu[rsu_id] = event

    active_congested_rsus = sorted(
        rsu_id for rsu_id, is_congested in state.items() if bool(is_congested)
    )

    return {
        "active_congested_rsus": active_congested_rsus,
        "last_event_by_rsu": last_event_by_rsu,
        "recent_events": recent_events[-50:],
    }


def _chat_archive_summary_and_hotspots(limit: int = 5) -> tuple[dict, list[dict]]:
    summary = {
        "total_events": 0,
        "total_alerts": 0,
    }
    hotspots = []

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) AS total FROM events")
            row = cursor.fetchone()
            summary["total_events"] = int(row["total"]) if row else 0

            cursor.execute("SELECT COUNT(*) AS total FROM events WHERE type='congestion'")
            row = cursor.fetchone()
            summary["total_alerts"] = int(row["total"]) if row else 0

            cursor.execute(
                """
                SELECT rsu_id, COUNT(*) as frequency
                FROM events
                WHERE type = 'congestion' AND rsu_id != 'SYSTEM'
                GROUP BY rsu_id
                ORDER BY frequency DESC
                LIMIT ?
                """,
                (int(limit),),
            )

            for row in cursor.fetchall():
                hotspots.append({
                    "rsu_id": str(row["rsu_id"]),
                    "frequency": int(row["frequency"]),
                })
    except Exception:
        pass

    return summary, hotspots


def _chat_archive_rsu_stats(rsu_id: str) -> dict:
    stats = {
        "events": 0,
        "alerts": 0,
        "last_seen": "",
    }
    rsu_key = str(rsu_id).strip()
    if not rsu_key:
        return stats

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                "SELECT COUNT(*) AS total FROM events WHERE rsu_id = ?",
                (rsu_key,),
            )
            row = cursor.fetchone()
            stats["events"] = int(row["total"]) if row else 0

            cursor.execute(
                "SELECT COUNT(*) AS total FROM events WHERE rsu_id = ? AND type='congestion'",
                (rsu_key,),
            )
            row = cursor.fetchone()
            stats["alerts"] = int(row["total"]) if row else 0

            cursor.execute(
                "SELECT timestamp FROM events WHERE rsu_id = ? ORDER BY id DESC LIMIT 1",
                (rsu_key,),
            )
            row = cursor.fetchone()
            stats["last_seen"] = str(row["timestamp"]) if row else ""
    except Exception:
        pass

    return stats


@app.route("/chat/insights", methods=["POST"])
def chat_insights_endpoint():
    """Deterministic RSU-focused chat assistant endpoint for dashboard clients."""
    if not request.is_json:
        return jsonify({
            "status": "error",
            "message": "Expected JSON payload (Content-Type: application/json)",
        }), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({
            "status": "error",
            "message": "Malformed payload: expected a JSON object",
        }), 400

    message = str(payload.get("message", "")).strip()
    if not message:
        return jsonify({
            "status": "error",
            "message": "message is required",
        }), 400

    target_rsu = _chat_extract_rsu_id(message, explicit_rsu_id=payload.get("rsu_id"))
    known_rsus = _chat_collect_known_rsus()
    live_snapshot = _chat_live_snapshot()
    summary, hotspots = _chat_archive_summary_and_hotspots(limit=5)
    active_corridors = _list_active_green_corridors()

    message_lc = message.lower()
    contains_rsu_term = bool(re.search(r"\b(rsu|rsus|ru|rus)\b", message_lc))
    asks_help = (
        message_lc in {"help", "?"}
        or "what can you do" in message_lc
        or "how to use" in message_lc
    )
    asks_hotspots = (
        "hotspot" in message_lc
        or "most congested" in message_lc
        or "top" in message_lc
        or "worst" in message_lc
    )
    asks_corridor = "corridor" in message_lc
    asks_summary = (
        "summary" in message_lc
        or "overview" in message_lc
        or "overall" in message_lc
        or "system" in message_lc
    )
    asks_rsu_count = contains_rsu_term and (
        "how many" in message_lc
        or "number" in message_lc
        or "count" in message_lc
        or "total" in message_lc
        or "present" in message_lc
    )
    asks_rsu_list = contains_rsu_term and (
        "list" in message_lc
        or "name" in message_lc
        or "names" in message_lc
        or "which rsu" in message_lc
        or "show rsu" in message_lc
    )
    asks_event_feed = (
        "event feed" in message_lc
        or "event log" in message_lc
        or "recent event" in message_lc
        or "recent log" in message_lc
        or "what happened" in message_lc
        or ("event" in message_lc and any(token in message_lc for token in ("latest", "recent", "last", "log", "show")))
    )

    active_congested_rsus = list(live_snapshot.get("active_congested_rsus", []))
    last_event_by_rsu = live_snapshot.get("last_event_by_rsu", {})
    recent_events = list(live_snapshot.get("recent_events", []))
    response_lines = []
    intent = "summary"

    if asks_help:
        intent = "help"
        response_lines = [
            "I can provide RSU-level traffic insights from live and archived mission data.",
            "Try prompts like: 'Top congested RSUs now', 'Summary for RSU C', 'Any active green corridor?', or 'Summarize event feed in last 30 minutes'.",
            "Include an RSU ID in your message or send rsu_id in payload for focused analysis.",
        ]
    elif asks_rsu_count or asks_rsu_list:
        intent = "rsu_inventory"
        known_rsu_list = sorted(known_rsus, key=lambda item: item.lower())
        known_rsu_names = sorted(
            {_analytics_rsu_label(rsu_id) for rsu_id in known_rsu_list},
            key=lambda item: item.lower(),
        )
        response_lines.append(
            f"There are {len(known_rsu_list)} known RSUs in the current mission context."
        )

        if known_rsu_names:
            preview_limit = 30
            rsu_preview = ", ".join(known_rsu_names[:preview_limit])
            if len(known_rsu_names) > preview_limit:
                rsu_preview = rsu_preview + ", ..."

            if asks_rsu_list or len(known_rsu_names) <= 20:
                response_lines.append(f"RSU names: {rsu_preview}")
            else:
                response_lines.append("Ask 'list RSU names' to view all currently known RSU names.")
        else:
            response_lines.append(
                "No RSU names are available yet. Ensure topology registration has been received from SUMO/RSU clients."
            )
    elif asks_event_feed:
        intent = "event_feed"
        window_minutes = 30
        window_match = re.search(
            r"(?:last|past)\s+(\d{1,3})\s*(?:m|min|mins|minute|minutes)\b",
            message_lc,
        )
        if window_match:
            try:
                window_minutes = max(1, min(720, int(window_match.group(1))))
            except (TypeError, ValueError):
                window_minutes = 30

        window_start = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

        scoped_events = []
        for event in recent_events:
            event_ts = _parse_event_timestamp(event.get("timestamp"))
            if event_ts is not None and event_ts < window_start:
                continue
            scoped_events.append(event)

        if target_rsu:
            target_key = str(target_rsu).strip()
            scoped_events = [
                event
                for event in scoped_events
                if str(event.get("from_rsu", event.get("rsu_id", ""))).strip() == target_key
            ]

        if not scoped_events:
            if target_rsu:
                response_lines.append(
                    f"No event-feed entries found for {_analytics_rsu_label(target_rsu)} in the last {window_minutes} minutes."
                )
            else:
                response_lines.append(
                    f"No event-feed entries found in the last {window_minutes} minutes."
                )
        else:
            if target_rsu:
                response_lines.append(
                    f"I found {len(scoped_events)} events for {_analytics_rsu_label(target_rsu)} in the last {window_minutes} minutes."
                )
            else:
                response_lines.append(
                    f"I found {len(scoped_events)} events in the last {window_minutes} minutes across the network."
                )

            type_counts = {}
            for event in scoped_events:
                event_type = str(event.get("type", "event")).strip().lower() or "event"
                type_counts[event_type] = int(type_counts.get(event_type, 0)) + 1

            breakdown = ", ".join(
                f"{event_type}={count}"
                for event_type, count in sorted(type_counts.items(), key=lambda item: item[1], reverse=True)
            )
            if breakdown:
                response_lines.append(f"Breakdown: {breakdown}.")

            detail_limit = 5
            response_lines.append("Most recent event-feed entries:")
            for event in reversed(scoped_events[-detail_limit:]):
                event_type = str(event.get("type", "event")).replace("_", " ")
                event_rsu = str(event.get("from_rsu", event.get("rsu_id", ""))).strip()
                event_label = _analytics_rsu_label(event_rsu) if event_rsu and event_rsu.upper() != "SYSTEM" else "System"
                event_ts = str(event.get("timestamp", "unknown"))
                event_message = str(event.get("message", "")).strip()

                if event_message:
                    response_lines.append(
                        f"- {event_type} at {event_label} ({event_ts}): {event_message}"
                    )
                else:
                    vehicle_count = event.get("vehicle_count")
                    avg_wait = event.get("avg_wait")
                    if vehicle_count is not None and avg_wait is not None:
                        response_lines.append(
                            f"- {event_type} at {event_label} ({event_ts}) | {vehicle_count} vehicles, avg_wait {avg_wait}"
                        )
                    else:
                        response_lines.append(
                            f"- {event_type} at {event_label} ({event_ts})"
                        )
    elif target_rsu:
        latest_event = last_event_by_rsu.get(target_rsu, {})
        archive_stats = _chat_archive_rsu_stats(target_rsu)
        is_known_rsu = (
            target_rsu in known_rsus
            or bool(latest_event)
            or int(archive_stats.get("events", 0)) > 0
        )

        if not is_known_rsu:
            intent = "rsu_lookup_miss"
            response_lines.append(
                f"I could not find RSU {target_rsu} in current topology/live events/archive."
            )
            response_lines.append("Try asking: 'how many RSUs are present' or 'list RSU names'.")
        else:
            intent = "rsu_detail"
            is_congested = target_rsu in active_congested_rsus
            status_label = "congested" if is_congested else "clear"
            target_rsu_name = _analytics_rsu_label(target_rsu)
            corridor_hits = [
                corridor
                for corridor in active_corridors
                if target_rsu in [str(rsu_id) for rsu_id in corridor.get("rsu_ids", [])]
            ]

            response_lines.append(f"{target_rsu_name} is currently {status_label}.")
            if latest_event:
                event_type = str(latest_event.get("type", "event"))
                event_ts = str(latest_event.get("timestamp", "unknown"))
                vehicle_count = latest_event.get("vehicle_count")
                avg_wait = latest_event.get("avg_wait")
                if vehicle_count is not None and avg_wait is not None:
                    response_lines.append(
                        f"Latest live signal: {event_type} at {event_ts} ({vehicle_count} vehicles, avg_wait {avg_wait})."
                    )
                else:
                    response_lines.append(f"Latest live signal: {event_type} at {event_ts}.")

            response_lines.append(
                f"Archive stats: {archive_stats['alerts']} congestion alerts across {archive_stats['events']} total events."
            )
            if archive_stats["last_seen"]:
                response_lines.append(f"Last archived update: {archive_stats['last_seen']}.")

            if corridor_hits:
                response_lines.append(
                    f"Green corridor coverage is active for this RSU ({len(corridor_hits)} corridor plan(s))."
                )
    elif asks_hotspots:
        intent = "hotspots"
        if hotspots:
            hotspot_text = ", ".join(
                f"{_analytics_rsu_label(item['rsu_id'])} ({item['frequency']} alerts)"
                for item in hotspots
            )
            response_lines.append(f"Top archived congestion hotspots: {hotspot_text}.")
        else:
            response_lines.append("No archived hotspot history is available yet.")

        if active_congested_rsus:
            response_lines.append(
                "Live congested RSUs right now: "
                + ", ".join(_analytics_rsu_label(rsu_id) for rsu_id in active_congested_rsus[:10])
                + ("." if len(active_congested_rsus) <= 10 else ", ...")
            )
        else:
            response_lines.append("No RSU is currently marked congested in the live state.")
    elif asks_corridor:
        intent = "corridor"
        if active_corridors:
            response_lines.append(
                f"There are {len(active_corridors)} active green corridor plan(s)."
            )
            response_lines.extend(
                f"- {c.get('corridor_id', 'unknown')} | anchor {c.get('anchor_rsu_id', '?')} | RSUs {len(c.get('rsu_ids', []))}"
                for c in active_corridors[:5]
            )
        else:
            response_lines.append("No active green corridor plans at the moment.")
    else:
        intent = "summary" if asks_summary else "status"
        response_lines.append(
            f"Mission snapshot: {len(known_rsus)} known RSUs, {len(active_congested_rsus)} currently congested."
        )
        response_lines.append(
            f"Archive totals: {summary['total_alerts']} congestion alerts out of {summary['total_events']} events."
        )

        if hotspots:
            top = hotspots[0]
            response_lines.append(
                f"Highest archived hotspot: {_analytics_rsu_label(top['rsu_id'])} ({top['frequency']} alerts)."
            )

        if active_corridors:
            response_lines.append(
                f"Green corridor active count: {len(active_corridors)}."
            )

    data_source_count = 0
    if live_snapshot.get("recent_events") or active_congested_rsus:
        data_source_count += 1
    if summary.get("total_events", 0) > 0 or hotspots:
        data_source_count += 1
    if active_corridors:
        data_source_count += 1

    confidence = 1.0 if intent == "help" else round(min(0.98, 0.45 + (0.18 * data_source_count)), 2)

    payload_target_rsu = target_rsu if intent in {"rsu_detail", "event_feed"} else None

    return jsonify({
        "status": "ok",
        "intent": intent,
        "response": "\n".join(response_lines),
        "insights": {
            "target_rsu": payload_target_rsu,
            "known_rsu_count": len(known_rsus),
            "active_congested_rsus": active_congested_rsus,
            "recent_events_count": len(recent_events),
            "hotspots": hotspots,
            "active_green_corridors": [
                {
                    "corridor_id": corridor.get("corridor_id"),
                    "anchor_rsu_id": corridor.get("anchor_rsu_id"),
                    "rsu_count": len(corridor.get("rsu_ids", [])),
                    "remaining_seconds": corridor.get("remaining_seconds", 0),
                }
                for corridor in active_corridors
            ],
            "archive_summary": summary,
        },
        "confidence": confidence,
        "server_timestamp": ts(),
    })


@app.errorhandler(404)
def handle_404(e):
    """Ensure 404s return JSON instead of HTML to prevent frontend crashes."""
    return jsonify({
        "status": "error",
        "message": "Endpoint not found",
        "path": request.path
    }), 404


@app.route("/status")
def status_endpoint():
    """Return the last 50 congestion events."""
    with log_lock:
        return jsonify(congestion_log[-50:])


@app.route("/route", methods=["POST"])
def route_endpoint():
    """Return a routing decision payload compatible with the hybrid workflow contract.

    The endpoint supports deterministic fallback plus optional GNN/Phase-3 upgrades.
    """
    if not request.is_json:
        return jsonify({
            "status": "error",
            "message": "Expected JSON payload (Content-Type: application/json)",
        }), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({
            "status": "error",
            "message": "Malformed payload: expected a JSON object",
        }), 400

    validation_errors = []

    rsu_id = str(payload.get("rsu_id", "global"))

    sim_time = 0.0
    if "timestamp" in payload:
        try:
            sim_time = float(payload.get("timestamp"))
        except (TypeError, ValueError):
            validation_errors.append("timestamp must be numeric")

    raw_vehicle_ids = payload.get("vehicle_ids", [])
    if raw_vehicle_ids is None:
        raw_vehicle_ids = []
    if not isinstance(raw_vehicle_ids, list):
        validation_errors.append("vehicle_ids must be a list")
        vehicle_ids = []
    else:
        vehicle_ids = [str(vid) for vid in raw_vehicle_ids]

    raw_emergency_ids = payload.get("emergency_vehicle_ids", [])
    if raw_emergency_ids is None:
        raw_emergency_ids = []
    if not isinstance(raw_emergency_ids, list):
        validation_errors.append("emergency_vehicle_ids must be a list")
        emergency_vehicle_ids = []
    else:
        emergency_vehicle_ids = [str(vid) for vid in raw_emergency_ids]

    raw_forced_congested_rsus = payload.get("congested_rsu_ids", [])
    if raw_forced_congested_rsus is None:
        raw_forced_congested_rsus = []
    if isinstance(raw_forced_congested_rsus, list):
        forced_congested_rsus = _unique_str_list(raw_forced_congested_rsus)
    else:
        forced_congested_rsus = []

    raw_rsu_batch_metrics = payload.get("rsu_batch_metrics", [])
    if raw_rsu_batch_metrics is None:
        raw_rsu_batch_metrics = []
    if isinstance(raw_rsu_batch_metrics, list):
        rsu_batch_metrics = raw_rsu_batch_metrics
    else:
        validation_errors.append("rsu_batch_metrics must be a list")
        rsu_batch_metrics = []

    vehicle_count = len(vehicle_ids)
    if "vehicle_count" in payload:
        try:
            vehicle_count = int(payload.get("vehicle_count"))
            if vehicle_count < 0:
                validation_errors.append("vehicle_count must be >= 0")
        except (TypeError, ValueError):
            validation_errors.append("vehicle_count must be an integer")

    avg_speed_mps = 0.0
    if "avg_speed_mps" in payload:
        try:
            avg_speed_mps = float(payload.get("avg_speed_mps"))
            if avg_speed_mps < 0:
                validation_errors.append("avg_speed_mps must be >= 0")
        except (TypeError, ValueError):
            validation_errors.append("avg_speed_mps must be numeric")

    request_forecast, forecast_errors = _validate_optional_forecast_payload(payload.get("forecast"))
    validation_errors.extend(forecast_errors)

    if validation_errors:
        return jsonify({
            "status": "error",
            "message": "Malformed payload",
            "details": validation_errors,
        }), 400

    # Default deterministic surrogate preserves prior behavior.
    count_score = min(1.0, max(0.0, vehicle_count / 50.0))
    speed_score = 1.0 - min(1.0, max(0.0, avg_speed_mps / 15.0))
    p_congestion = max(0.0, min(1.0, 0.6 * count_score + 0.4 * speed_score))
    confidence = max(0.5, min(0.9, 0.9 - abs(p_congestion - 0.5)))
    model_label = "gnn_surrogate_v1"
    forecast_source = "deterministic_stub"
    gnn_decision = None
    prioritized_vehicle_ids = list(vehicle_ids)

    # Priority 1: explicit forecast fields in request payload (fully additive).
    if request_forecast:
        p_congestion = float(request_forecast.get("p_congestion", p_congestion))
        confidence = float(request_forecast.get("confidence", confidence))
        if "uncertainty" in request_forecast and "confidence" not in request_forecast:
            confidence = 1.0 - float(request_forecast.get("uncertainty"))
        confidence = max(0.0, min(1.0, confidence))
        p_congestion = max(0.0, min(1.0, p_congestion))
        model_label = str(request_forecast.get("model", "payload_forecast_v1"))
        forecast_source = "request_payload"

    # Priority 2: graph message-passing reroute inference behind feature flag.
    elif _is_gnn_routing_enabled():
        gnn_engine = _load_gnn_reroute_engine()
        if gnn_engine is not None:
            try:
                gnn_decision = gnn_engine.predict(
                    rsu_graph=rsu_graph,
                    rsu_id=rsu_id,
                    sim_timestamp=sim_time,
                    vehicle_ids=vehicle_ids,
                    emergency_vehicle_ids=emergency_vehicle_ids,
                    vehicle_count=vehicle_count,
                    avg_speed_mps=avg_speed_mps,
                )
                p_congestion = max(0.0, min(1.0, float(gnn_decision.get("p_congestion", p_congestion))))
                confidence = max(0.0, min(1.0, float(gnn_decision.get("confidence", confidence))))
                model_label = str(gnn_decision.get("model", "gnn_reroute_v1"))
                forecast_source = str(gnn_decision.get("source", "graph_message_passing"))

                vehicle_priority_order = gnn_decision.get("vehicle_priority_order", [])
                if isinstance(vehicle_priority_order, list):
                    seen_priorities = set()
                    ordered: list[str] = []
                    vehicle_id_set = set(vehicle_ids)
                    for raw_vid in vehicle_priority_order:
                        vid = str(raw_vid)
                        if not vid or vid in seen_priorities or vid not in vehicle_id_set:
                            continue
                        ordered.append(vid)
                        seen_priorities.add(vid)
                    if ordered:
                        ordered.extend([vid for vid in vehicle_ids if vid not in seen_priorities])
                        prioritized_vehicle_ids = ordered
            except Exception as exc:
                gnn_decision = None
                log(f"[GNN] Inference failed, fallback to deterministic stub: {exc}")

    # Priority 3: local artifact model behind feature flag.
    elif _is_forecast_artifact_enabled():
        engine = _load_forecast_engine()
        if engine is not None:
            try:
                forecast = engine.predict_from_route_payload(payload)
                p_congestion = max(0.0, min(1.0, float(forecast.get("p_congestion", p_congestion))))
                confidence = max(0.0, min(1.0, float(forecast.get("confidence", confidence))))
                model_label = str(forecast.get("model", "phase2_forecast_artifact_v1"))
                forecast_source = str(forecast.get("source", "forecast_artifact"))
            except Exception as exc:
                log(f"[FORECAST] Inference failed, fallback to deterministic stub: {exc}")

    uncertainty = max(0.0, min(1.0, 1.0 - confidence))
    if isinstance(gnn_decision, dict) and str(gnn_decision.get("risk_level", "")).lower() in {
        "low",
        "medium",
        "high",
    }:
        risk_level = str(gnn_decision.get("risk_level", "low")).lower()
    elif p_congestion >= 0.70:
        risk_level = "high"
    elif p_congestion >= 0.45:
        risk_level = "medium"
    else:
        risk_level = "low"

    emergency_active = len(emergency_vehicle_ids) > 0
    routing_engine = {
        "primary": "gnn_surrogate",
        "fallback": "dijkstra",
    }
    recommended_action = {
        "reroute_bias": "avoid_hotspots" if risk_level != "low" else "normal",
        "signal_priority": "inbound_relief" if risk_level == "high" else "balanced",
        "reroute_enabled": emergency_active or (risk_level != "low"),
        "reroute_mode": "dijkstra" if emergency_active else "gnn_effort",
        "reroute_fraction": 1.0 if emergency_active else (0.35 if risk_level == "high" else (0.20 if risk_level == "medium" else 0.0)),
        "min_confidence": 0.0 if emergency_active else 0.50,
        "fallback_algorithm": "dijkstra",
    }
    route_directives = []

    if isinstance(gnn_decision, dict):
        routing_engine["primary"] = str(gnn_decision.get("model", "gnn_reroute_v1"))

        gnn_recommended_action = gnn_decision.get("recommended_action")
        if isinstance(gnn_recommended_action, dict):
            recommended_action = {
                **recommended_action,
                **gnn_recommended_action,
            }

        gnn_route_directives = gnn_decision.get("route_directives")
        if isinstance(gnn_route_directives, list):
            route_directives = gnn_route_directives

    response = {
        "status": "ok",
        "rsu_id": rsu_id,
        "model": model_label,
        "forecast_source": forecast_source,
        "routing_engine": routing_engine,
        "p_congestion": p_congestion,
        "uncertainty": uncertainty,
        "confidence": confidence,
        "risk_level": risk_level,
        "recommended_action": recommended_action,
        "emergency_action": {
            "active": emergency_active,
            "vehicle_ids": emergency_vehicle_ids,
            "strategy": "optimal_route_plus_corridor_preemption" if emergency_active else "none",
            "traffic_control": "stop_non_emergency_on_corridor" if emergency_active else "normal_hybrid_control",
        },
        "sim_timestamp": sim_time,
        "server_timestamp": ts(),
    }

    if route_directives:
        response["route_directives"] = route_directives

    if isinstance(gnn_decision, dict):
        response["gnn_routing"] = {
            "enabled": True,
            "strategy": str(gnn_decision.get("strategy", "gnn_primary")),
            "diagnostics": gnn_decision.get("diagnostics", {}),
        }

    if _is_phase3_routing_enabled():
        try:
            from routing.phase3_risk_router import Phase3RoutingConfig, build_phase3_decision

            phase3_decision = build_phase3_decision(
                rsu_id=rsu_id,
                sim_timestamp=sim_time,
                vehicle_ids=prioritized_vehicle_ids,
                emergency_vehicle_ids=emergency_vehicle_ids,
                vehicle_count=vehicle_count,
                avg_speed_mps=avg_speed_mps,
                p_congestion=p_congestion,
                confidence=confidence,
                uncertainty=uncertainty,
                config=Phase3RoutingConfig.from_env(),
            )

            response["routing_engine"] = phase3_decision.get("routing_engine", response["routing_engine"])
            response["risk_level"] = phase3_decision.get("risk_level", response["risk_level"])
            response["recommended_action"] = phase3_decision.get(
                "recommended_action", response["recommended_action"]
            )

            if "route_directives" in phase3_decision:
                response["route_directives"] = phase3_decision.get("route_directives", [])

            phase3_payload = phase3_decision.get("phase3", {})
            if isinstance(phase3_payload, dict):
                if isinstance(gnn_decision, dict):
                    phase3_payload.setdefault(
                        "gnn_context",
                        {
                            "strategy": str(gnn_decision.get("strategy", "gnn_primary")),
                            "model": str(gnn_decision.get("model", "gnn_reroute_v1")),
                            "source": str(gnn_decision.get("source", "graph_message_passing")),
                        },
                    )
                response["phase3"] = phase3_payload

            audit_logger = _load_route_audit_logger()
            if audit_logger is not None:
                audit_id = audit_logger.log(
                    {
                        "rsu_id": rsu_id,
                        "sim_timestamp": sim_time,
                        "vehicle_count": vehicle_count,
                        "avg_speed_mps": avg_speed_mps,
                        "emergency_vehicle_count": len(emergency_vehicle_ids),
                        "forecast": {
                            "model": model_label,
                            "source": forecast_source,
                            "p_congestion": p_congestion,
                            "confidence": confidence,
                            "uncertainty": uncertainty,
                        },
                        "routing_engine": response.get("routing_engine", {}),
                        "risk_level": response.get("risk_level", "unknown"),
                        "recommended_action": response.get("recommended_action", {}),
                        "route_directives": response.get("route_directives", []),
                        "phase3": response.get("phase3", {}),
                    }
                )
                response["route_audit_id"] = audit_id
                phase3 = response.get("phase3")
                if isinstance(phase3, dict):
                    phase3["audit_id"] = audit_id
        except Exception as exc:
            log(f"[PHASE3] Routing decision failed, fallback to legacy policy: {exc}")

    _apply_green_corridor_override(response, rsu_id)
    if not rsu_batch_metrics:
        rsu_batch_metrics = [{
            "rsu_id": rsu_id,
            "vehicle_count": vehicle_count,
            "avg_speed_mps": avg_speed_mps,
        }]
    _sync_rsu_congestion_snapshot(rsu_batch_metrics, forced_congested_rsus)

    return jsonify(response)


# ─── SocketIO events ───────────────────────────────────────────────────────────
@socketio.on("connect")
def handle_connect():
    log(f"[CONNECT] Client connected  sid={request.sid}")


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    connected_clients.pop(sid, None)
    log(f"[DISCONNECT] Client disconnected  sid={sid}")


@socketio.on("rsu_register")
def handle_register(data):
    """
        Payload:
                {
                    "nodes": ["C", {"id": "E", "display_name": "Bowbazar"}],
                    "edges": [["C","E"], ["E","G"], ...]
                }
    """
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    replace_graph = data.get("replace_graph", True)
    if isinstance(replace_graph, str):
        replace_graph = replace_graph.strip().lower() in {"1", "true", "yes", "on"}
    else:
        replace_graph = bool(replace_graph)

    if replace_graph:
        rsu_graph.clear()

    # Build / update the RSU graph
    for raw_node in nodes:
        node_id = ""
        node_attrs = {}

        if isinstance(raw_node, dict):
            node_id = str(raw_node.get("id", "")).strip()

            x = raw_node.get("x")
            y = raw_node.get("y")
            display_name = str(raw_node.get("display_name", "")).strip()

            if x is not None:
                node_attrs["x"] = x
            if y is not None:
                node_attrs["y"] = y
            if display_name:
                node_attrs["display_name"] = display_name
        else:
            node_id = str(raw_node).strip()

        if node_id:
            rsu_graph.add_node(node_id, **node_attrs)
    for u, v in edges:
        rsu_graph.add_edge(u, v)

    connected_clients[request.sid] = "simulator"

    log(
        f"[REGISTER] RSU graph updated (replace={replace_graph}) "
        f"nodes={rsu_graph.number_of_nodes()} edges={rsu_graph.number_of_edges()}"
    )
    emit("register_ack", {"status": "ok", "rsu_count": len(nodes)})


@socketio.on("junction_congestion")
def handle_junction_congestion(data):
    """
    Payload:
        { "from_rsu": "C",
          "vehicle_count": 5,
          "avg_wait": 350 }
    """
    from_rsu = data.get("from_rsu", "?")
    count    = data.get("vehicle_count", 0)
    avg_wait = data.get("avg_wait", 0)

    log(
        f"[LONG WAIT] Junction '{from_rsu}' reports {count} vehicles "
        f"with avg wait {avg_wait} frames"
    )
    # Log the event
    event = {
        "type": "congestion",
        "from_rsu": from_rsu,
        "vehicle_count": count,
        "avg_wait": avg_wait,
        "timestamp": ts()
    }
    with log_lock:
        congestion_log.append(event)
    # Broadcast to all
    broadcast_payload = {
        "from_rsu":      from_rsu,
        "vehicle_count": count,
        "avg_wait":      avg_wait,
        "timestamp":     ts(),
    }
    emit("junction_broadcast", broadcast_payload, broadcast=True)


@socketio.on("junction_clear")
def handle_junction_clear(data):
    """
    Payload: { "from_rsu": "C" }
    """
    from_rsu = data.get("from_rsu", "?")
    log(f"[CLEAR] Junction '{from_rsu}' traffic resumed")
    # Log the event
    event = {
        "type": "clear",
        "from_rsu": from_rsu,
        "timestamp": ts()
    }
    with log_lock:
        congestion_log.append(event)
    broadcast_payload = {
        "from_rsu":  from_rsu,
        "timestamp": ts(),
    }
    emit("junction_clear_broadcast", broadcast_payload, broadcast=True)


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    db_init()
    db_reset_for_fresh_start()
    port = int(os.getenv("PORT", os.getenv("HYBRID_SERVER_PORT", "5000")))
    print("=" * 60)
    print("  V2X Central Server  |  Flask-SocketIO")
    print(f"  Listening on  http://0.0.0.0:{port}")
    print("  Endpoints:  GET /graph   GET /status   GET/POST /signals/green-corridor")
    print("=" * 60)
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=False,
        allow_unsafe_werkzeug=True,
        use_reloader=False,
        log_output=True,
    )
