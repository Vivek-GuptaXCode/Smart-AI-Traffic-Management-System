from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib import error, request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

try:
    from sumo.sumo_adapter import (
        SumoAdapter,
        build_sumo_command,
        load_scenario_config,
    )
except ModuleNotFoundError:
    from sumo_adapter import (  # type: ignore
        SumoAdapter,
        build_sumo_command,
        load_scenario_config,
    )

try:
    from pipelines.logging.runtime_logger import SumoSimulationDataLogger
except ModuleNotFoundError:
    # Support direct script execution: `python3 sumo/run_sumo_pipeline.py`
    # where sys.path starts at `sumo/` and does not include project root.
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.append(str(_PROJECT_ROOT))
    try:
        from pipelines.logging.runtime_logger import SumoSimulationDataLogger
    except ModuleNotFoundError:
        SumoSimulationDataLogger = None  # type: ignore[assignment]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SUMO scaffold loop for data pipeline integration.")
    parser.add_argument(
        "--contract",
        default="sumo/scenarios/sumo_contract.json",
        help="Path to SUMO scenario contract JSON.",
    )
    parser.add_argument(
        "--scenario",
        choices=["low", "medium", "high", "demo", "city", "kolkata"],
        default="demo",
        help="Scenario name from contract (default: demo -> real-city 3D hackathon flow).",
    )
    parser.add_argument("--seed", type=int, default=11, help="SUMO random seed.")
    parser.add_argument("--max-steps", type=int, default=None, help="Override contract max steps.")
    parser.add_argument("--gui", action="store_true", help="Use sumo-gui binary instead of sumo.")
    parser.add_argument(
        "--three-d",
        action="store_true",
        help="Enable OpenSceneGraph renderer (requires SUMO build with --osg-view support).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved command/config only; do not import traci/libsumo.",
    )
    parser.add_argument(
        "--rsu-range-m",
        type=float,
        default=120.0,
        help="RSU range radius in meters used for GUI range overlays.",
    )
    parser.add_argument(
        "--rsu-min-inc-lanes",
        type=int,
        default=4,
        help="Place RSU only on junctions with at least this many incoming lanes.",
    )
    parser.add_argument(
        "--rsu-max-count",
        type=int,
        default=40,
        help="Maximum number of RSU circles to draw.",
    )
    parser.add_argument(
        "--rsu-min-spacing-m",
        type=float,
        default=None,
        help="Minimum center-to-center spacing between RSUs (default: 1.8 * rsu-range-m).",
    )
    parser.add_argument(
        "--rsu-whitelist",
        type=str,
        default=None,
        help="Comma-separated list of RSU aliases to keep (e.g., 'A,B,K,M,R'). Only these RSUs will be active.",
    )
    parser.add_argument(
        "--rsu-config",
        type=str,
        default=None,
        help="Path to RSU configuration JSON file with custom RSU placements and names (e.g., data/rsu_config_kolkata.json).",
    )
    parser.add_argument(
        "--traffic-scale",
        type=float,
        default=1.0,
        help="Global demand multiplier via SUMO --scale (use >1.0 for jam-level traffic).",
    )
    parser.add_argument(
        "--traffic-reduction-pct",
        type=float,
        default=0.0,
        help="Optional traffic reduction percentage applied to traffic-scale (default: 0, opt-in).",
    )
    parser.add_argument(
        "--controlled-count",
        type=int,
        default=0,
        help="Number of AI-controlled test vehicles generated as a dedicated flow.",
    )
    parser.add_argument(
        "--controlled-source",
        default=None,
        help="Source location for controlled vehicles (junction id or edge id).",
    )
    parser.add_argument(
        "--controlled-destination",
        default=None,
        help="Destination location for controlled vehicles (junction id or edge id).",
    )
    parser.add_argument(
        "--controlled-via-rsus",
        default="",
        help="Comma-separated intermediate RSU locations (junction ids or edge ids).",
    )
    parser.add_argument(
        "--controlled-begin",
        type=float,
        default=90.0,
        help="Begin time for controlled vehicle flow.",
    )
    parser.add_argument(
        "--controlled-end",
        type=float,
        default=900.0,
        help="End time for controlled vehicle flow.",
    )
    parser.add_argument(
        "--emergency-count",
        type=int,
        default=0,
        help="Base emergency vehicle count; effective generated count is tripled (x3).",
    )
    parser.add_argument(
        "--emergency-source",
        default=None,
        help="Source location for emergency vehicles (junction id or edge id).",
    )
    parser.add_argument(
        "--emergency-destination",
        default=None,
        help="Destination location for emergency vehicles (junction id or edge id).",
    )
    parser.add_argument(
        "--emergency-via-rsus",
        default="",
        help="Comma-separated intermediate RSU locations for emergency vehicles.",
    )
    parser.add_argument(
        "--emergency-begin",
        type=float,
        default=120.0,
        help="Begin time for emergency vehicle flow.",
    )
    parser.add_argument(
        "--emergency-end",
        type=float,
        default=1800.0,
        help="End time for emergency vehicle flow.",
    )
    parser.add_argument(
        "--suggest-near-junction",
        default=None,
        help="Print nearby valid drivable junction IDs around the given junction and exit.",
    )
    parser.add_argument(
        "--suggest-purpose",
        choices=["source", "destination", "checkpoint", "any"],
        default="any",
        help="Filter suggested junctions for source/destination/checkpoint suitability.",
    )
    parser.add_argument(
        "--suggest-count",
        type=int,
        default=8,
        help="Number of nearest suggested junctions to print.",
    )
    parser.add_argument(
        "--list-rsus",
        action="store_true",
        help="Print RSU aliases (A, B, ... AA) mapped to junction IDs and exit.",
    )
    parser.add_argument(
        "--auto-fallback-junctions",
        action="store_true",
        help=(
            "Auto-replace invalid controlled junction source/destination/checkpoints with nearest valid "
            "passenger-drivable junctions (junction-mode only)."
        ),
    )
    parser.add_argument(
        "--enable-hybrid-uplink-stub",
        action="store_true",
        help="Send periodic RSU batch payload stubs to server /route during SUMO step loop.",
    )
    parser.add_argument(
        "--server-url",
        default="http://localhost:5000",
        help="Base server URL for hybrid uplink stub (default: http://localhost:5000).",
    )
    parser.add_argument(
        "--hybrid-batch-seconds",
        type=float,
        default=5.0,
        help="Batch period for hybrid uplink stub payloads in simulation seconds.",
    )
    parser.add_argument(
        "--route-timeout-seconds",
        type=float,
        default=1.5,
        help="HTTP timeout for server /route call in hybrid uplink stub.",
    )
    parser.add_argument(
        "--reroute-highlight-seconds",
        type=float,
        default=8.0,
        help="Duration to keep GUI highlight on vehicles rerouted from server policy.",
    )
    parser.add_argument(
        "--enable-emergency-priority",
        action="store_true",
        help="Enable emergency-vehicle priority: optimal reroute + corridor preemption.",
    )
    parser.add_argument(
        "--emergency-corridor-lookahead-edges",
        type=int,
        default=6,
        help="Number of upcoming edges treated as emergency corridor.",
    )
    parser.add_argument(
        "--emergency-hold-seconds",
        type=float,
        default=8.0,
        help="Duration to hold non-emergency traffic stopped on emergency corridor edges.",
    )
    parser.add_argument(
        "--emergency-priority-interval-steps",
        type=int,
        default=2,
        help="Run emergency priority policy every N simulation steps (default: 2).",
    )
    parser.add_argument(
        "--emergency-tls-lookahead",
        type=int,
        default=1,
        help="Number of upcoming TLS conflicts to preempt per emergency vehicle (default: 1).",
    )
    parser.add_argument(
        "--emergency-tls-preempt-distance-m",
        type=float,
        default=180.0,
        help="Maximum distance (m) to an upcoming TLS for lane-level preemption (default: 180).",
    )
    parser.add_argument(
        "--marker-refresh-steps",
        type=int,
        default=4,
        help="Refresh controlled/emergency vehicle markers every N simulation steps (default: 4).",
    )
    parser.add_argument(
        "--enable-runtime-logging",
        action="store_true",
        help="Enable Phase-1 1 Hz logging to data/raw/<run_id>/ (RSU + edge + manifest).",
    )
    parser.add_argument(
        "--runtime-log-root",
        default="data/raw",
        help="Output root for runtime logs (default: data/raw).",
    )
    parser.add_argument(
        "--runtime-log-run-id",
        default=None,
        help="Optional explicit run id for runtime logs (default: auto timestamp_scenario_seed).",
    )
    parser.add_argument(
        "--statistics-output",
        default=None,
        help="Optional SUMO statistics XML output path (--statistic-output).",
    )
    parser.add_argument(
        "--summary-output",
        default=None,
        help="Optional SUMO summary XML output path (--summary-output).",
    )
    parser.add_argument(
        "--tripinfo-output",
        default=None,
        help="Optional SUMO tripinfo XML output path (--tripinfo-output).",
    )
    parser.add_argument(
        "--tripinfo-write-unfinished",
        action="store_true",
        help="Include vehicles that have not arrived by simulation end in tripinfo output.",
    )
    parser.add_argument(
        "--kpi-output-dir",
        default=None,
        help=(
            "Optional output directory for auto-named KPI XML files "
            "(statistics/summary/tripinfo)."
        ),
    )
    parser.add_argument(
        "--kpi-output-prefix",
        default=None,
        help="Filename prefix used with --kpi-output-dir (default: auto timestamp_scenario_seed).",
    )
    # ── Phase 4: RL adaptive signal control ──────────────────────────────
    parser.add_argument(
        "--enable-rl-signal-control",
        action="store_true",
        help=(
            "Enable Phase-4 RL adaptive traffic signal control. "
            "Uses pre-trained DQN weights if --rl-model-dir is set, "
            "otherwise falls back to MaxPressure policy."
        ),
    )
    parser.add_argument(
        "--rl-model-dir",
        default=None,
        help="Path to DQN weights directory (models/rl/artifacts by default).",
    )
    parser.add_argument(
        "--rl-tls-ids",
        default=None,
        help="Comma-separated TLS junction IDs to control (auto-discovers all if omitted).",
    )
    parser.add_argument(
        "--rl-min-green-seconds",
        type=float,
        default=15.0,
        help="Minimum green duration enforced by safety guardrail (default: 15 s).",
    )
    parser.add_argument(
        "--rl-yellow-duration-seconds",
        type=float,
        default=3.0,
        help="Yellow transition window inserted between green phases (default: 3 s).",
    )
    parser.add_argument(
        "--rl-max-switches-per-window",
        type=int,
        default=4,
        help="Max phase switches allowed per 60-s rolling window (anti-oscillation).",
    )
    parser.add_argument(
        "--rl-max-controlled-tls",
        type=int,
        default=96,
        help=(
            "Upper bound on auto-discovered TLS controllers for large maps. "
            "Use 0 to disable limiting."
        ),
    )
    parser.add_argument(
        "--rl-step-interval-steps",
        type=int,
        default=5,
        help="Compute/apply RL signal actions every N simulation steps (default: 5).",
    )
    parser.add_argument(
        "--force-congestion-at-junction",
        default=None,
        help=(
            "Junction ID (or RSU alias e.g. DALHOUSIE) to force extreme congestion at. "
            "All connected edges receive a 9999 s travel-time penalty so every rerouting "
            "vehicle avoids it. Combine with --controlled-count to send a cohort from "
            "Sealdah to Park Circus that gets redirected around Dalhousie Square."
        ),
    )
    parser.add_argument(
        "--force-congestion-at-step",
        type=int,
        default=30,
        help="Simulation step at which to inject the forced junction congestion (default: 30).",
    )
    parser.add_argument(
        "--init-detour-junctions",
        default=None,
        help=(
            "Comma-separated junction IDs (or RSU aliases) to penalise at step 1 so vehicles "
            "are guided AWAY from these junctions initially (onto the Dalhousie corridor). "
            "The penalty is lifted automatically at --force-congestion-at-step, after which "
            "Dalhousie is blocked instead.  Example: 'MOULALI,ESPLANADE,CHANDNI_CHOWK'."
        ),
    )
    # ── T-GCN Neural Network Routing ──────────────────────────────────────
    parser.add_argument(
        "--enable-tgcn",
        action="store_true",
        help=(
            "Enable PyTorch T-GCN (Temporal Graph Convolutional Network) for "
            "learned traffic prediction and rerouting. Uses GPU if available."
        ),
    )
    parser.add_argument(
        "--tgcn-model-path",
        default=None,
        help="Path to pretrained T-GCN model weights (optional).",
    )
    parser.add_argument(
        "--tgcn-train",
        action="store_true",
        help="Enable online training of T-GCN during simulation.",
    )
    parser.add_argument(
        "--tgcn-log-interval",
        type=int,
        default=50,
        help="Steps between T-GCN metrics logging (default: 50).",
    )
    parser.add_argument(
        "--tgcn-checkpoint-dir",
        default="models/tgcn",
        help="Directory to save T-GCN checkpoints (default: models/tgcn).",
    )
    return parser.parse_args()


def _post_json(url: str, payload: dict, timeout_seconds: float, method: str = "POST") -> dict | None:
    body = json.dumps(payload).encode("utf-8") if method == "POST" else None
    headers = {"Content-Type": "application/json"} if method == "POST" else {}
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def _update_edge_weights_from_congestion(traci_module, *, conservative: bool = True) -> int:
    """Update SUMO edge travel times based on real-time congestion.

    This implements dynamic edge weight updates (key technique from PressLight/MA2C).
    Vehicles rerouting will use these updated weights for path finding.

    Args:
        traci_module: The SUMO TraCI module
        conservative: If True, use gentler penalties to avoid route oscillation

    Returns number of edges updated.
    """
    updated = 0
    try:
        edge_ids = traci_module.edge.getIDList()
        for edge_id in edge_ids:
            if edge_id.startswith(":"):  # Skip internal edges
                continue
            try:
                # Get current travel time (based on actual vehicle speeds)
                current_tt = traci_module.edge.getTraveltime(edge_id)
                # Get number of halting vehicles (queue length proxy)
                halting = traci_module.edge.getLastStepHaltingNumber(edge_id)
                # Get mean speed
                mean_speed = traci_module.edge.getLastStepMeanSpeed(edge_id)

                # Conservative mode: Only penalize severely congested edges
                # This avoids route oscillation where all vehicles switch together
                if conservative:
                    # Much higher thresholds to avoid false positives
                    if halting > 8 or mean_speed < 1.0:
                        # Gentler penalty to avoid over-steering
                        congestion_factor = 1.0 + (halting * 0.05) + (max(0, 3.0 - mean_speed) * 0.1)
                        adjusted_tt = current_tt * min(congestion_factor, 1.8)  # Cap at 1.8x
                        traci_module.edge.adaptTraveltime(edge_id, adjusted_tt)
                        updated += 1
                else:
                    # Original aggressive mode
                    if halting > 3 or mean_speed < 2.0:
                        congestion_factor = 1.0 + (halting * 0.15) + (max(0, 5.0 - mean_speed) * 0.2)
                        adjusted_tt = current_tt * min(congestion_factor, 3.0)
                        traci_module.edge.adaptTraveltime(edge_id, adjusted_tt)
                        updated += 1
            except Exception:
                continue
    except Exception:
        pass
    return updated


def _filter_vehicles_for_rerouting(
    traci_module,
    vehicle_ids: list[str],
    *,
    min_remaining_distance: float = 200.0,
    min_remaining_edges: int = 3,
) -> list[str]:
    """Filter vehicles that would benefit from rerouting.

    Avoids rerouting vehicles that are:
    - Too close to destination (would add overhead without benefit)
    - Already on optimal path (short remaining route)
    - Currently waiting/stopped (may cause issues)

    This is a key optimization from traffic engineering literature.
    """
    eligible = []
    for vid in vehicle_ids:
        try:
            # Skip vehicles near destination
            route = traci_module.vehicle.getRoute(vid)
            route_idx = traci_module.vehicle.getRouteIndex(vid)
            remaining_edges = len(route) - route_idx - 1

            if remaining_edges < min_remaining_edges:
                continue

            # Estimate remaining distance
            remaining_dist = 0.0
            current_edge = traci_module.vehicle.getRoadID(vid)
            if current_edge and not current_edge.startswith(":"):
                pos_on_edge = traci_module.vehicle.getLanePosition(vid)
                edge_length = traci_module.lane.getLength(traci_module.vehicle.getLaneID(vid))
                remaining_dist = edge_length - pos_on_edge

            # Add remaining edges
            for edge in route[route_idx + 1:]:
                if not edge.startswith(":"):
                    try:
                        remaining_dist += traci_module.lane.getLength(edge + "_0")
                    except Exception:
                        remaining_dist += 100.0  # Estimate

            if remaining_dist < min_remaining_distance:
                continue

            # Vehicle is eligible for rerouting
            eligible.append(vid)
        except Exception:
            continue

    return eligible


def _prioritize_vehicles_by_delay(
    traci_module,
    vehicle_ids: list[str],
    target_count: int,
) -> list[str]:
    """Prioritize vehicles with highest accumulated delay for rerouting.

    Vehicles stuck in traffic benefit most from rerouting.
    This improves overall efficiency vs random selection.
    """
    if len(vehicle_ids) <= target_count:
        return vehicle_ids

    vehicle_delays = []
    for vid in vehicle_ids:
        try:
            waiting_time = traci_module.vehicle.getAccumulatedWaitingTime(vid)
            vehicle_delays.append((vid, waiting_time))
        except Exception:
            vehicle_delays.append((vid, 0.0))

    # Sort by waiting time (highest first) and return top target_count
    vehicle_delays.sort(key=lambda x: -x[1])
    return [vid for vid, _ in vehicle_delays[:target_count]]


def _is_reroute_safe_now(traci_module, vehicle_id: str) -> bool:
    """Avoid applying route changes while a vehicle is on internal junction edges."""
    try:
        road_id = str(traci_module.vehicle.getRoadID(vehicle_id))
    except Exception:
        return False
    if not road_id:
        return False
    return not road_id.startswith(":")


def _reroute_with_dijkstra_fallback(traci_module, vehicle_id: str) -> bool:
    """Fallback route recomputation using findRoute (Dijkstra by default in SUMO)."""
    try:
        current_edge = str(traci_module.vehicle.getRoadID(vehicle_id))
        if not current_edge or current_edge.startswith(":"):
            return False

        current_route = list(traci_module.vehicle.getRoute(vehicle_id))
        if not current_route:
            return False
        destination_edge = str(current_route[-1])
        if not destination_edge:
            return False

        stage = traci_module.simulation.findRoute(current_edge, destination_edge)
        new_edges = list(getattr(stage, "edges", []))
        if not new_edges:
            return False
        if new_edges[0] != current_edge:
            return False

        traci_module.vehicle.setRoute(vehicle_id, new_edges)
        return True
    except Exception:
        return False


def _build_rsu_knn_edges(
    rsu_alias_table: list[tuple[str, str, float, float]],
    k: int = 3,
) -> list[tuple[str, str]]:
    """Connect each RSU to its K nearest neighbours (undirected, no duplicates)."""
    nodes = [(jid, x, y) for _alias, jid, x, y in rsu_alias_table]
    if len(nodes) < 2:
        return []
    edges: set[tuple[str, str]] = set()
    for i, (jid_a, xa, ya) in enumerate(nodes):
        distances = sorted(
            (math.hypot(xa - xb, ya - yb), jid_b)
            for j, (jid_b, xb, yb) in enumerate(nodes)
            if i != j
        )
        for _dist, jid_b in distances[:k]:
            edge = (min(jid_a, jid_b), max(jid_a, jid_b))
            edges.add(edge)
    return list(edges)


def _try_register_rsu_graph(
    register_url: str,
    rsu_alias_table: list[tuple[str, str, float, float]],
    rsu_display_name_by_jid: dict[str, str] | None = None,
    graph_edges: list[tuple[str, str]] | None = None,
    k_neighbors: int = 3,
    timeout: float = 2.0,
) -> bool:
    """POST RSU graph topology to server /graph/register. Returns True on success."""
    if not rsu_alias_table:
        return False
    nodes: list[dict[str, object]] = []
    for alias, jid, x, y in rsu_alias_table:
        display_name = ""
        if rsu_display_name_by_jid:
            display_name = str(rsu_display_name_by_jid.get(jid, "")).strip()
        if not display_name:
            display_name = str(alias).strip() or str(jid)

        nodes.append({
            "id": jid,
            "x": x,
            "y": y,
            "display_name": display_name,
        })

    if graph_edges is not None:
        edges = graph_edges
        edge_source = "provided"
    else:
        edges = _build_rsu_knn_edges(rsu_alias_table, k=k_neighbors)
        edge_source = "knn"

    payload = {"nodes": nodes, "edges": [[u, v] for u, v in edges]}
    result = _post_json(register_url, payload, timeout_seconds=timeout)
    if result is not None and result.get("status") == "ok":
        print(
            "[SUMO][GNN] RSU graph registered ({src}): nodes={n} edges={e}".format(
                src=edge_source,
                n=result.get("node_count", len(nodes)),
                e=result.get("edge_count", len(edges)),
            )
        )
        return True
    print("[SUMO][GNN] RSU graph registration failed — server may not be up yet or missing /graph/register")
    return False


def _is_emergency_vehicle(traci_module, vehicle_id: str) -> bool:
    try:
        vclass = str(traci_module.vehicle.getVehicleClass(vehicle_id)).lower()
        if vclass == "emergency":
            return True
    except Exception:
        pass

    try:
        type_id = str(traci_module.vehicle.getTypeID(vehicle_id)).lower()
    except Exception:
        type_id = ""

    emergency_tokens = ("emergency", "ambulance", "fire", "police")
    return any(token in type_id for token in emergency_tokens)


def _collect_emergency_tls_targets(
    traci_module,
    *,
    emergency_vehicle_ids: list[str],
    lookahead_tls: int,
    max_distance_m: float,
) -> dict[str, set[int]]:
    """Map each TLS to controlled link indices for emergency movement preemption.

    Uses TraCI vehicle.getNextTLS so we only preempt links actually used by
    approaching emergency vehicles instead of opening all directions.
    """
    targets: dict[str, set[int]] = {}
    max_tls = max(1, int(lookahead_tls))
    max_dist = max(10.0, float(max_distance_m))

    for vehicle_id in emergency_vehicle_ids:
        try:
            tls_rows = list(traci_module.vehicle.getNextTLS(vehicle_id))
        except Exception:
            continue

        if not tls_rows:
            continue

        used = 0
        for row in tls_rows:
            if used >= max_tls:
                break
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue

            tls_id = str(row[0]).strip()
            if not tls_id:
                continue

            try:
                link_index = int(row[1])
                distance_m = float(row[2])
            except (TypeError, ValueError):
                continue

            if link_index < 0 or distance_m < 0:
                continue

            # If first upcoming TLS is too far, avoid premature preemption.
            if distance_m > max_dist:
                if used == 0:
                    break
                continue

            targets.setdefault(tls_id, set()).add(link_index)
            used += 1

    return targets


def _apply_lane_level_tls_preemption(
    traci_module,
    *,
    sim_time: float,
    hold_seconds: float,
    tls_targets: dict[str, set[int]],
    tls_hold_until: dict[str, float],
    tls_original_state: dict[str, str],
) -> tuple[int, int]:
    """Force green only on emergency movement links while preserving TLS coordination."""
    preempted_tls = 0
    restored_tls = 0
    safe_hold = max(0.1, float(hold_seconds))

    for tls_id, green_indices in tls_targets.items():
        if not green_indices:
            continue
        try:
            current_state = str(traci_module.trafficlight.getRedYellowGreenState(tls_id))
        except Exception:
            continue

        if not current_state:
            continue

        # Save original state ONCE (not on every step to preserve cycling)
        if tls_id not in tls_original_state:
            tls_original_state[tls_id] = current_state
            # Save the REAL program ID BEFORE setRedYellowGreenState switches to "online".
            # This is the key to proper restoration — "online" cannot be used with setProgram.
            try:
                prog = traci_module.trafficlight.getProgram(tls_id)
                if prog and prog != "online":
                    tls_original_state[f"{tls_id}_program"] = prog
            except Exception:
                pass
            # Also capture the phase for intelligent restoration
            try:
                phase = traci_module.trafficlight.getPhase(tls_id)
                tls_original_state[f"{tls_id}_phase"] = str(phase)
            except Exception:
                pass

        # CRITICAL FIX: Start with current state instead of all red
        # This respects SUMO's internal signal cycle
        next_state_chars = list(current_state)
        for idx in green_indices:
            if 0 <= idx < len(next_state_chars):
                next_state_chars[idx] = "G"
        next_state = "".join(next_state_chars)

        try:
            if next_state != current_state:
                traci_module.trafficlight.setRedYellowGreenState(tls_id, next_state)
            # Set hold_until to absolute time, not cumulative
            tls_hold_until[tls_id] = sim_time + safe_hold
            preempted_tls += 1
        except Exception:
            continue

    for tls_id, until in list(tls_hold_until.items()):
        # Keep active preemption while target is still requested this step.
        if tls_id in tls_targets:
            continue
        if sim_time < float(until):
            continue

        # Restore normal TLS program cycling.
        # IMPORTANT: setRedYellowGreenState() silently switches SUMO into
        # the "online" pseudo-program.  Calling setProgram(tls_id, "online")
        # is a no-op.  We must call setProgram with the ORIGINAL named program
        # ID (e.g. "0") to exit "online" mode and resume normal phase cycling.
        original_state = tls_original_state.pop(tls_id, None)
        tls_original_state.pop(f"{tls_id}_phase", None)
        original_program = tls_original_state.pop(f"{tls_id}_program", None)

        try:
            if original_program:
                # Best path: restore to the saved named program
                traci_module.trafficlight.setProgram(tls_id, original_program)
            else:
                # Fallback: discover real programs via getAllProgramLogics
                try:
                    logics = traci_module.trafficlight.getAllProgramLogics(tls_id)
                    real_prog = next(
                        (l.programID for l in logics if l.programID != "online"),
                        None,
                    )
                    if real_prog:
                        traci_module.trafficlight.setProgram(tls_id, real_prog)
                    elif original_state:
                        traci_module.trafficlight.setRedYellowGreenState(tls_id, original_state)
                except Exception:
                    if original_state:
                        traci_module.trafficlight.setRedYellowGreenState(tls_id, original_state)

            # Reset to phase 0 so normal cycling starts cleanly
            try:
                traci_module.trafficlight.setPhase(tls_id, 0)
            except Exception:
                pass

        except Exception:
            # Last-resort: push the saved raw state string
            if original_state:
                try:
                    traci_module.trafficlight.setRedYellowGreenState(tls_id, original_state)
                except Exception:
                    pass

        tls_hold_until.pop(tls_id, None)
        restored_tls += 1

    return preempted_tls, restored_tls


def _force_restore_all_corridor_tls(
    traci_module,
    tls_hold_until: dict[str, float],
    tls_original_state: dict[str, str],
) -> int:
    """Immediately restore ALL preempted TLS to their original programs.

    Called when a green corridor is cleared or expires.  This is a dedicated
    one-shot restoration that does NOT rely on the per-step preemption loop.
    It uses three strategies in order of preference:

    1. setProgram(saved_original_program) — exits SUMO's "online" pseudo-program
    2. getAllProgramLogics → first non-"online" program
    3. setRedYellowGreenState(saved_state_string) — last resort
    """
    tls_ids = list(tls_hold_until.keys())
    if not tls_ids:
        return 0

    restored = 0
    for tls_id in tls_ids:
        original_program = tls_original_state.pop(f"{tls_id}_program", None)
        original_state = tls_original_state.pop(tls_id, None)
        tls_original_state.pop(f"{tls_id}_phase", None)

        try:
            if original_program:
                traci_module.trafficlight.setProgram(tls_id, original_program)
                print(f"[SUMO][GreenCorridor] Restored TLS {tls_id} → program '{original_program}'")
            else:
                # Discover original program from available logics
                try:
                    logics = traci_module.trafficlight.getAllProgramLogics(tls_id)
                    real_prog = next(
                        (l.programID for l in logics if l.programID != "online"),
                        None,
                    )
                    if real_prog:
                        traci_module.trafficlight.setProgram(tls_id, real_prog)
                        print(f"[SUMO][GreenCorridor] Restored TLS {tls_id} → discovered program '{real_prog}'")
                    elif original_state:
                        traci_module.trafficlight.setRedYellowGreenState(tls_id, original_state)
                        print(f"[SUMO][GreenCorridor] Restored TLS {tls_id} → raw state string (fallback)")
                    else:
                        print(f"[SUMO][GreenCorridor] WARNING: No program or state found for TLS {tls_id}")
                except Exception as e:
                    if original_state:
                        traci_module.trafficlight.setRedYellowGreenState(tls_id, original_state)
                        print(f"[SUMO][GreenCorridor] Restored TLS {tls_id} → raw state (getAllProgramLogics failed: {e})")

            # Reset to phase 0 to restart normal cycling cleanly
            try:
                traci_module.trafficlight.setPhase(tls_id, 0)
            except Exception:
                pass

            restored += 1
        except Exception as e:
            print(f"[SUMO][GreenCorridor] FAILED to restore TLS {tls_id}: {e}")
            # Last resort: try raw state
            if original_state:
                try:
                    traci_module.trafficlight.setRedYellowGreenState(tls_id, original_state)
                    restored += 1
                except Exception:
                    pass

    # Clear the dicts completely
    tls_hold_until.clear()
    print(f"[SUMO][GreenCorridor] TLS restoration complete: {restored}/{len(tls_ids)} restored")
    return restored


def _lane_id_to_edge_id(lane_id: str) -> str:
    lane = str(lane_id).strip()
    if not lane:
        return ""
    base, sep, suffix = lane.rpartition("_")
    if sep and base and suffix.isdigit():
        return base
    return lane


def _get_tls_outgoing_edge_index_map(
    traci_module,
    tls_id: str,
    tls_outgoing_edge_index_cache: dict[str, dict[str, set[int]]],
) -> dict[str, set[int]]:
    if tls_id in tls_outgoing_edge_index_cache:
        return tls_outgoing_edge_index_cache[tls_id]

    outgoing_edge_map: dict[str, set[int]] = {}
    try:
        controlled_links = list(traci_module.trafficlight.getControlledLinks(tls_id))
    except Exception:
        controlled_links = []

    for link_index, link_group in enumerate(controlled_links):
        if not link_group:
            continue
        for link_tuple in link_group:
            if not isinstance(link_tuple, (list, tuple)) or len(link_tuple) < 2:
                continue
            out_lane = str(link_tuple[1] or "").strip()
            if not out_lane:
                continue
            out_edge = _lane_id_to_edge_id(out_lane)
            if not out_edge:
                continue
            outgoing_edge_map.setdefault(out_edge, set()).add(link_index)

    tls_outgoing_edge_index_cache[tls_id] = outgoing_edge_map
    return outgoing_edge_map


def _get_all_tls_ids(
    traci_module,
    tls_id_cache: list[str],
) -> list[str]:
    if tls_id_cache:
        return tls_id_cache

    try:
        tls_ids = [str(tls_id).strip() for tls_id in traci_module.trafficlight.getIDList()]
    except Exception:
        tls_ids = []

    tls_id_cache.extend([tls_id for tls_id in tls_ids if tls_id])
    return tls_id_cache


def _build_junction_to_tls_map(
    traci_module,
    edge_to_junctions: dict[str, tuple[str, str]],
    tls_outgoing_edge_index_cache: dict[str, dict[str, set[int]]],
) -> dict[str, set[str]]:
    """Build a mapping: junction_id → set of TLS IDs that can control outgoing edges from that junction."""
    junction_to_tls: dict[str, set[str]] = {}

    try:
        tls_ids = [str(tls_id).strip() for tls_id in traci_module.trafficlight.getIDList()]
    except Exception:
        tls_ids = []

    for tls_id in tls_ids:
        if not tls_id:
            continue

        edge_map = _get_tls_outgoing_edge_index_map(
            traci_module,
            tls_id,
            tls_outgoing_edge_index_cache,
        )
        if not edge_map:
            continue

        # For each edge controlled by this TLS, map its source junction
        for edge_id in edge_map.keys():
            endpoints = edge_to_junctions.get(edge_id)
            if not endpoints or len(endpoints) < 2:
                continue
            from_junction = str(endpoints[0]).strip()
            if from_junction:
                junction_to_tls.setdefault(from_junction, set()).add(tls_id)

    return junction_to_tls


def _build_incoming_tls_map(
    traci_module,
    edge_to_junctions: dict[str, tuple[str, str]],
    tls_outgoing_edge_index_cache: dict[str, dict[str, set[int]]],
) -> dict[str, set[str]]:
    """Build a mapping: junction_id → set of TLS IDs that control INCOMING edges to that junction."""
    incoming_tls: dict[str, set[str]] = {}

    try:
        tls_ids = [str(tls_id).strip() for tls_id in traci_module.trafficlight.getIDList()]
    except Exception:
        tls_ids = []

    for tls_id in tls_ids:
        if not tls_id:
            continue

        edge_map = _get_tls_outgoing_edge_index_map(
            traci_module,
            tls_id,
            tls_outgoing_edge_index_cache,
        )
        if not edge_map:
            continue

        # For each edge controlled by this TLS, map its destination junction (incoming to that junction)
        for edge_id in edge_map.keys():
            endpoints = edge_to_junctions.get(edge_id)
            if not endpoints or len(endpoints) < 2:
                continue
            to_junction = str(endpoints[1]).strip()
            if to_junction:
                incoming_tls.setdefault(to_junction, set()).add(tls_id)

    return incoming_tls


def _find_shortest_junction_path(
    start_junction: str,
    target_junction: str,
    junction_adjacency: dict[str, set[str]],
    segment_path_cache: dict[tuple[str, str], list[str]],
) -> list[str]:
    start = str(start_junction).strip()
    target = str(target_junction).strip()
    if not start or not target:
        return []

    cache_key = (start, target)
    if cache_key in segment_path_cache:
        return segment_path_cache[cache_key]

    if start == target:
        segment_path_cache[cache_key] = [start]
        return [start]

    queue: list[str] = [start]
    visited = {start}
    parent: dict[str, str | None] = {start: None}

    while queue:
        node = queue.pop(0)
        if node == target:
            break

        for neighbor in junction_adjacency.get(node, set()):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            parent[neighbor] = node
            queue.append(neighbor)

    if target not in parent:
        segment_path_cache[cache_key] = []
        # Debug: Log path finding failure for Girish Park
        if "10092213336" in target or "10092213336" in start:
            print(f"[SUMO][GreenCorridor][DEBUG] No path found: {start} -> {target}")
            print(f"  visited {len(visited)} junctions")
            if start in junction_adjacency:
                neighbors = junction_adjacency[start]
                print(f"  {start} has {len(neighbors)} neighbors")
                if len(neighbors) > 0:
                    print(f"    sample neighbors: {list(neighbors)[:3]}")
        return []

    path_nodes: list[str] = []
    cursor: str | None = target
    while cursor is not None:
        path_nodes.append(cursor)
        cursor = parent.get(cursor)

    path_nodes.reverse()
    segment_path_cache[cache_key] = path_nodes

    # Debug: Log successful path for Girish Park
    if "10092213336" in target or "10092213336" in start:
        print(f"[SUMO][GreenCorridor][DEBUG] Path found: {start} -> {target} ({len(path_nodes)} hops)")
        if len(path_nodes) <= 5:
            print(f"  path: {' -> '.join(path_nodes)}")

    return path_nodes


def _has_junction_path(
    start_junction: str,
    target_junction: str,
    junction_adjacency: dict[str, set[str]],
    junction_reachability_cache: dict[tuple[str, str], bool],
) -> bool:
    start = str(start_junction).strip()
    target = str(target_junction).strip()
    if not start or not target:
        return False
    if start == target:
        return True

    cache_key = (start, target)
    if cache_key in junction_reachability_cache:
        return junction_reachability_cache[cache_key]

    queue: list[str] = [start]
    visited = {start}

    while queue:
        node = queue.pop(0)
        for neighbor in junction_adjacency.get(node, set()):
            if neighbor == target:
                junction_reachability_cache[cache_key] = True
                return True
            if neighbor in visited:
                continue
            visited.add(neighbor)
            queue.append(neighbor)

    junction_reachability_cache[cache_key] = False
    return False


def _extract_green_corridor_paths_from_route_response(route_response: dict[str, Any]) -> tuple[list[list[str]], float]:
    path_rows: list[list[str]] = []
    hold_seconds = 1.0

    candidate_corridors: list[dict[str, Any]] = []
    global_payload = route_response.get("green_corridor_global")
    if isinstance(global_payload, dict):
        corridors = global_payload.get("corridors")
        if isinstance(corridors, list):
            candidate_corridors.extend([row for row in corridors if isinstance(row, dict)])

    local_payload = route_response.get("green_corridor")
    if isinstance(local_payload, dict):
        corridors = local_payload.get("corridors")
        if isinstance(corridors, list):
            candidate_corridors.extend([row for row in corridors if isinstance(row, dict)])

    max_hold = 0.0
    for corridor in candidate_corridors:
        strategy = str(corridor.get("strategy", "")).strip().lower()
        source = str(corridor.get("source_rsu_id", "")).strip()
        destination = str(corridor.get("destination_rsu_id", "")).strip()
        rsu_ids = [str(raw).strip() for raw in corridor.get("rsu_ids", []) if str(raw).strip()]
        if len(rsu_ids) < 2:
            continue

        # Shortest-path corridors must have explicit source+destination.
        # Hop-expansion corridors only need a valid rsu_ids list (already validated ≥ 2 above).
        if "shortest_path" in strategy and not (source and destination):
            continue

        path_rows.append(rsu_ids)

        try:
            remaining = float(corridor.get("remaining_seconds", 0.0))
        except Exception:
            remaining = 0.0
        try:
            planned = float(corridor.get("hold_seconds", 0.0))
        except Exception:
            planned = 0.0
        max_hold = max(max_hold, remaining if remaining > 0 else planned)

    if max_hold > 0:
        hold_seconds = max(0.5, min(300.0, max_hold))

    return path_rows, hold_seconds


def _collect_green_corridor_tls_targets(
    traci_module,
    *,
    corridor_paths: list[list[str]],
    edge_to_junctions: dict[str, tuple[str, str]],
    tls_outgoing_edge_index_cache: dict[str, dict[str, set[int]]],
    tls_id_cache: list[str],
    segment_path_cache: dict[tuple[str, str], list[str]],
    junction_reachability_cache: dict[tuple[str, str], bool],
    junction_to_tls_map: dict[str, set[str]],
    incoming_tls_map: dict[str, set[str]] | None = None,
) -> dict[str, set[int]]:
    """Collect all TLS targets to enable a complete green corridor for vehicles.

    Strategy:
    1. For START junction: turn green on INCOMING edges (so vehicles can enter)
    2. For INTERMEDIATE junctions: turn green on edges along the corridor path
    3. For END junction: turn green on path edges leading IN (to reach destination)

    This ensures complete signal preemption from source to destination.
    """
    if not corridor_paths or not edge_to_junctions:
        return {}

    if incoming_tls_map is None:
        incoming_tls_map = {}

    # Build adjacency and edge maps
    directed_edge_by_pair: dict[tuple[str, str], set[str]] = {}
    junction_adjacency: dict[str, set[str]] = {}
    incoming_edges_to_junction: dict[str, set[str]] = {}  # NEW: track incoming edges

    for edge_id, endpoints in edge_to_junctions.items():
        if not isinstance(endpoints, (list, tuple)) or len(endpoints) < 2:
            continue
        from_junction = str(endpoints[0]).strip()
        to_junction = str(endpoints[1]).strip()
        if not from_junction or not to_junction:
            continue
        directed_edge_by_pair.setdefault((from_junction, to_junction), set()).add(str(edge_id))
        junction_adjacency.setdefault(from_junction, set()).add(to_junction)
        incoming_edges_to_junction.setdefault(to_junction, set()).add(str(edge_id))  # NEW

    all_tls_ids = _get_all_tls_ids(traci_module, tls_id_cache)
    tls_targets: dict[str, set[int]] = {}
    junctions_in_corridor: set[str] = set()

    def _assign_targets_for_edges(
        preferred_junction: str,
        candidate_edges: set[str],
        target_junction: str,
    ) -> None:
        # Get TLS IDs that can control outgoing edges from the preferred junction
        preferred_tls_ids = junction_to_tls_map.get(preferred_junction, set())

        for preferred_tls_id in preferred_tls_ids:
            preferred_map = _get_tls_outgoing_edge_index_map(
                traci_module,
                preferred_tls_id,
                tls_outgoing_edge_index_cache,
            )
            preferred_indices: set[int] = set()
            for edge_id in candidate_edges:
                preferred_indices.update(preferred_map.get(edge_id, set()))

            if preferred_indices:
                tls_targets.setdefault(preferred_tls_id, set()).update(preferred_indices)
                return

        # Secondary fallback: scan all TLS IDs for any that control the candidate edges
        for tls_id in all_tls_ids:
            fallback_edge_map = _get_tls_outgoing_edge_index_map(
                traci_module,
                tls_id,
                tls_outgoing_edge_index_cache,
            )
            if not fallback_edge_map:
                continue

            fallback_indices: set[int] = set()
            for edge_id in candidate_edges:
                fallback_indices.update(fallback_edge_map.get(edge_id, set()))

            if fallback_indices:
                tls_targets.setdefault(tls_id, set()).update(fallback_indices)
                return

        # Tertiary fallback: directional reachability
        target = str(target_junction).strip()
        if not target:
            return

        scan_tls_ids = [*preferred_tls_ids, *[tls_id for tls_id in all_tls_ids if tls_id not in preferred_tls_ids]]
        for tls_id in scan_tls_ids:
            edge_index_map = _get_tls_outgoing_edge_index_map(
                traci_module,
                tls_id,
                tls_outgoing_edge_index_cache,
            )
            if not edge_index_map:
                continue

            directional_indices: set[int] = set()
            for out_edge, out_indices in edge_index_map.items():
                endpoints = edge_to_junctions.get(out_edge)
                if not endpoints or len(endpoints) < 2:
                    continue

                out_to_junction = str(endpoints[1]).strip()
                if not out_to_junction:
                    continue

                if out_to_junction == target or _has_junction_path(
                    out_to_junction,
                    target,
                    junction_adjacency,
                    junction_reachability_cache,
                ):
                    directional_indices.update(out_indices)

            if directional_indices:
                tls_targets.setdefault(tls_id, set()).update(directional_indices)
                return

    # Process all corridor paths
    for path in corridor_paths:
        if len(path) < 2:
            continue

        # Track all junctions in corridor
        for junction in path:
            junctions_in_corridor.add(str(junction).strip())

        # Build path segment by segment
        for idx in range(len(path) - 1):
            current_junction = str(path[idx]).strip()
            next_junction = str(path[idx + 1]).strip()
            if not current_junction or not next_junction:
                continue

            # Expand sparse RSU hops to real junction-by-junction movement path.
            expanded_segment = _find_shortest_junction_path(
                start_junction=current_junction,
                target_junction=next_junction,
                junction_adjacency=junction_adjacency,
                segment_path_cache=segment_path_cache,
            )
            if len(expanded_segment) < 2:
                continue

            # For each hop in the expanded segment, target outgoing edges
            for step_idx in range(len(expanded_segment) - 1):
                segment_from = expanded_segment[step_idx]
                segment_to = expanded_segment[step_idx + 1]
                candidate_edges = directed_edge_by_pair.get((segment_from, segment_to), set())

                if "10092213336" in segment_to or "10092213336" in segment_from:
                    print(f"[SUMO][GreenCorridor][DEBUG] Processing segment: {segment_from} -> {segment_to} ({len(candidate_edges)} edges)")

                _assign_targets_for_edges(segment_from, candidate_edges, segment_to)

    # NEW: Ensure START and END junctions have full signal coverage
    # For START junction: turn green on ALL INCOMING edges (critical!)
    if junctions_in_corridor:
        path_list = [j for j in junctions_in_corridor if j]
        if path_list:
            start_junction = path_list[0]
            end_junction = path_list[-1]

            # Handle START junction - turn green on INCOMING edges (critical!)
            # These are controlled by the incoming_tls_map
            incoming_tls_for_start = incoming_tls_map.get(start_junction, set())
            if incoming_tls_for_start:
                print(f"[SUMO][GreenCorridor] START junction has {len(incoming_tls_for_start)} incoming TLS to enable")
                for incoming_tls_id in incoming_tls_for_start:
                    incoming_edge_map = _get_tls_outgoing_edge_index_map(
                        traci_module,
                        incoming_tls_id,
                        tls_outgoing_edge_index_cache,
                    )
                    for edge_id, indices in incoming_edge_map.items():
                        endpoints = edge_to_junctions.get(edge_id, ("", ""))
                        # Only target edges that LEAD TO the start junction
                        if endpoints[1] == start_junction:
                            tls_targets.setdefault(incoming_tls_id, set()).update(indices)
                            print(f"  Added {len(indices)} lanes from incoming TLS {incoming_tls_id} for edge {edge_id}")

            # Handle INTERMEDIATE and END junctions - ensure they have full coverage
            for intermediate_idx, intermediate in enumerate(path_list[:-1]):
                next_j = path_list[intermediate_idx + 1]
                edges_on_path = directed_edge_by_pair.get((intermediate, next_j), set())
                if edges_on_path:
                    print(f"[SUMO][GreenCorridor] Path segment {intermediate[:20]} -> {next_j[:20]}: {len(edges_on_path)} edges")
                    for edge_id in edges_on_path:
                        # For each path edge, find TLS that control it
                        for tls_id in all_tls_ids:
                            tls_edge_map = _get_tls_outgoing_edge_index_map(
                                traci_module,
                                tls_id,
                                tls_outgoing_edge_index_cache,
                            )
                            if edge_id in tls_edge_map:
                                tls_targets.setdefault(tls_id, set()).update(tls_edge_map[edge_id])
                                print(f"    Edge {edge_id}: controlled by TLS {tls_id}")
                                break  # Found controlling TLS for this edge

    # Debug: Log final TLS targets
    if tls_targets:
        girish_tls = [tls for tls in tls_targets.keys() if "10092213336" in str(tls)]
        if girish_tls or len(tls_targets) % 5 == 0:
            total_lanes = sum(len(indices) for indices in tls_targets.values())
            print(f"[SUMO][GreenCorridor] TLS targets: {len(tls_targets)} TLS controls with {total_lanes} total lane indices")
            if girish_tls:
                print(f"  Girish Park TLS: {', '.join(f'{tls}({len(tls_targets[tls])}lanes)' for tls in girish_tls[:3])}")

    return tls_targets


def _apply_emergency_priority_policy(
    traci_module,
    *,
    sim_time: float,
    vehicle_ids: list[str],
    emergency_vehicle_ids: list[str],
    held_until: dict[str, float],
    tls_hold_until: dict[str, float],
    tls_original_state: dict[str, str],
    lookahead_edges: int,
    tls_lookahead_count: int,
    tls_preempt_distance_m: float,
    hold_seconds: float,
) -> dict[str, int]:
    emergency_ids = emergency_vehicle_ids
    corridor_edges: set[str] = set()
    emergency_reroutes = 0

    for evid in emergency_ids:
        if not _is_reroute_safe_now(traci_module, evid):
            continue

        try:
            current_edge = str(traci_module.vehicle.getRoadID(evid))
            current_route = list(traci_module.vehicle.getRoute(evid))
            if not current_route or not current_edge or current_edge.startswith(":"):
                continue
            destination_edge = str(current_route[-1])

            stage = traci_module.simulation.findRoute(current_edge, destination_edge)
            optimal_edges = list(getattr(stage, "edges", []))
            if optimal_edges and optimal_edges[0] == current_edge:
                traci_module.vehicle.setRoute(evid, optimal_edges)
                active_route = optimal_edges
                emergency_reroutes += 1
            else:
                active_route = current_route

            try:
                idx = active_route.index(current_edge)
            except Exception:
                idx = max(0, int(traci_module.vehicle.getRouteIndex(evid)))

            for edge_id in active_route[idx : idx + max(1, lookahead_edges)]:
                if edge_id and not str(edge_id).startswith(":"):
                    corridor_edges.add(str(edge_id))
        except Exception:
            continue

    preempted = 0
    if corridor_edges:
        for vid in vehicle_ids:
            if vid in emergency_ids:
                continue
            try:
                road_id = str(traci_module.vehicle.getRoadID(vid))
            except Exception:
                continue
            if road_id in corridor_edges:
                current_hold_until = float(held_until.get(vid, -1.0))
                next_hold_until = sim_time + max(0.1, hold_seconds)
                if current_hold_until > sim_time:
                    # Already held; extend the hold window without repeatedly forcing setSpeed.
                    held_until[vid] = max(current_hold_until, next_hold_until)
                    continue
                try:
                    traci_module.vehicle.setSpeed(vid, 0.0)
                    held_until[vid] = next_hold_until
                    preempted += 1
                except Exception:
                    continue

    released = 0
    for vid, until in list(held_until.items()):
        if vid not in vehicle_ids or sim_time >= until:
            try:
                if vid in vehicle_ids:
                    traci_module.vehicle.setSpeed(vid, -1)
                    released += 1
            except Exception:
                pass
            held_until.pop(vid, None)

    tls_targets = _collect_emergency_tls_targets(
        traci_module,
        emergency_vehicle_ids=emergency_ids,
        lookahead_tls=tls_lookahead_count,
        max_distance_m=tls_preempt_distance_m,
    )
    tls_preempted, tls_restored = _apply_lane_level_tls_preemption(
        traci_module,
        sim_time=sim_time,
        hold_seconds=hold_seconds,
        tls_targets=tls_targets,
        tls_hold_until=tls_hold_until,
        tls_original_state=tls_original_state,
    )

    return {
        "emergency_count": len(emergency_ids),
        "emergency_reroutes": emergency_reroutes,
        "corridor_preempted": preempted,
        "released": released,
        "tls_preempted": tls_preempted,
        "tls_restored": tls_restored,
    }


def _apply_server_reroute_policy(
    traci_module,
    vehicle_ids: list[str],
    route_response: dict,
    *,
    sim_time: float | None = None,
    reroute_cooldown_until: dict[str, float] | None = None,
    reroute_cooldown_seconds: float = 25.0,
) -> dict[str, Any]:
    """Apply live rerouting decisions from server policy fields.

    This is the runtime bridge that turns cloud policy output into TraCI route updates.
    """
    rec = route_response.get("recommended_action") or {}
    vehicle_id_set = set(vehicle_ids)
    emergency_action = route_response.get("emergency_action") or {}
    emergency_active = bool(emergency_action.get("active", False))
    emergency_vehicle_ids = {
        str(vid)
        for vid in (emergency_action.get("vehicle_ids") or [])
        if str(vid) in vehicle_id_set
    }

    if not bool(rec.get("reroute_enabled", False)) and not emergency_active:
        return {"count": 0, "vehicle_ids": []}

    try:
        confidence = float(route_response.get("confidence", 0.0))
    except Exception:
        confidence = 0.0

    try:
        min_confidence = float(rec.get("min_confidence", 0.5))
    except Exception:
        min_confidence = 0.5

    if not emergency_active:
        try:
            conf_floor = float(os.getenv("HYBRID_REROUTE_MIN_CONF_FLOOR", "0.58"))
        except Exception:
            conf_floor = 0.58
        conf_floor = max(0.0, min(1.0, conf_floor))
        min_confidence = max(min_confidence, conf_floor)

    if confidence < min_confidence and not emergency_active:
        return {"count": 0, "vehicle_ids": []}

    if not vehicle_ids:
        return {"count": 0, "vehicle_ids": []}

    try:
        reroute_fraction = float(rec.get("reroute_fraction", 0.0))
    except Exception:
        reroute_fraction = 0.0
    reroute_fraction = max(0.0, min(1.0, reroute_fraction))
    if not emergency_active:
        try:
            fraction_cap = float(os.getenv("HYBRID_REROUTE_FRACTION_CAP", "0.12"))
        except Exception:
            fraction_cap = 0.12
        fraction_cap = max(0.0, min(1.0, fraction_cap))
        reroute_fraction = min(reroute_fraction, fraction_cap)

    reroute_mode = str(rec.get("reroute_mode", "travel_time"))
    fallback_algorithm = str(rec.get("fallback_algorithm", "")).lower()
    routing_engine = route_response.get("routing_engine") or {}
    if not fallback_algorithm:
        fallback_algorithm = str(routing_engine.get("fallback", "")).lower()

    directives_raw = route_response.get("route_directives")
    planned_reroutes: list[tuple[str, str]] = []
    if isinstance(directives_raw, list):
        seen_ids: set[str] = set()
        for row in directives_raw:
            if not isinstance(row, dict):
                continue
            vid = str(row.get("vehicle_id", "")).strip()
            if not vid or vid in seen_ids or vid not in vehicle_id_set:
                continue
            mode = str(row.get("mode", reroute_mode))
            planned_reroutes.append((vid, mode))
            seen_ids.add(vid)

    if emergency_active and emergency_vehicle_ids:
        # Emergency flow should clear the path for emergency vehicles, not reroute all traffic.
        planned_reroutes = [
            (vid, mode)
            for vid, mode in planned_reroutes
            if vid in emergency_vehicle_ids
        ]
        if not planned_reroutes:
            planned_reroutes = [(vid, "dijkstra") for vid in vehicle_ids if vid in emergency_vehicle_ids]
    else:
        if reroute_fraction <= 0.0:
            return {"count": 0, "vehicle_ids": []}

        # IMPROVEMENT: Filter vehicles that would benefit from rerouting
        # Skip vehicles near destination or with short remaining routes
        eligible_vehicles = _filter_vehicles_for_rerouting(
            traci_module,
            vehicle_ids,
            min_remaining_distance=150.0,
            min_remaining_edges=2,
        )

        if not eligible_vehicles:
            return {"count": 0, "vehicle_ids": []}

        # Calculate target count from eligible vehicles
        target_count = max(1, int(len(eligible_vehicles) * reroute_fraction))

        # IMPROVEMENT: Prioritize vehicles with highest delay
        # Vehicles stuck in traffic benefit most from rerouting
        if planned_reroutes:
            # Use server directives but filter to eligible
            planned_reroutes = [
                (vid, mode) for vid, mode in planned_reroutes
                if vid in eligible_vehicles
            ][:target_count]
        else:
            # Prioritize by accumulated delay
            priority_vehicles = _prioritize_vehicles_by_delay(
                traci_module, eligible_vehicles, target_count
            )
            planned_reroutes = [(vid, reroute_mode) for vid in priority_vehicles]

    applied = 0
    rerouted_ids: list[str] = []
    for vid, mode in planned_reroutes:
        if (
            reroute_cooldown_until is not None
            and sim_time is not None
            and float(reroute_cooldown_until.get(vid, -1.0)) > sim_time
        ):
            continue

        if not _is_reroute_safe_now(traci_module, vid):
            continue

        try:
            if mode in {"gnn_effort", "effort"}:
                traci_module.vehicle.rerouteEffort(vid)
            elif mode == "dijkstra":
                if not _reroute_with_dijkstra_fallback(traci_module, vid):
                    continue
            else:
                traci_module.vehicle.rerouteTraveltime(vid)
            applied += 1
            rerouted_ids.append(vid)
            if reroute_cooldown_until is not None and sim_time is not None:
                reroute_cooldown_until[vid] = sim_time + max(1.0, reroute_cooldown_seconds)
        except Exception:
            if fallback_algorithm == "dijkstra":
                if _reroute_with_dijkstra_fallback(traci_module, vid):
                    applied += 1
                    rerouted_ids.append(vid)
                    if reroute_cooldown_until is not None and sim_time is not None:
                        reroute_cooldown_until[vid] = sim_time + max(1.0, reroute_cooldown_seconds)
            continue

    return {"count": applied, "vehicle_ids": rerouted_ids}


def _resolve_net_file_from_sumocfg(sumocfg_path: Path) -> Path | None:
    try:
        root = ET.parse(sumocfg_path).getroot()
    except Exception:
        return None

    net_node = root.find("./input/net-file")
    if net_node is None:
        return None

    value = net_node.attrib.get("value")
    if not value:
        return None

    net_path = Path(value)
    if not net_path.is_absolute():
        net_path = sumocfg_path.parent / net_path
    return net_path.resolve()


def _parse_world_bounds_from_net(net_file: Path) -> tuple[float, float, float, float] | None:
    try:
        root = ET.parse(net_file).getroot()
    except Exception:
        return None

    location = root.find("location")
    if location is None:
        return None

    conv_boundary = location.attrib.get("convBoundary")
    if not conv_boundary:
        return None

    try:
        min_x, min_y, max_x, max_y = [float(v) for v in conv_boundary.split(",")]
    except Exception:
        return None

    # SUMO GUI camera controls expect network (converted) coordinates.
    return (min_x, min_y, max_x, max_y)


def _resolve_additional_files_from_sumocfg(sumocfg_path: Path) -> list[Path]:
    try:
        root = ET.parse(sumocfg_path).getroot()
    except Exception:
        return []

    node = root.find("./input/additional-files")
    if node is None:
        return []

    raw_value = node.attrib.get("value", "")
    if not raw_value.strip():
        return []

    resolved: list[Path] = []
    for piece in raw_value.split(","):
        part = piece.strip()
        if not part:
            continue
        p = Path(part)
        if not p.is_absolute():
            p = (sumocfg_path.parent / p).resolve()
        resolved.append(p)
    return resolved


def _resolve_route_files_from_sumocfg(sumocfg_path: Path) -> list[Path]:
    try:
        root = ET.parse(sumocfg_path).getroot()
    except Exception:
        return []

    node = root.find("./input/route-files")
    if node is None:
        return []

    raw_value = node.attrib.get("value", "")
    if not raw_value.strip():
        return []

    resolved: list[Path] = []
    for piece in raw_value.split(","):
        part = piece.strip()
        if not part:
            continue
        p = Path(part)
        if not p.is_absolute():
            p = (sumocfg_path.parent / p).resolve()
        resolved.append(p)
    return resolved


def _resolve_net_ids(net_file: Path) -> tuple[set[str], set[str]]:
    root = ET.parse(net_file).getroot()

    junction_ids: set[str] = set()
    for junction in root.findall("junction"):
        jid = junction.attrib.get("id")
        if not jid:
            continue
        jtype = junction.attrib.get("type", "")
        if jtype == "internal":
            continue
        junction_ids.add(jid)

    edge_ids: set[str] = set()
    for edge in root.findall("edge"):
        eid = edge.attrib.get("id")
        if not eid or eid.startswith(":"):
            continue
        if edge.attrib.get("function", "") == "internal":
            continue
        edge_ids.add(eid)

    return junction_ids, edge_ids


def _lane_allows_passenger(lane_node: ET.Element) -> bool:
    allow = lane_node.attrib.get("allow", "").strip()
    disallow = lane_node.attrib.get("disallow", "").strip()

    if allow:
        allowed = set(allow.split())
        return "passenger" in allowed or "all" in allowed

    if disallow:
        disallowed = set(disallow.split())
        return "passenger" not in disallowed and "all" not in disallowed

    # SUMO default lane permissions allow passenger unless restricted.
    return True


def _resolve_passenger_junction_connectivity(net_file: Path) -> tuple[dict[str, int], dict[str, int]]:
    root = ET.parse(net_file).getroot()

    incoming_counts: dict[str, int] = {}
    outgoing_counts: dict[str, int] = {}

    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":"):
            continue
        if edge.attrib.get("function", "") == "internal":
            continue

        # Only consider edges that have at least one lane usable by passenger vehicles.
        if not any(_lane_allows_passenger(lane) for lane in edge.findall("lane")):
            continue

        from_junction = edge.attrib.get("from")
        to_junction = edge.attrib.get("to")
        if from_junction:
            outgoing_counts[from_junction] = outgoing_counts.get(from_junction, 0) + 1
        if to_junction:
            incoming_counts[to_junction] = incoming_counts.get(to_junction, 0) + 1

    return incoming_counts, outgoing_counts


def _resolve_junction_positions(net_file: Path) -> dict[str, tuple[float, float]]:
    root = ET.parse(net_file).getroot()
    positions: dict[str, tuple[float, float]] = {}

    for junction in root.findall("junction"):
        jid = junction.attrib.get("id")
        if not jid:
            continue
        try:
            x = float(junction.attrib.get("x", ""))
            y = float(junction.attrib.get("y", ""))
        except Exception:
            continue
        positions[jid] = (x, y)

    return positions


def _suggest_nearest_junctions(
    *,
    target_junction: str,
    purpose: str,
    count: int,
    positions: dict[str, tuple[float, float]],
    incoming_counts: dict[str, int],
    outgoing_counts: dict[str, int],
) -> list[tuple[float, str, int, int]]:
    if target_junction not in positions:
        return []

    tx, ty = positions[target_junction]
    candidates: list[tuple[float, str, int, int]] = []

    for jid, (x, y) in positions.items():
        if jid == target_junction:
            continue

        incoming = incoming_counts.get(jid, 0)
        outgoing = outgoing_counts.get(jid, 0)

        if purpose == "source" and outgoing <= 0:
            continue
        if purpose == "destination" and incoming <= 0:
            continue
        if purpose == "checkpoint" and (incoming <= 0 or outgoing <= 0):
            continue
        if purpose == "any" and (incoming <= 0 and outgoing <= 0):
            continue

        dist = math.hypot(x - tx, y - ty)
        candidates.append((dist, jid, incoming, outgoing))

    candidates.sort(key=lambda item: item[0])
    return candidates[: max(1, count)]


def _auto_fix_controlled_junctions(
    *,
    net_file: Path,
    source: str,
    destination: str,
    via_list: list[str],
) -> tuple[str, str, list[str], list[tuple[str, str, str]]]:
    """Auto-fix junction IDs to nearest drivable alternatives.

    Returns (source, destination, via_list, replacements) where replacements contain
    tuples of (role, old_id, new_id).
    """
    junction_ids, _edge_ids = _resolve_net_ids(net_file)
    all_as_junctions = source in junction_ids and destination in junction_ids and all(
        via in junction_ids for via in via_list
    )
    if not all_as_junctions:
        # Fallback applies only to junction-mode input.
        return source, destination, via_list, []

    incoming_counts, outgoing_counts = _resolve_passenger_junction_connectivity(net_file)
    positions = _resolve_junction_positions(net_file)

    replacements: list[tuple[str, str, str]] = []

    fixed_source = source
    if outgoing_counts.get(fixed_source, 0) <= 0:
        candidates = _suggest_nearest_junctions(
            target_junction=fixed_source,
            purpose="source",
            count=1,
            positions=positions,
            incoming_counts=incoming_counts,
            outgoing_counts=outgoing_counts,
        )
        if not candidates:
            raise ValueError(
                f"Controlled source junction '{source}' is invalid and no nearby valid source fallback was found."
            )
        fixed_source = candidates[0][1]
        replacements.append(("source", source, fixed_source))

    fixed_destination = destination
    if incoming_counts.get(fixed_destination, 0) <= 0:
        candidates = _suggest_nearest_junctions(
            target_junction=fixed_destination,
            purpose="destination",
            count=1,
            positions=positions,
            incoming_counts=incoming_counts,
            outgoing_counts=outgoing_counts,
        )
        if not candidates:
            raise ValueError(
                f"Controlled destination junction '{destination}' is invalid and no nearby valid destination fallback was found."
            )
        fixed_destination = candidates[0][1]
        replacements.append(("destination", destination, fixed_destination))

    fixed_via: list[str] = []
    used_ids = {fixed_source, fixed_destination}
    for via in via_list:
        current_via = via
        if incoming_counts.get(current_via, 0) <= 0 or outgoing_counts.get(current_via, 0) <= 0:
            candidates = _suggest_nearest_junctions(
                target_junction=current_via,
                purpose="checkpoint",
                count=12,
                positions=positions,
                incoming_counts=incoming_counts,
                outgoing_counts=outgoing_counts,
            )
            replacement = None
            for _dist, jid, _incoming, _outgoing in candidates:
                if jid not in used_ids:
                    replacement = jid
                    break
            if replacement is None:
                raise ValueError(
                    f"Controlled checkpoint junction '{via}' is invalid and no nearby valid checkpoint fallback was found."
                )
            current_via = replacement
            replacements.append(("checkpoint", via, current_via))

        if current_via not in used_ids:
            fixed_via.append(current_via)
            used_ids.add(current_via)

    return fixed_source, fixed_destination, fixed_via, replacements


def _parse_csv_values(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _build_runtime_run_id(*, scenario: str, seed: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{scenario}_seed{seed}"


def _resolve_project_path(path_value: str | Path, *, project_root: Path) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = project_root / path
    return path


def _to_bijective_base26_label(index_1_based: int) -> str:
    if index_1_based <= 0:
        raise ValueError("index must be >= 1")

    n = index_1_based
    out: list[str] = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out.append(chr(ord("A") + rem))
    out.reverse()
    return "".join(out)


def _load_rsu_config_from_json(
    config_path: Path,
    net_file: Path,
) -> list[tuple[str, str, float, float, str]]:
    """Load RSU configuration from JSON file with custom placements and names.

    Returns list of (alias/id, junction_id, x, y, display_name) tuples.
    Junction IDs are validated against the network; if exact match not found,
    finds nearest junction to specified coordinates.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        print(f"[SUMO][RSU] Error loading RSU config: {e}")
        return []

    rsus = config.get("rsus", [])
    if not rsus:
        print("[SUMO][RSU] No RSUs defined in config file")
        return []

    # Parse network junctions for validation/matching
    try:
        root = ET.parse(net_file).getroot()
    except Exception as e:
        print(f"[SUMO][RSU] Error parsing network file: {e}")
        return []

    junction_map: dict[str, tuple[float, float]] = {}
    for junction in root.findall("junction"):
        jid = junction.attrib.get("id")
        jtype = junction.attrib.get("type", "")
        x_str = junction.attrib.get("x")
        y_str = junction.attrib.get("y")
        if not jid or not x_str or not y_str:
            continue
        if jtype in {"internal", "dead_end"}:
            continue
        junction_map[jid] = (float(x_str), float(y_str))

    table: list[tuple[str, str, float, float, str]] = []
    for rsu in rsus:
        rsu_id = rsu.get("id", "")
        display_name = rsu.get("display_name", rsu_id)
        junction_id = rsu.get("junction_id", "")
        x = rsu.get("x", 0.0)
        y = rsu.get("y", 0.0)

        # Try exact junction match first
        if junction_id in junction_map:
            jx, jy = junction_map[junction_id]
            table.append((rsu_id, junction_id, jx, jy, display_name))
        else:
            # Find nearest junction to specified coordinates
            best_dist = float("inf")
            best_junction = None
            for jid, (jx, jy) in junction_map.items():
                dist = math.sqrt((jx - x) ** 2 + (jy - y) ** 2)
                if dist < best_dist:
                    best_dist = dist
                    best_junction = (jid, jx, jy)

            if best_junction and best_dist < 200.0:  # within 200m tolerance
                jid, jx, jy = best_junction
                table.append((rsu_id, jid, jx, jy, display_name))
                if best_dist > 10.0:
                    print(f"[SUMO][RSU] {rsu_id}: matched to {jid} ({best_dist:.1f}m away)")
            else:
                print(f"[SUMO][RSU] Warning: {rsu_id} - no junction found within 200m")

    print(f"[SUMO][RSU] Loaded {len(table)} RSUs from {config_path.name}")
    return table


def _load_rsu_graph_edges_from_json(
    config_path: Path,
    rsu_config_table: list[tuple[str, str, float, float, str]],
) -> list[tuple[str, str]] | None:
    """Load optional fixed RSU graph edges from config JSON.

    Supported edge formats:
      - ["SEALDAH", "BOWBAZAR"]  (RSU id, display name, or junction id)
      - {"from": "SEALDAH", "to": "BOWBAZAR"}

    Returns:
      - list[(junction_id_u, junction_id_v)] when `graph_edges` exists
      - None when `graph_edges` key is absent
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as exc:
        print(f"[SUMO][RSU] Warning: failed to load graph_edges from {config_path.name}: {exc}")
        return None

    raw_edges = config.get("graph_edges")
    if raw_edges is None:
        return None
    if not isinstance(raw_edges, list):
        print("[SUMO][RSU] Warning: graph_edges must be a list; ignoring provided value.")
        return []

    rsu_to_junction: dict[str, str] = {}
    display_name_to_junction: dict[str, str] = {}
    known_junction_ids: set[str] = set()
    for rsu_id, junction_id, _x, _y, display_name in rsu_config_table:
        rid = str(rsu_id).strip()
        jid = str(junction_id).strip()
        dname = str(display_name).strip()

        if rid and jid:
            rsu_to_junction[rid] = jid
            rsu_to_junction[rid.upper()] = jid
        if dname and jid:
            display_name_to_junction[dname] = jid
            display_name_to_junction[dname.upper()] = jid
        if jid:
            known_junction_ids.add(jid)

    def _resolve_graph_node(raw_node: Any) -> str:
        token = str(raw_node).strip()
        if not token:
            return ""
        if token in known_junction_ids:
            return token
        if token in rsu_to_junction:
            return rsu_to_junction[token]
        upper = token.upper()
        if upper in rsu_to_junction:
            return rsu_to_junction[upper]
        if token in display_name_to_junction:
            return display_name_to_junction[token]
        if upper in display_name_to_junction:
            return display_name_to_junction[upper]
        return ""

    edge_set: set[tuple[str, str]] = set()
    for idx, raw_edge in enumerate(raw_edges, start=1):
        from_node: Any = ""
        to_node: Any = ""

        if isinstance(raw_edge, (list, tuple)) and len(raw_edge) >= 2:
            from_node, to_node = raw_edge[0], raw_edge[1]
        elif isinstance(raw_edge, dict):
            from_node = raw_edge.get("from", raw_edge.get("source", raw_edge.get("u", "")))
            to_node = raw_edge.get("to", raw_edge.get("target", raw_edge.get("v", "")))
        else:
            print(f"[SUMO][RSU] Warning: graph_edges[{idx}] has invalid format; skipping")
            continue

        u = _resolve_graph_node(from_node)
        v = _resolve_graph_node(to_node)
        if not u or not v or u == v:
            print(
                f"[SUMO][RSU] Warning: graph_edges[{idx}] unresolved/invalid "
                f"({from_node} -> {to_node}); skipping"
            )
            continue

        edge = (u, v) if u <= v else (v, u)
        edge_set.add(edge)

    edges = sorted(edge_set)
    print(f"[SUMO][RSU] Loaded {len(edges)} fixed RSU graph edges from {config_path.name}")
    return edges


def _build_rsu_alias_table(
    *,
    net_file: Path,
    min_incoming_lanes: int,
    max_count: int,
    min_spacing_m: float,
) -> list[tuple[str, str, float, float]]:
    try:
        root = ET.parse(net_file).getroot()
    except Exception:
        return []

    selected, _candidate_count = _select_rsu_junctions(
        root,
        min_incoming_lanes=min_incoming_lanes,
        max_count=max_count,
        min_spacing_m=min_spacing_m,
    )

    table: list[tuple[str, str, float, float]] = []
    for idx, (jid, x, y) in enumerate(selected, start=1):
        alias = _to_bijective_base26_label(idx)
        table.append((alias, jid, x, y))
    return table


def _resolve_rsu_identifier(token: str, alias_to_junction: dict[str, str]) -> str:
    normalized = token.strip()
    if not normalized:
        return normalized

    upper = normalized.upper()
    if upper in alias_to_junction:
        return alias_to_junction[upper]

    if upper.startswith("RSU_") and upper[4:] in alias_to_junction:
        return alias_to_junction[upper[4:]]
    if upper.startswith("RSU-") and upper[4:] in alias_to_junction:
        return alias_to_junction[upper[4:]]
    if upper.startswith("RSU") and upper[3:] in alias_to_junction:
        return alias_to_junction[upper[3:]]

    return normalized


def _resolve_rsu_route_inputs(
    *,
    source: str,
    destination: str,
    via_list: list[str],
    alias_to_junction: dict[str, str],
) -> tuple[str, str, list[str], list[tuple[str, str, str]]]:
    replacements: list[tuple[str, str, str]] = []

    resolved_source = _resolve_rsu_identifier(source, alias_to_junction)
    if resolved_source != source:
        replacements.append(("source", source, resolved_source))

    resolved_destination = _resolve_rsu_identifier(destination, alias_to_junction)
    if resolved_destination != destination:
        replacements.append(("destination", destination, resolved_destination))

    resolved_via: list[str] = []
    for via in via_list:
        resolved = _resolve_rsu_identifier(via, alias_to_junction)
        if resolved != via:
            replacements.append(("checkpoint", via, resolved))
        resolved_via.append(resolved)

    return resolved_source, resolved_destination, resolved_via, replacements


def _resolve_route_mode_and_attrs(
    *,
    net_file: Path,
    source: str,
    destination: str,
    via_list: list[str],
) -> tuple[str, str, str, str]:
    junction_ids, edge_ids = _resolve_net_ids(net_file)

    all_as_junctions = source in junction_ids and destination in junction_ids and all(
        via in junction_ids for via in via_list
    )
    all_as_edges = source in edge_ids and destination in edge_ids and all(via in edge_ids for via in via_list)

    if all_as_junctions:
        incoming_counts, outgoing_counts = _resolve_passenger_junction_connectivity(net_file)

        src_outgoing = outgoing_counts.get(source, 0)
        dst_incoming = incoming_counts.get(destination, 0)
        if src_outgoing <= 0:
            raise ValueError(
                f"Source junction '{source}' has no passenger-drivable outgoing edges. "
                "Pick a source junction connected to a drivable road."
            )
        if dst_incoming <= 0:
            raise ValueError(
                f"Destination junction '{destination}' has no passenger-drivable incoming edges. "
                "Pick a destination junction reachable via drivable roads."
            )

        for via in via_list:
            via_incoming = incoming_counts.get(via, 0)
            via_outgoing = outgoing_counts.get(via, 0)
            if via_incoming <= 0 or via_outgoing <= 0:
                raise ValueError(
                    f"Checkpoint junction '{via}' is not usable as an intermediate passenger waypoint "
                    "(needs both incoming and outgoing passenger-drivable edges)."
                )

        route_mode = "junction"
        src_attr = 'fromJunction="{}"'.format(escape(source))
        dst_attr = 'toJunction="{}"'.format(escape(destination))
        via_attr = ""
        if via_list:
            via_attr = ' viaJunctions="{}"'.format(escape(" ".join(via_list)))
        return route_mode, src_attr, dst_attr, via_attr

    if all_as_edges:
        route_mode = "edge"
        src_attr = 'from="{}"'.format(escape(source))
        dst_attr = 'to="{}"'.format(escape(destination))
        via_attr = ""
        if via_list:
            via_attr = ' via="{}"'.format(escape(" ".join(via_list)))
        return route_mode, src_attr, dst_attr, via_attr

    raise ValueError(
        "Route IDs must consistently be valid junction IDs or valid edge IDs. "
        "Use either (source, destination, via) all as junctions or all as edges."
    )


def _generate_guided_flow_route_file(
    *,
    net_file: Path,
    scenario_name: str,
    route_file_suffix: str,
    flow_id: str,
    vehicle_type_id: str,
    vehicle_class: str,
    vehicle_color: str,
    max_speed: float,
    vehicle_count: int,
    source: str,
    destination: str,
    via_list: list[str],
    begin_time: float,
    end_time: float,
) -> tuple[Path, str]:
    if vehicle_count <= 0:
        raise ValueError("vehicle count must be positive")
    if begin_time < 0:
        raise ValueError("begin time must be >= 0")
    if end_time <= begin_time:
        raise ValueError("end time must be greater than begin time")

    route_mode, src_attr, dst_attr, via_attr = _resolve_route_mode_and_attrs(
        net_file=net_file,
        source=source,
        destination=destination,
        via_list=via_list,
    )

    route_file = net_file.parent.parent / "scenarios" / f"{scenario_name}_{route_file_suffix}.rou.xml"
    route_file.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "<routes>",
        (
            f'    <vType id="{escape(vehicle_type_id)}" vClass="{escape(vehicle_class)}" '
            f'color="{escape(vehicle_color)}" maxSpeed="{max_speed:.2f}" accel="2.6" decel="4.5" sigma="0.2"/>'
        ),
        (
            "    "
            f'<flow id="{escape(flow_id)}" type="{escape(vehicle_type_id)}" begin="{begin_time:.2f}" end="{end_time:.2f}" '
            f'number="{vehicle_count}" departLane="best" departSpeed="max" departPos="base" '
            f'{src_attr} {dst_attr}{via_attr}/>'
        ),
        "</routes>",
    ]
    route_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return route_file, route_mode


def _generate_controlled_group_route_file(
    *,
    net_file: Path,
    scenario_name: str,
    vehicle_count: int,
    source: str,
    destination: str,
    via_list: list[str],
    begin_time: float,
    end_time: float,
) -> tuple[Path, str]:
    return _generate_guided_flow_route_file(
        net_file=net_file,
        scenario_name=scenario_name,
        route_file_suffix="controlled_group",
        flow_id="controlled_group_flow",
        vehicle_type_id="controlled_ai_vehicle",
        vehicle_class="passenger",
        vehicle_color="0,51,153",
        max_speed=16.67,
        vehicle_count=vehicle_count,
        source=source,
        destination=destination,
        via_list=via_list,
        begin_time=begin_time,
        end_time=end_time,
    )


def _generate_emergency_group_route_file(
    *,
    net_file: Path,
    scenario_name: str,
    vehicle_count: int,
    source: str,
    destination: str,
    via_list: list[str],
    begin_time: float,
    end_time: float,
) -> tuple[Path, str]:
    return _generate_guided_flow_route_file(
        net_file=net_file,
        scenario_name=scenario_name,
        route_file_suffix="emergency_group",
        flow_id="emergency_group_flow",
        vehicle_type_id="emergency_priority_vehicle",
        vehicle_class="emergency",
        vehicle_color="255,255,0",
        max_speed=22.22,
        vehicle_count=vehicle_count,
        source=source,
        destination=destination,
        via_list=via_list,
        begin_time=begin_time,
        end_time=end_time,
    )


def _highlight_vehicle_circle(
    traci_module,
    vehicle_id: str,
    *,
    set_vehicle_color: bool,
    vehicle_color: tuple[int, int, int, int],
    highlight_color: tuple[int, int, int, int],
    radius_m: float,
    alpha_max: int = -1,
    duration_s: float = -1.0,
    highlight_type: int = 0,
) -> None:
    # Keep the vehicle body color stable and high-contrast.
    if set_vehicle_color:
        try:
            traci_module.vehicle.setColor(vehicle_id, vehicle_color)
        except Exception:
            pass

    try:
        # Persistent highlighting avoids fade-reset flicker when called each step.
        if alpha_max > 0 and duration_s > 0:
            traci_module.vehicle.highlight(
                vehicle_id,
                highlight_color,
                radius_m,
                alpha_max,
                duration_s,
                highlight_type,
            )
        else:
            traci_module.vehicle.highlight(vehicle_id, highlight_color, radius_m)
    except Exception:
        # If highlighting is unavailable on this build, keep body-color fallback only.
        pass


def _apply_visual_vehicle_markers(traci_module, vehicle_ids: list[str]) -> dict[str, int]:
    marked_controlled = 0
    marked_emergency = 0

    for vid in vehicle_ids:
        _highlight_vehicle_circle(
            traci_module,
            vid,
            set_vehicle_color=True,
            vehicle_color=(0, 51, 153, 255),
            highlight_color=(64, 224, 255, 170),
            radius_m=5.2,
        )
        marked_controlled += 1

    return {
        "controlled_marked": marked_controlled,
        "emergency_marked": marked_emergency,
    }


def _classify_vehicle_marker_targets(
    traci_module,
    *,
    vehicle_ids: list[str],
    marker_type_cache: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Classify active vehicles for marker/emergency logic with cache reuse.

    Cache values are one of: controlled, emergency, other.
    """

    if len(marker_type_cache) > max(2000, len(vehicle_ids) * 2):
        active_ids = set(vehicle_ids)
        for stale_id in list(marker_type_cache.keys()):
            if stale_id not in active_ids:
                marker_type_cache.pop(stale_id, None)

    controlled_ids: list[str] = []
    emergency_ids: list[str] = []

    for vid in vehicle_ids:
        marker_kind = marker_type_cache.get(vid)
        if marker_kind is None:
            marker_kind = "other"
            type_id = ""
            try:
                type_id = str(traci_module.vehicle.getTypeID(vid)).lower()
            except Exception:
                type_id = ""

            if type_id == "controlled_ai_vehicle":
                marker_kind = "controlled"
            elif type_id == "emergency_priority_vehicle":
                marker_kind = "emergency"
            else:
                try:
                    vehicle_class = str(traci_module.vehicle.getVehicleClass(vid)).lower()
                except Exception:
                    vehicle_class = ""
                if vehicle_class == "emergency" or any(
                    token in type_id for token in ("emergency", "ambulance", "fire", "police")
                ):
                    marker_kind = "emergency"

            marker_type_cache[vid] = marker_kind

        if marker_kind == "controlled":
            controlled_ids.append(vid)
        elif marker_kind == "emergency":
            emergency_ids.append(vid)

    return controlled_ids, emergency_ids


def _apply_emergency_vehicle_markers(
    traci_module,
    emergency_vehicle_ids: list[str],
) -> dict[str, int]:
    marked = 0
    for vid in emergency_vehicle_ids:
        _highlight_vehicle_circle(
            traci_module,
            vid,
            set_vehicle_color=True,
            vehicle_color=(255, 255, 0, 255),
            highlight_color=(255, 69, 0, 190),
            radius_m=6.6,
        )
        marked += 1

    return {
        "controlled_marked": 0,
        "emergency_marked": marked,
    }


def _apply_active_reroute_highlights(
    traci_module,
    *,
    sim_time: float,
    active_vehicle_ids: set[str],
    reroute_highlight_until: dict[str, float],
) -> int:
    highlighted = 0
    for vid, until in list(reroute_highlight_until.items()):
        if sim_time >= until or vid not in active_vehicle_ids:
            reroute_highlight_until.pop(vid, None)
            continue

        _highlight_vehicle_circle(
            traci_module,
            vid,
            set_vehicle_color=True,
            vehicle_color=(255, 0, 0, 255),
            highlight_color=(255, 80, 80, 210),
            radius_m=4.7,
            highlight_type=2,
        )
        highlighted += 1
    return highlighted


def _get_edges_connected_to_junction(net_file: Path, junction_id: str) -> list[str]:
    """Return all non-internal edge IDs whose from- or to-junction matches junction_id."""
    try:
        root = ET.parse(net_file).getroot()
    except Exception:
        return []
    result: list[str] = []
    for edge in root.findall("edge"):
        eid = edge.attrib.get("id", "")
        if not eid or eid.startswith(":"):
            continue
        if edge.attrib.get("function", "") == "internal":
            continue
        if edge.attrib.get("from") == junction_id or edge.attrib.get("to") == junction_id:
            result.append(eid)
    return result


def _force_edge_congestion(traci_module, edge_ids: list[str], penalty_seconds: float = 9999.0) -> int:
    """Set an extreme travel-time penalty on the given edges so all rerouters avoid them.

    Returns the number of edges successfully penalized.
    """
    penalized = 0
    for eid in edge_ids:
        try:
            traci_module.edge.adaptTraveltime(eid, penalty_seconds)
            penalized += 1
        except Exception:
            continue
    return penalized


def _collect_congested_rsu_ids(
    rsu_batch_metrics: list[dict[str, object]],
    forced_congested_rsus: list[str] | None = None,
    *,
    min_vehicle_count: int = 5,
    max_avg_speed_mps: float = 5.0,
) -> set[str]:
    """Return RSU junction IDs considered congested for the current batch."""
    congested: set[str] = set()
    for jid in forced_congested_rsus or []:
        normalized = str(jid).strip()
        if normalized:
            congested.add(normalized)

    for metric in rsu_batch_metrics:
        if not isinstance(metric, dict):
            continue
        jid = str(metric.get("rsu_id", "")).strip()
        if not jid:
            continue
        try:
            vehicle_count = int(metric.get("vehicle_count", 0))
        except Exception:
            vehicle_count = 0
        try:
            avg_speed_mps = float(metric.get("avg_speed_mps", 0.0))
        except Exception:
            avg_speed_mps = 0.0

        if vehicle_count >= min_vehicle_count and avg_speed_mps <= max_avg_speed_mps:
            congested.add(jid)

    return congested


def _collect_edges_for_congested_rsus(
    congested_rsu_ids: set[str],
    rsu_edges_by_jid: dict[str, list[str]],
) -> list[str]:
    """Return deduplicated edge IDs connected to currently congested RSUs."""
    deduped: list[str] = []
    seen: set[str] = set()
    for jid in congested_rsu_ids:
        for edge_id in rsu_edges_by_jid.get(jid, []):
            if edge_id in seen:
                continue
            seen.add(edge_id)
            deduped.append(edge_id)
    return deduped


def _apply_temporary_edge_penalties(
    traci_module,
    edge_ids: list[str],
    *,
    sim_time: float,
    penalty_seconds: float,
    hold_seconds: float,
) -> int:
    """Apply temporary travel-time penalties so rerouting avoids these edges."""
    if not edge_ids:
        return 0

    begin = max(0.0, float(sim_time))
    end = begin + max(0.5, float(hold_seconds))
    penalty = max(1.0, float(penalty_seconds))

    penalized = 0
    for edge_id in edge_ids:
        try:
            traci_module.edge.adaptTraveltime(edge_id, penalty, begin, end)
            penalized += 1
        except Exception:
            continue
    return penalized


def _reroute_vehicles_away_from_edges(
    traci_module,
    vehicle_ids: list[str],
    *,
    blocked_edge_ids: list[str],
    sim_time: float | None = None,
    reroute_cooldown_until: dict[str, float] | None = None,
    reroute_cooldown_seconds: float = 25.0,
    max_fraction: float = 0.7,
    skip_vehicle_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Reroute vehicles whose remaining route still passes through blocked edges."""
    blocked_set = set(blocked_edge_ids)
    if not blocked_set or not vehicle_ids:
        return {"count": 0, "vehicle_ids": [], "candidate_count": 0}

    skip_set = set(skip_vehicle_ids or set())
    candidates: list[str] = []
    for vid in vehicle_ids:
        if vid in skip_set:
            continue
        if (
            reroute_cooldown_until is not None
            and sim_time is not None
            and float(reroute_cooldown_until.get(vid, -1.0)) > sim_time
        ):
            continue
        if not _is_reroute_safe_now(traci_module, vid):
            continue

        try:
            route_edges = list(traci_module.vehicle.getRoute(vid))
            route_idx = int(traci_module.vehicle.getRouteIndex(vid))
        except Exception:
            continue

        if not route_edges:
            continue
        route_idx = max(0, route_idx)
        remaining_edges = route_edges[route_idx:]
        if not remaining_edges:
            continue
        if any(edge_id in blocked_set for edge_id in remaining_edges):
            candidates.append(vid)

    if not candidates:
        return {"count": 0, "vehicle_ids": [], "candidate_count": 0}

    max_fraction = max(0.05, min(1.0, float(max_fraction)))
    reroute_limit = max(1, int(len(candidates) * max_fraction))
    selected = candidates[:reroute_limit]

    applied = 0
    rerouted_ids: list[str] = []
    for vid in selected:
        try:
            traci_module.vehicle.rerouteTraveltime(vid)
            applied += 1
            rerouted_ids.append(vid)
            if reroute_cooldown_until is not None and sim_time is not None:
                reroute_cooldown_until[vid] = sim_time + max(1.0, reroute_cooldown_seconds)
        except Exception:
            if _reroute_with_dijkstra_fallback(traci_module, vid):
                applied += 1
                rerouted_ids.append(vid)
                if reroute_cooldown_until is not None and sim_time is not None:
                    reroute_cooldown_until[vid] = sim_time + max(1.0, reroute_cooldown_seconds)

    return {
        "count": applied,
        "vehicle_ids": rerouted_ids,
        "candidate_count": len(candidates),
    }


def _build_edge_junction_map(net_file: Path) -> dict[str, tuple[str, str]]:
    """Return {edge_id: (from_junction_id, to_junction_id)} for every non-internal edge."""
    try:
        root = ET.parse(net_file).getroot()
    except Exception:
        return {}
    result: dict[str, tuple[str, str]] = {}
    for edge in root.findall("edge"):
        eid = edge.attrib.get("id", "")
        if not eid or eid.startswith(":"):
            continue
        if edge.attrib.get("function", "") == "internal":
            continue
        from_j = edge.attrib.get("from", "")
        to_j = edge.attrib.get("to", "")
        if from_j and to_j:
            result[eid] = (from_j, to_j)
    return result


def _get_rsus_on_route(
    route_edges: list[str],
    junction_to_rsu: dict[str, str],
    edge_to_junctions: dict[str, tuple[str, str]],
) -> list[str]:
    """Return ordered, deduplicated RSU display-names whose junctions appear on route_edges."""
    seen: set[str] = set()
    rsus: list[str] = []
    for edge in route_edges:
        from_j, to_j = edge_to_junctions.get(edge, ("", ""))
        for jid in (from_j, to_j):
            if jid and jid in junction_to_rsu:
                name = junction_to_rsu[jid]
                if name not in seen:
                    seen.add(name)
                    rsus.append(name)
    return rsus


def _build_circle_shape_points(*, x: float, y: float, radius_m: float, points: int = 24) -> str:
    coords: list[str] = []
    for i in range(points):
        theta = 2.0 * math.pi * (i / points)
        cx = x + radius_m * math.cos(theta)
        cy = y + radius_m * math.sin(theta)
        coords.append(f"{cx:.2f},{cy:.2f}")
    return " ".join(coords)


def _distance_xy(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.hypot(dx, dy)


def _parse_shape_points(shape: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for raw in shape.split():
        if "," not in raw:
            continue
        sx, sy = raw.split(",", 1)
        try:
            points.append((float(sx), float(sy)))
        except Exception:
            continue
    return points


def _normalize_vector(dx: float, dy: float) -> tuple[float, float] | None:
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        return None
    return (dx / length, dy / length)


def _collect_connected_lane_samples_and_normals(
    root,
    *,
    junction_id: str,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    samples: list[tuple[float, float]] = []
    normals: list[tuple[float, float]] = []

    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":"):
            continue
        if edge.attrib.get("function", "") == "internal":
            continue

        from_junction = edge.attrib.get("from")
        to_junction = edge.attrib.get("to")
        if from_junction != junction_id and to_junction != junction_id:
            continue

        for lane in edge.findall("lane"):
            points = _parse_shape_points(lane.attrib.get("shape", ""))
            if len(points) < 2:
                continue

            if from_junction == junction_id:
                near = points[0]
                away = points[1]
                samples.extend(points[: min(4, len(points))])
            else:
                near = points[-1]
                away = points[-2]
                samples.extend(points[max(0, len(points) - 4) :])

            direction = _normalize_vector(away[0] - near[0], away[1] - near[1])
            if direction is not None:
                nx, ny = -direction[1], direction[0]
                normals.append((nx, ny))
                normals.append((-nx, -ny))

            # One lane is enough to infer side-of-road orientation for this edge.
            break

    return samples, normals


def _select_rsu_label_position(
    root,
    *,
    junction_id: str,
    x: float,
    y: float,
    alias_index: int,
) -> tuple[float, float, tuple[float, float]]:
    lane_samples, normal_candidates = _collect_connected_lane_samples_and_normals(root, junction_id=junction_id)

    if not normal_candidates:
        # Deterministic fallback orientation based on alias index.
        angle = math.radians((alias_index * 37) % 360)
        normal_candidates = [
            (math.cos(angle), math.sin(angle)),
            (-math.cos(angle), -math.sin(angle)),
        ]

    offsets = (18.0, 24.0, 30.0)
    best_score = -1.0
    best_x = x
    best_y = y - offsets[0]
    best_dir = normal_candidates[0]

    for nx, ny in normal_candidates:
        for offset in offsets:
            cx = x + nx * offset
            cy = y + ny * offset

            if lane_samples:
                clearance = min(_distance_xy((cx, cy), sample) for sample in lane_samples)
            else:
                clearance = offset

            # Prefer points away from lane centerlines and slightly away from junction center.
            score = clearance + 0.05 * offset
            if score > best_score:
                best_score = score
                best_x = cx
                best_y = cy
                best_dir = (nx, ny)

    return best_x, best_y, best_dir


def _select_rsu_junctions(
    root,
    *,
    min_incoming_lanes: int,
    max_count: int,
    min_spacing_m: float,
) -> tuple[list[tuple[str, float, float]], int]:
    candidates: list[tuple[str, float, float, int, bool]] = []

    for junction in root.findall("junction"):
        jid = junction.attrib.get("id")
        jtype = junction.attrib.get("type", "")
        x = junction.attrib.get("x")
        y = junction.attrib.get("y")
        if not jid or not x or not y:
            continue

        # Ignore helper and terminal nodes.
        if jtype in {"internal", "dead_end"}:
            continue

        inc_lanes = junction.attrib.get("incLanes", "").split()
        inc_count = sum(1 for lane_id in inc_lanes if lane_id and not lane_id.startswith(":"))
        if inc_count < min_incoming_lanes:
            continue

        try:
            xv = float(x)
            yv = float(y)
        except Exception:
            continue

        is_signalized = jtype in {"traffic_light", "traffic_light_unregulated", "traffic_light_right_on_red"}
        candidates.append((jid, xv, yv, inc_count, is_signalized))

    # Prioritize signalized and higher-lane junctions.
    candidates.sort(key=lambda item: (item[4], item[3]), reverse=True)

    selected: list[tuple[str, float, float]] = []
    for jid, xv, yv, _inc_count, _is_signalized in candidates:
        pos = (xv, yv)
        if any(_distance_xy(pos, (sx, sy)) < min_spacing_m for _sid, sx, sy in selected):
            continue
        selected.append((jid, xv, yv))
        if len(selected) >= max_count:
            break

    return selected, len(candidates)


def _generate_rsu_poi_add_file(
    net_file: Path,
    scenario_name: str,
    rsu_range_m: float,
    min_incoming_lanes: int,
    max_count: int,
    min_spacing_m: float,
    rsu_whitelist: set[str] | None = None,
) -> tuple[Path | None, int, int]:
    try:
        root = ET.parse(net_file).getroot()
    except Exception:
        return None, 0, 0

    rsu_nodes, candidate_count = _select_rsu_junctions(
        root,
        min_incoming_lanes=min_incoming_lanes,
        max_count=max_count,
        min_spacing_m=min_spacing_m,
    )

    if not rsu_nodes:
        return None, 0, candidate_count

    output_path = net_file.parent.parent / "scenarios" / f"{scenario_name}_rsu_pois.add.xml"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "<additional>",
    ]
    placed_labels: list[tuple[float, float]] = []

    # Build full node list with aliases first (to maintain consistent alias assignment)
    nodes_with_alias: list[tuple[str, str, float, float]] = []
    for idx, (jid, x, y) in enumerate(rsu_nodes, start=1):
        alias = _to_bijective_base26_label(idx)
        nodes_with_alias.append((alias, jid, x, y))

    # Filter by whitelist if provided
    if rsu_whitelist:
        nodes_with_alias = [(alias, jid, x, y) for alias, jid, x, y in nodes_with_alias if alias in rsu_whitelist]

    for alias, jid, x, y in nodes_with_alias:
        label_text = f"RSU_{alias}"

        range_shape = _build_circle_shape_points(x=x, y=y, radius_m=rsu_range_m)
        # Find original index for label positioning
        original_idx = next((i+1 for i, (j, _, _) in enumerate(rsu_nodes) if j == jid), 1)
        label_x, label_y, label_dir = _select_rsu_label_position(
            root,
            junction_id=jid,
            x=x,
            y=y,
            alias_index=original_idx,
        )

        # Keep text labels spread out and away from dense center areas.
        for _ in range(4):
            if not any(_distance_xy((label_x, label_y), pos) < 16.0 for pos in placed_labels):
                break
            label_x += label_dir[0] * 7.0
            label_y += label_dir[1] * 7.0
        placed_labels.append((label_x, label_y))

        # Transparent RSU range with red circumference only.
        lines.append(
            f'    <poly id="rsu_range_{escape(jid)}" type="rsu_range" color="255,0,0,255" layer="12" lineWidth="2" fill="false" shape="{range_shape}"/>'
        )
        lines.append(
            f'    <poi id="rsu_label_anchor_{escape(alias)}" type="rsu_anchor" color="26,140,26,220" layer="13" x="{label_x:.2f}" y="{label_y:.2f}" width="2.4"/>'
        )
        lines.append(
            f'    <poi id="rsu_label_text_{escape(alias)}" type="{escape(label_text)}" color="0,0,0,0" layer="14" x="{label_x:.2f}" y="{label_y:.2f}">'
        )
        lines.append(f'        <param key="name" value="{escape(label_text)}"/>')
        lines.append("    </poi>")

    lines.append("</additional>")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path, len(nodes_with_alias), candidate_count


def _generate_rsu_poi_from_config(
    net_file: Path,
    scenario_name: str,
    rsu_range_m: float,
    rsu_config_table: list[tuple[str, str, float, float, str]],
) -> tuple[Path | None, int]:
    """Generate RSU POI file from custom configuration with display names.

    Args:
        net_file: Path to the SUMO network file
        scenario_name: Name of the scenario for output file naming
        rsu_range_m: RSU range radius in meters
        rsu_config_table: List of (rsu_id, junction_id, x, y, display_name) tuples

    Returns:
        (output_path, rsu_count) tuple
    """
    if not rsu_config_table:
        return None, 0

    try:
        root = ET.parse(net_file).getroot()
    except Exception:
        return None, 0

    output_path = net_file.parent.parent / "scenarios" / f"{scenario_name}_rsu_pois.add.xml"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["<additional>"]
    placed_labels: list[tuple[float, float]] = []

    for idx, (rsu_id, jid, x, y, display_name) in enumerate(rsu_config_table, start=1):
        # Use display_name for label, fallback to rsu_id
        label_text = display_name if display_name else f"RSU_{rsu_id}"

        range_shape = _build_circle_shape_points(x=x, y=y, radius_m=rsu_range_m)
        label_x, label_y, label_dir = _select_rsu_label_position(
            root,
            junction_id=jid,
            x=x,
            y=y,
            alias_index=idx,
        )

        # Keep text labels spread out
        for _ in range(4):
            if not any(_distance_xy((label_x, label_y), pos) < 16.0 for pos in placed_labels):
                break
            label_x += label_dir[0] * 7.0
            label_y += label_dir[1] * 7.0
        placed_labels.append((label_x, label_y))

        # RSU range circle (red outline)
        lines.append(
            f'    <poly id="rsu_range_{escape(jid)}" type="rsu_range" color="255,0,0,255" layer="12" lineWidth="2" fill="false" shape="{range_shape}"/>'
        )
        # Label anchor (green dot)
        lines.append(
            f'    <poi id="rsu_label_anchor_{escape(rsu_id)}" type="rsu_anchor" color="26,140,26,220" layer="13" x="{label_x:.2f}" y="{label_y:.2f}" width="2.4"/>'
        )
        # Label text with display name
        lines.append(
            f'    <poi id="rsu_label_text_{escape(rsu_id)}" type="{escape(label_text)}" color="0,0,0,0" layer="14" x="{label_x:.2f}" y="{label_y:.2f}">'
        )
        lines.append(f'        <param key="name" value="{escape(label_text)}"/>')
        lines.append("    </poi>")

    lines.append("</additional>")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path, len(rsu_config_table)


def main() -> None:
    args = parse_args()
    contract_path = Path(args.contract)
    project_root = Path(__file__).resolve().parent.parent

    config = load_scenario_config(contract_path, args.scenario)
    additional_files = _resolve_additional_files_from_sumocfg(config.sumocfg_path)
    route_files = _resolve_route_files_from_sumocfg(config.sumocfg_path)
    net_file = _resolve_net_file_from_sumocfg(config.sumocfg_path)
    use_junction_taz = False

    # RSU configuration - either from JSON config file or auto-detected
    rsu_alias_table: list[tuple[str, str, float, float]] = []
    rsu_config_table: list[tuple[str, str, float, float, str]] = []  # With display names
    rsu_graph_edges: list[tuple[str, str]] | None = None
    rsu_alias_map: dict[str, str] = {}
    use_custom_rsu_config = False

    if args.rsu_config and net_file is not None:
        # Load custom RSU configuration from JSON
        rsu_config_path = Path(args.rsu_config)
        if not rsu_config_path.is_absolute():
            rsu_config_path = project_root / rsu_config_path

        if rsu_config_path.exists():
            rsu_config_table = _load_rsu_config_from_json(rsu_config_path, net_file)
            if rsu_config_table:
                use_custom_rsu_config = True
                rsu_graph_edges = _load_rsu_graph_edges_from_json(rsu_config_path, rsu_config_table)
                # Convert to alias_table format for backward compatibility
                rsu_alias_table = [(rsu_id, jid, x, y) for rsu_id, jid, x, y, _name in rsu_config_table]
                rsu_alias_map = {rsu_id: jid for rsu_id, jid, _x, _y, _name in rsu_config_table}
        else:
            print(f"[SUMO][RSU] Warning: Config file not found: {rsu_config_path}")

    if not use_custom_rsu_config and net_file is not None:
        # Fall back to auto-detection
        rsu_spacing_for_alias = args.rsu_min_spacing_m
        if rsu_spacing_for_alias is None:
            rsu_spacing_for_alias = max(80.0, args.rsu_range_m * 1.8)

        rsu_alias_table = _build_rsu_alias_table(
            net_file=net_file,
            min_incoming_lanes=max(1, args.rsu_min_inc_lanes),
            max_count=max(1, args.rsu_max_count),
            min_spacing_m=max(1.0, rsu_spacing_for_alias),
        )

        # Apply RSU whitelist filter if specified
        if args.rsu_whitelist:
            whitelist_set = set()
            for token in args.rsu_whitelist.split(","):
                alias = token.strip().upper()
                # Handle RSU_X or just X format
                if alias.startswith("RSU_"):
                    alias = alias[4:]
                elif alias.startswith("RSU"):
                    alias = alias[3:]
                whitelist_set.add(alias)

            original_count = len(rsu_alias_table)
            rsu_alias_table = [
                (alias, jid, x, y) for alias, jid, x, y in rsu_alias_table
                if alias in whitelist_set
            ]
            if rsu_alias_table:
                print(f"[SUMO][RSU] Whitelist applied: {len(rsu_alias_table)}/{original_count} RSUs retained")
                print(f"[SUMO][RSU] Active RSUs: {', '.join('RSU_' + alias for alias, _, _, _ in rsu_alias_table)}")
            else:
                print(f"[SUMO][RSU] Warning: Whitelist filtered out all RSUs! Check aliases.")

        rsu_alias_map = {alias: jid for alias, jid, _x, _y in rsu_alias_table}

    # ── RSU path-lookup tables (junction_id → display_name, edge → junctions) ─
    # Used to translate vehicle routes into human-readable RSU hop sequences.
    if use_custom_rsu_config:
        junction_to_rsu_name: dict[str, str] = {
            jid: display_name
            for _rsu_id, jid, _x, _y, display_name in rsu_config_table
        }
    else:
        junction_to_rsu_name = {
            jid: alias
            for alias, jid, _x, _y in rsu_alias_table
        }
    edge_to_junctions: dict[str, tuple[str, str]] = (
        _build_edge_junction_map(net_file) if net_file is not None else {}
    )

    if args.list_rsus:
        if net_file is None:
            raise ValueError("--list-rsus requires a valid net-file in the scenario config")

        print(f"[SUMO] RSU configuration for scenario '{config.scenario}':")
        if use_custom_rsu_config:
            print(f"[SUMO] (Using custom config: {args.rsu_config})")
            for rsu_id, jid, x, y, display_name in rsu_config_table:
                print(f"  - {rsu_id}: {display_name} | junction={jid} x={x:.2f} y={y:.2f}")
        elif rsu_alias_table:
            for alias, jid, x, y in rsu_alias_table:
                print(f"  - RSU_{alias}: junction={jid} x={x:.2f} y={y:.2f}")
        else:
            print("[SUMO] No RSUs selected with current filters.")
        return

    if args.suggest_near_junction is not None:
        if net_file is None:
            raise ValueError("--suggest-near-junction requires a valid net-file in the scenario config")

        incoming_counts, outgoing_counts = _resolve_passenger_junction_connectivity(net_file)
        positions = _resolve_junction_positions(net_file)
        target = args.suggest_near_junction
        if target not in positions:
            raise ValueError(f"Junction '{target}' not found in network {net_file}")

        suggestions = _suggest_nearest_junctions(
            target_junction=target,
            purpose=args.suggest_purpose,
            count=args.suggest_count,
            positions=positions,
            incoming_counts=incoming_counts,
            outgoing_counts=outgoing_counts,
        )

        print(f"[SUMO] Suggestions near junction {target} (purpose={args.suggest_purpose}):")
        if not suggestions:
            print("[SUMO] No suitable nearby junctions found.")
        else:
            for dist, jid, incoming, outgoing in suggestions:
                print(
                    f"  - {jid}  distance={dist:.2f}m  incoming(passenger)={incoming}  outgoing(passenger)={outgoing}"
                )
        return

    traffic_scale = args.traffic_scale
    if traffic_scale <= 0:
        raise ValueError("--traffic-scale must be > 0")

    reduction_pct = args.traffic_reduction_pct
    if reduction_pct < 0 or reduction_pct >= 100:
        raise ValueError("--traffic-reduction-pct must be in [0, 100)")
    effective_scale = traffic_scale * (1.0 - (reduction_pct / 100.0))
    if effective_scale <= 0:
        raise ValueError("effective traffic scale must stay > 0")
    traffic_scale = effective_scale
    print(f"[SUMO] Traffic scale after {reduction_pct:.1f}% reduction: {traffic_scale:.4f}")

    if args.controlled_count < 0:
        raise ValueError("--controlled-count must be >= 0")
    if args.controlled_count > 0:
        if net_file is None:
            raise ValueError("controlled flow generation requires a valid net-file in the scenario config")
        if not args.controlled_source or not args.controlled_destination:
            raise ValueError(
                "--controlled-source and --controlled-destination are required when --controlled-count > 0"
            )

        controlled_via = _parse_csv_values(args.controlled_via_rsus)
        controlled_source = args.controlled_source
        controlled_destination = args.controlled_destination

        (
            controlled_source,
            controlled_destination,
            controlled_via,
            controlled_alias_replacements,
        ) = _resolve_rsu_route_inputs(
            source=controlled_source,
            destination=controlled_destination,
            via_list=controlled_via,
            alias_to_junction=rsu_alias_map,
        )
        for role, old_id, new_id in controlled_alias_replacements:
            print(f"[SUMO] Controlled RSU alias ({role}): {old_id} -> {new_id}")

        if args.auto_fallback_junctions:
            (
                controlled_source,
                controlled_destination,
                controlled_via,
                replacements,
            ) = _auto_fix_controlled_junctions(
                net_file=net_file,
                source=controlled_source,
                destination=controlled_destination,
                via_list=controlled_via,
            )
            for role, old_id, new_id in replacements:
                print(f"[SUMO] Auto-fallback ({role}): {old_id} -> {new_id}")

        controlled_file, route_mode = _generate_controlled_group_route_file(
            net_file=net_file,
            scenario_name=config.scenario,
            vehicle_count=args.controlled_count,
            source=controlled_source,
            destination=controlled_destination,
            via_list=controlled_via,
            begin_time=args.controlled_begin,
            end_time=args.controlled_end,
        )
        route_files.append(controlled_file)
        if route_mode == "junction":
            use_junction_taz = True

        print(
            f"[SUMO] Controlled cohort: {args.controlled_count} vehicles, mode={route_mode}, "
            f"source={controlled_source}, destination={controlled_destination}, "
            f"via={controlled_via if controlled_via else '[]'}"
        )

    if args.emergency_count < 0:
        raise ValueError("--emergency-count must be >= 0")
    if args.emergency_count > 0:
        emergency_count_multiplier = 3
        effective_emergency_count = args.emergency_count * emergency_count_multiplier

        if net_file is None:
            raise ValueError("emergency flow generation requires a valid net-file in the scenario config")
        if not args.emergency_source or not args.emergency_destination:
            raise ValueError(
                "--emergency-source and --emergency-destination are required when --emergency-count > 0"
            )

        emergency_via = _parse_csv_values(args.emergency_via_rsus)
        emergency_source = args.emergency_source
        emergency_destination = args.emergency_destination

        (
            emergency_source,
            emergency_destination,
            emergency_via,
            emergency_alias_replacements,
        ) = _resolve_rsu_route_inputs(
            source=emergency_source,
            destination=emergency_destination,
            via_list=emergency_via,
            alias_to_junction=rsu_alias_map,
        )
        for role, old_id, new_id in emergency_alias_replacements:
            print(f"[SUMO] Emergency RSU alias ({role}): {old_id} -> {new_id}")

        if args.auto_fallback_junctions:
            (
                emergency_source,
                emergency_destination,
                emergency_via,
                replacements,
            ) = _auto_fix_controlled_junctions(
                net_file=net_file,
                source=emergency_source,
                destination=emergency_destination,
                via_list=emergency_via,
            )
            for role, old_id, new_id in replacements:
                print(f"[SUMO] Emergency auto-fallback ({role}): {old_id} -> {new_id}")

        emergency_file, emergency_mode = _generate_emergency_group_route_file(
            net_file=net_file,
            scenario_name=config.scenario,
            vehicle_count=effective_emergency_count,
            source=emergency_source,
            destination=emergency_destination,
            via_list=emergency_via,
            begin_time=args.emergency_begin,
            end_time=args.emergency_end,
        )
        route_files.append(emergency_file)
        if emergency_mode == "junction":
            use_junction_taz = True

        print(
            f"[SUMO] Emergency cohort: {effective_emergency_count} vehicles (base={args.emergency_count}, x3), mode={emergency_mode}, "
            f"source={emergency_source}, destination={emergency_destination}, "
            f"via={emergency_via if emergency_via else '[]'}"
        )

    if args.gui and net_file is not None:
        rsu_spacing = args.rsu_min_spacing_m
        if rsu_spacing is None:
            rsu_spacing = max(80.0, args.rsu_range_m * 1.8)

        # Use custom RSU config if available, otherwise auto-detect
        if use_custom_rsu_config and rsu_config_table:
            poi_file, selected_count = _generate_rsu_poi_from_config(
                net_file,
                config.scenario,
                rsu_range_m=max(5.0, args.rsu_range_m),
                rsu_config_table=rsu_config_table,
            )
            candidate_count = selected_count  # All configured RSUs are placed
            if poi_file is not None:
                additional_files.append(poi_file)
                print(
                    f"[SUMO] RSU overlays: {selected_count} RSUs from custom config "
                    f"({args.rsu_config})"
                )
        else:
            # Parse whitelist for POI generation
            poi_whitelist: set[str] | None = None
            if args.rsu_whitelist:
                poi_whitelist = set()
                for token in args.rsu_whitelist.split(","):
                    alias = token.strip().upper()
                    if alias.startswith("RSU_"):
                        alias = alias[4:]
                    elif alias.startswith("RSU"):
                        alias = alias[3:]
                    poi_whitelist.add(alias)

            poi_file, selected_count, candidate_count = _generate_rsu_poi_add_file(
                net_file,
                config.scenario,
                rsu_range_m=max(5.0, args.rsu_range_m),
                min_incoming_lanes=max(1, args.rsu_min_inc_lanes),
                max_count=max(1, args.rsu_max_count),
                min_spacing_m=max(1.0, rsu_spacing),
                rsu_whitelist=poi_whitelist,
            )
            if poi_file is not None:
                additional_files.append(poi_file)
                whitelist_note = f" (whitelist: {len(poi_whitelist)} RSUs)" if poi_whitelist else ""
                print(
                    f"[SUMO] RSU overlays: selected {selected_count} intersections "
                    f"out of {candidate_count} candidates{whitelist_note} (min-inc-lanes={args.rsu_min_inc_lanes}, "
                    f"min-spacing={rsu_spacing:.1f}m, max-count={args.rsu_max_count})."
                )

        if config.gui_use_osg_view or args.three_d:
            print(
                "[SUMO] Note: OSG 3D mode may hide POI/poly overlays on some builds. "
                "Use 2D GUI (omit --three-d) for guaranteed RSU-range visibility."
            )

    statistics_output_path = (
        _resolve_project_path(args.statistics_output, project_root=project_root)
        if args.statistics_output
        else None
    )
    summary_output_path = (
        _resolve_project_path(args.summary_output, project_root=project_root)
        if args.summary_output
        else None
    )
    tripinfo_output_path = (
        _resolve_project_path(args.tripinfo_output, project_root=project_root)
        if args.tripinfo_output
        else None
    )

    if args.kpi_output_dir:
        kpi_output_dir = _resolve_project_path(args.kpi_output_dir, project_root=project_root)
        kpi_output_prefix = args.kpi_output_prefix or _build_runtime_run_id(
            scenario=config.scenario,
            seed=args.seed,
        )

        if statistics_output_path is None:
            statistics_output_path = kpi_output_dir / f"{kpi_output_prefix}_statistics.xml"
        if summary_output_path is None:
            summary_output_path = kpi_output_dir / f"{kpi_output_prefix}_summary.xml"
        if tripinfo_output_path is None:
            tripinfo_output_path = kpi_output_dir / f"{kpi_output_prefix}_tripinfo.xml"

    kpi_output_paths = {
        "statistics_output": statistics_output_path,
        "summary_output": summary_output_path,
        "tripinfo_output": tripinfo_output_path,
    }
    if any(path is not None for path in kpi_output_paths.values()):
        print(
            "[SUMO] KPI outputs enabled: "
            f"statistics={statistics_output_path if statistics_output_path is not None else 'disabled'}, "
            f"summary={summary_output_path if summary_output_path is not None else 'disabled'}, "
            f"tripinfo={tripinfo_output_path if tripinfo_output_path is not None else 'disabled'}"
        )

    command = build_sumo_command(
        config,
        seed=args.seed,
        use_gui=args.gui,
        force_3d=args.three_d,
        additional_files=additional_files,
        route_files=route_files,
        scale=traffic_scale,
        junction_taz=use_junction_taz,
        statistics_output_path=statistics_output_path,
        summary_output_path=summary_output_path,
        tripinfo_output_path=tripinfo_output_path,
        tripinfo_write_unfinished=bool(args.tripinfo_write_unfinished),
    )
    max_steps = args.max_steps if args.max_steps is not None else config.default_max_steps

    print("[SUMO] Scenario:", config.scenario)
    print("[SUMO] Config:", config.sumocfg_path)
    print("[SUMO] Command:", command)
    print("[SUMO] Max steps:", max_steps)

    if args.dry_run:
        print("[SUMO] Dry-run complete. No TraCI/libsumo session started.")
        return

    for output_path in kpi_output_paths.values():
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)

    # libsumo is an in-process backend and does not provide the GUI window.
    # Force traci backend whenever GUI mode is requested.
    prefer_libsumo = config.prefer_libsumo and not args.gui
    adapter = SumoAdapter.create(prefer_libsumo=prefer_libsumo)
    adapter.start(command)

    if args.gui:
        if net_file is not None:
            bounds = _parse_world_bounds_from_net(net_file)
            if bounds is not None:
                adapter.set_view_boundary(
                    xmin=bounds[0],
                    ymin=bounds[1],
                    xmax=bounds[2],
                    ymax=bounds[3],
                )

    runtime_logger = None
    runtime_logger_failed = False
    if args.enable_runtime_logging:
        if SumoSimulationDataLogger is None:
            print("[SUMO][LOGGER] runtime logger unavailable: could not import pipelines.logging.runtime_logger")
        elif net_file is None:
            print("[SUMO][LOGGER] runtime logging skipped: scenario has no resolvable net-file")
        else:
            runtime_log_root = _resolve_project_path(args.runtime_log_root, project_root=project_root)

            runtime_run_id = args.runtime_log_run_id or _build_runtime_run_id(
                scenario=config.scenario,
                seed=args.seed,
            )
            runtime_run_dir = runtime_log_root / runtime_run_id

            rsu_alias_table_for_logging = list(rsu_alias_table)
            if not rsu_alias_table_for_logging:
                rsu_spacing_for_logging = args.rsu_min_spacing_m
                if rsu_spacing_for_logging is None:
                    rsu_spacing_for_logging = max(80.0, args.rsu_range_m * 1.8)
                rsu_alias_table_for_logging = _build_rsu_alias_table(
                    net_file=net_file,
                    min_incoming_lanes=1,
                    max_count=max(1, args.rsu_max_count),
                    min_spacing_m=max(1.0, rsu_spacing_for_logging),
                )

            run_metadata = {
                "run_id": runtime_run_id,
                "scenario": config.scenario,
                "seed": args.seed,
                "contract": str(contract_path),
                "sumocfg": str(config.sumocfg_path),
                "max_steps": int(max_steps),
                "step_length_seconds": float(config.step_length_seconds),
                "traffic_scale": float(traffic_scale),
                "sumo_command": command,
            }
            kpi_outputs_metadata = {
                key: str(path)
                for key, path in kpi_output_paths.items()
                if path is not None
            }
            if kpi_outputs_metadata:
                run_metadata["kpi_outputs"] = kpi_outputs_metadata

            try:
                runtime_logger = SumoSimulationDataLogger(
                    run_dir=runtime_run_dir,
                    run_metadata=run_metadata,
                    net_file=net_file,
                    rsu_alias_table=rsu_alias_table_for_logging,
                    rsu_range_m=max(5.0, args.rsu_range_m),
                )
                print(
                    "[SUMO][LOGGER] Enabled 1 Hz logging: run_id={run_id} rsu_count={rsu_count} edge_count={edge_count} dir={dir}".format(
                        run_id=runtime_run_id,
                        rsu_count=len(rsu_alias_table_for_logging),
                        edge_count=len(getattr(runtime_logger, "_edge_ids", [])),
                        dir=runtime_run_dir,
                    )
                )
            except Exception as exc:
                runtime_logger = None
                print(f"[SUMO][LOGGER] Failed to initialize runtime logger: {exc}")

    # ── Phase 4: RL signal controller (lazy import so it's optional) ─────
    rl_signal_controller = None
    if getattr(args, "enable_rl_signal_control", False):
        try:
            from controllers.rl.inference_hook import RLSignalController
            rl_signal_controller = RLSignalController.from_args(args, None)  # traci not yet live
            print("[SUMO][RL] RL signal controller initialised (will activate on first step)")
        except Exception as _rl_exc:
            print(f"[SUMO][RL] Could not load RL controller: {_rl_exc}")

    # ── T-GCN Neural Network (learned traffic prediction) ─────────────────
    tgcn_engine = None
    if getattr(args, "enable_tgcn", False):
        try:
            from routing.pytorch_gnn import PyTorchGNNRerouteEngine, TGCNConfig
            import networkx as nx

            # Build road graph from RSU connectivity
            road_graph = nx.DiGraph()
            rsu_junction_ids = [jid for _, jid, _, _ in rsu_alias_table] if rsu_alias_table else []
            if use_custom_rsu_config:
                rsu_junction_ids = [jid for _, jid, _, _, _ in rsu_config_table]

            road_graph.add_nodes_from(rsu_junction_ids)
            # Add edges between adjacent RSUs (simplified connectivity)
            for i, jid_i in enumerate(rsu_junction_ids):
                for j, jid_j in enumerate(rsu_junction_ids):
                    if i != j:
                        # Connect RSUs that are within reasonable distance
                        road_graph.add_edge(jid_i, jid_j)

            # Configure T-GCN
            tgcn_config = TGCNConfig(
                log_interval=getattr(args, "tgcn_log_interval", 50),
            )

            # Initialize engine
            tgcn_engine = PyTorchGNNRerouteEngine(
                road_graph=road_graph,
                rsu_junctions=rsu_junction_ids,
                config=tgcn_config,
                model_path=getattr(args, "tgcn_model_path", None),
            )

            # Create checkpoint directory
            tgcn_checkpoint_dir = Path(getattr(args, "tgcn_checkpoint_dir", "models/tgcn"))
            tgcn_checkpoint_dir.mkdir(parents=True, exist_ok=True)

            print(f"[SUMO][T-GCN] T-GCN engine initialized with {len(rsu_junction_ids)} RSU nodes")
            print(f"[SUMO][T-GCN] Training enabled: {getattr(args, 'tgcn_train', False)}")
        except Exception as _tgcn_exc:
            print(f"[SUMO][T-GCN] Could not initialize T-GCN: {_tgcn_exc}")
            import traceback
            traceback.print_exc()

    # ── Forced junction congestion pre-computation ────────────────────────
    # Resolve RSU alias (e.g. "DALHOUSIE") -> real junction ID, then find
    # all edges connected to that junction for later travel-time penalisation.
    forced_congestion_edge_ids: list[str] = []
    force_congestion_junction_raw = getattr(args, "force_congestion_at_junction", None)
    if force_congestion_junction_raw:
        force_congestion_junction = _resolve_rsu_identifier(
            force_congestion_junction_raw, rsu_alias_map
        )
        if net_file is not None:
            forced_congestion_edge_ids = _get_edges_connected_to_junction(
                net_file, force_congestion_junction
            )
        print(
            f"[SUMO][CONGESTION] Force-congestion armed: "
            f"junction='{force_congestion_junction}' "
            f"(input='{force_congestion_junction_raw}') "
            f"fires at step={args.force_congestion_at_step}, "
            f"edges_to_penalize={len(forced_congestion_edge_ids)}"
        )

    # ── Initial detour pre-computation ────────────────────────────────────
    # At step 1 these junctions are penalised so vehicles use the Dalhousie
    # corridor.  The penalty is lifted when Dalhousie is blocked.
    init_detour_edge_ids: list[str] = []
    init_detour_raw = getattr(args, "init_detour_junctions", None) or ""
    for raw_token in [t.strip() for t in init_detour_raw.split(",") if t.strip()]:
        resolved = _resolve_rsu_identifier(raw_token, rsu_alias_map)
        if net_file is not None:
            init_detour_edge_ids.extend(
                _get_edges_connected_to_junction(net_file, resolved)
            )
    if init_detour_edge_ids:
        print(
            f"[SUMO][DETOUR] Initial western-route bias armed: "
            f"{len(init_detour_edge_ids)} edges will be penalised at step 0 (9900s) "
            f"so vehicles route via Dalhousie; Dalhousie blocked at step {getattr(args, 'force_congestion_at_step', 30)}"
        )

    # Precompute RSU-connected edges once so congestion-aware rerouting can
    # quickly penalize all edges near currently congested RSUs.
    rsu_edges_by_jid: dict[str, list[str]] = {}
    if net_file is not None and rsu_alias_table:
        for _alias, jid, _rx, _ry in rsu_alias_table:
            if jid in rsu_edges_by_jid:
                continue
            rsu_edges_by_jid[jid] = _get_edges_connected_to_junction(net_file, jid)

    try:
        last_hybrid_push_sim_time = -1e9
        try:
            reroute_cooldown_seconds = float(os.getenv("HYBRID_REROUTE_COOLDOWN_SECONDS", "20.0"))
        except Exception:
            reroute_cooldown_seconds = 20.0
        reroute_cooldown_seconds = max(1.0, reroute_cooldown_seconds)
        try:
            congestion_min_vehicle_count = int(os.getenv("HYBRID_CONGESTION_MIN_VEHICLES", "5"))
        except Exception:
            congestion_min_vehicle_count = 5
        congestion_min_vehicle_count = max(1, congestion_min_vehicle_count)
        try:
            congestion_max_avg_speed_mps = float(os.getenv("HYBRID_CONGESTION_MAX_AVG_SPEED_MPS", "5.0"))
        except Exception:
            congestion_max_avg_speed_mps = 5.0
        congestion_max_avg_speed_mps = max(0.1, congestion_max_avg_speed_mps)
        try:
            congestion_edge_penalty_seconds = float(os.getenv("HYBRID_CONGESTION_EDGE_PENALTY_SECONDS", "9000.0"))
        except Exception:
            congestion_edge_penalty_seconds = 9000.0
        congestion_edge_penalty_seconds = max(60.0, congestion_edge_penalty_seconds)
        try:
            congestion_edge_hold_seconds = float(os.getenv("HYBRID_CONGESTION_EDGE_HOLD_SECONDS", "20.0"))
        except Exception:
            congestion_edge_hold_seconds = 20.0
        congestion_edge_hold_seconds = max(1.0, congestion_edge_hold_seconds)
        try:
            congestion_reroute_fraction = float(os.getenv("HYBRID_CONGESTION_REROUTE_FRACTION", "0.7"))
        except Exception:
            congestion_reroute_fraction = 0.7
        congestion_reroute_fraction = max(0.05, min(1.0, congestion_reroute_fraction))
        marker_refresh_steps = max(1, int(getattr(args, "marker_refresh_steps", 4)))
        emergency_priority_interval_steps = max(
            1,
            int(getattr(args, "emergency_priority_interval_steps", 2)),
        )
        held_until: dict[str, float] = {}
        tls_preempt_until: dict[str, float] = {}
        tls_original_state: dict[str, str] = {}
        manual_corridor_tls_hold_until: dict[str, float] = {}
        manual_corridor_tls_original_state: dict[str, str] = {}
        tls_outgoing_edge_index_cache: dict[str, dict[str, set[int]]] = {}
        tls_id_cache: list[str] = []
        segment_junction_path_cache: dict[tuple[str, str], list[str]] = {}
        junction_reachability_cache: dict[tuple[str, str], bool] = {}
        junction_to_tls_cache: dict[str, set[str]] = {}
        incoming_tls_cache: dict[str, set[str]] = {}  # NEW: TLS controlling INCOMING edges to junctions
        active_green_corridor_paths: list[list[str]] = []
        active_green_corridor_until = -1.0
        # Per-RSU cumulative telemetry counters (matches runtime_logger.py training data format).
        # In training data, packets_received and bytes_received are cumulative totals across all steps.
        rsu_cumulative_packets: dict[str, int] = {}
        rsu_cumulative_bytes: dict[str, int] = {}
        reroute_highlight_until: dict[str, float] = {}
        reroute_cooldown_until: dict[str, float] = {}
        marker_type_cache: dict[str, str] = {}
        enable_vehicle_markers = args.controlled_count > 0 or args.emergency_count > 0
        rsu_graph_registered: list[bool] = [False]  # mutable flag for closure
        congestion_injected: list[bool] = [False]   # mutable flag for forced congestion
        detour_injected: list[bool] = [False]       # mutable flag for initial detour bias
        controlled_initial_rerouted: set[str] = set()  # controlled vehicles rerouted on first appearance
        last_emergency_log_signature: tuple[int, int, int, int] | None = None
        last_corridor_log_signature: tuple[int, int, int] | None = None

        def _on_step(step_idx: int, sim_time: float, traci_module) -> None:
            nonlocal last_hybrid_push_sim_time
            nonlocal runtime_logger_failed
            nonlocal last_emergency_log_signature
            nonlocal active_green_corridor_paths
            nonlocal active_green_corridor_until
            nonlocal last_corridor_log_signature
            try:
                vehicle_ids = list(traci_module.vehicle.getIDList())
            except Exception:
                return
            active_vehicle_ids = set(vehicle_ids)
            controlled_vehicle_ids: list[str] = []
            emergency_vehicle_ids: list[str] = []

            if enable_vehicle_markers or args.enable_emergency_priority or args.enable_hybrid_uplink_stub:
                controlled_vehicle_ids, emergency_vehicle_ids = _classify_vehicle_marker_targets(
                    traci_module,
                    vehicle_ids=vehicle_ids,
                    marker_type_cache=marker_type_cache,
                )

            # ── First-insertion reroute for controlled vehicles ──────────────
            # Flow vehicles have routes pre-computed at load time.
            # Use setVia to force controlled vehicles through Dalhousie,
            # then call rerouteTraveltime to compute a route via that edge.
            # This scripted detour is Kolkata-specific and is skipped for demo.
            DALHOUSIE_VIA_EDGE = "1029381530#0" if args.scenario == "kolkata" else None
            if not congestion_injected[0] and DALHOUSIE_VIA_EDGE is not None:
                for vid in controlled_vehicle_ids:
                    if vid not in controlled_initial_rerouted:
                        if _is_reroute_safe_now(traci_module, vid):
                            try:
                                before_r = list(traci_module.vehicle.getRoute(vid))
                                # Force vehicle through Dalhousie via edge
                                traci_module.vehicle.setVia(vid, [DALHOUSIE_VIA_EDGE])
                                traci_module.vehicle.rerouteTraveltime(vid)
                                after_r = list(traci_module.vehicle.getRoute(vid))
                                before_rsus = _get_rsus_on_route(before_r, junction_to_rsu_name, edge_to_junctions)
                                after_rsus = _get_rsus_on_route(after_r, junction_to_rsu_name, edge_to_junctions)
                                print(
                                    f"[SUMO][DETOUR] {vid}: "
                                    f"{'→'.join(before_rsus) or '(no RSU)'} => "
                                    f"{'→'.join(after_rsus) or '(no RSU)'}"
                                )
                            except Exception as e:
                                print(f"[SUMO][DETOUR] {vid}: setVia failed - {e}")
                        controlled_initial_rerouted.add(vid)

            if runtime_logger is not None and not runtime_logger_failed:
                try:
                    runtime_logger.maybe_log(
                        sim_time_seconds=sim_time,
                        frame_idx=step_idx,
                        traci_module=traci_module,
                        vehicle_ids=vehicle_ids,
                    )
                except Exception as exc:
                    runtime_logger_failed = True
                    print(f"[SUMO][LOGGER] Disabled after runtime error: {exc}")

            if enable_vehicle_markers and step_idx % marker_refresh_steps == 0:
                marker_stats = _apply_visual_vehicle_markers(traci_module, controlled_vehicle_ids)
                emergency_marker_stats = _apply_emergency_vehicle_markers(
                    traci_module,
                    emergency_vehicle_ids,
                )
                if step_idx % 50 == 0 and (
                    marker_stats["controlled_marked"] > 0
                    or emergency_marker_stats["emergency_marked"] > 0
                ):
                    print(
                        "[SUMO][MARKERS] deep_blue_controlled={c} bright_yellow_emergency={e}".format(
                            c=marker_stats["controlled_marked"],
                            e=emergency_marker_stats["emergency_marked"],
                        )
                    )

            reroute_marker_count = _apply_active_reroute_highlights(
                traci_module,
                sim_time=sim_time,
                active_vehicle_ids=active_vehicle_ids,
                reroute_highlight_until=reroute_highlight_until,
            )
            for vid, until in list(reroute_cooldown_until.items()):
                if vid not in active_vehicle_ids or sim_time >= until:
                    reroute_cooldown_until.pop(vid, None)
            if step_idx % 50 == 0 and reroute_marker_count > 0:
                print(f"[SUMO][MARKERS] reroute_highlights={reroute_marker_count}")

            if args.enable_emergency_priority and step_idx % emergency_priority_interval_steps == 0:
                emergency_stats = _apply_emergency_priority_policy(
                    traci_module,
                    sim_time=sim_time,
                    vehicle_ids=vehicle_ids,
                    emergency_vehicle_ids=emergency_vehicle_ids,
                    held_until=held_until,
                    tls_hold_until=tls_preempt_until,
                    tls_original_state=tls_original_state,
                    lookahead_edges=max(1, args.emergency_corridor_lookahead_edges),
                    tls_lookahead_count=max(1, args.emergency_tls_lookahead),
                    tls_preempt_distance_m=max(10.0, args.emergency_tls_preempt_distance_m),
                    hold_seconds=max(0.1, args.emergency_hold_seconds),
                )
                if emergency_stats["emergency_count"] > 0:
                    emergency_signature = (
                        emergency_stats["emergency_count"],
                        emergency_stats["corridor_preempted"],
                        emergency_stats["tls_preempted"],
                        emergency_stats["released"],
                    )
                    if step_idx % 20 == 0 or emergency_signature != last_emergency_log_signature:
                        print(
                            "[SUMO][EMERGENCY] active={a} rerouted={r} edge_preempted={p} tls_preempted={tp} released={rel}".format(
                                a=emergency_stats["emergency_count"],
                                r=emergency_stats["emergency_reroutes"],
                                p=emergency_stats["corridor_preempted"],
                                tp=emergency_stats["tls_preempted"],
                                rel=emergency_stats["released"],
                            )
                        )
                    last_emergency_log_signature = emergency_signature
                else:
                    last_emergency_log_signature = None

            # Check backend for CLEARED corridors (but don't clear on network timeout)
            # Only clear if backend explicitly reports NO corridors AND we had some before
            # This prevents spurious clearing due to network jitter
            if args.enable_hybrid_uplink_stub and args.server_url and active_green_corridor_paths:
                try:
                    corridor_check_url = args.server_url.rstrip("/") + "/signals/green-corridor"
                    response = _post_json(corridor_check_url, {}, timeout_seconds=0.1, method="GET")
                    if response and isinstance(response.get("active_corridors"), list):
                        backend_active_corridors = response.get("active_corridors", [])

                        if backend_active_corridors:
                            # Renew local expiry from server's remaining_seconds
                            # so persistent corridors stay alive.
                            max_remaining = max(
                                (float(c.get("remaining_seconds", 0)) for c in backend_active_corridors),
                                default=0.0,
                            )
                            if max_remaining > 0:
                                active_green_corridor_until = sim_time + min(300.0, max_remaining)

                        # ONLY clear if backend explicitly says no corridors AND we had some
                        elif active_green_corridor_paths:
                            print(f"[SUMO][GreenCorridor] Backend reports no active corridors - clearing local state")
                            active_green_corridor_paths = []
                            active_green_corridor_until = -1.0
                            # IMMEDIATE TLS restoration: restore every held TLS now
                            _force_restore_all_corridor_tls(
                                traci_module,
                                manual_corridor_tls_hold_until,
                                manual_corridor_tls_original_state,
                            )
                except Exception:
                    # Network timeout or error - DON'T clear corridors
                    # Let /route polling and time-based expiration handle corridor lifetimes
                    pass

            corridor_tls_targets: dict[str, set[int]] = {}
            active_corridor_count = 0
            if active_green_corridor_paths and sim_time <= active_green_corridor_until:
                active_corridor_count = len(active_green_corridor_paths)

                # Build junction→TLS mappings on first use (lazy initialization)
                if not junction_to_tls_cache:
                    junction_to_tls_cache.update(_build_junction_to_tls_map(
                        traci_module,
                        edge_to_junctions,
                        tls_outgoing_edge_index_cache,
                    ))
                if not incoming_tls_cache:
                    incoming_tls_cache.update(_build_incoming_tls_map(
                        traci_module,
                        edge_to_junctions,
                        tls_outgoing_edge_index_cache,
                    ))

                corridor_tls_targets = _collect_green_corridor_tls_targets(
                    traci_module,
                    corridor_paths=active_green_corridor_paths,
                    edge_to_junctions=edge_to_junctions,
                    tls_outgoing_edge_index_cache=tls_outgoing_edge_index_cache,
                    tls_id_cache=tls_id_cache,
                    segment_path_cache=segment_junction_path_cache,
                    junction_reachability_cache=junction_reachability_cache,
                    junction_to_tls_map=junction_to_tls_cache,
                    incoming_tls_map=incoming_tls_cache,
                )

                # Debug: Log corridor activity (every 20 steps to avoid log spam)
                if int(sim_time) % 20 < 0.1 and corridor_tls_targets:
                    corridor_path_str = " → ".join(active_green_corridor_paths[0])
                    target_tls_list = [f"{tls_id}({len(indices)}lanes)" for tls_id, indices in corridor_tls_targets.items()]
                    print(f"[SUMO][GreenCorridor] sim_time={int(sim_time)}s path={corridor_path_str} TLS={','.join(target_tls_list[:3])}")
                    if "Girish_Park" in str(active_green_corridor_paths):
                        print(f"[SUMO][GreenCorridor][DEBUG] Girish Park detected in corridor!")
                        print(f"  Active paths: {active_green_corridor_paths}")
                        print(f"  Junction-to-TLS map keys: {list(junction_to_tls_cache.keys())[:10]}")
                        if any("Girish" in str(k) for k in junction_to_tls_cache.keys()):
                            print(f"  Found Girish Park in junction map!")
                        for path in active_green_corridor_paths:
                            for rsu in path:
                                if "Girish" in str(rsu):
                                    print(f"    Girish Park in corridor path at position")

            elif sim_time > active_green_corridor_until:
                active_green_corridor_paths = []
                print(f"[SUMO][GreenCorridor] Corridor expired at sim_time={int(sim_time)}s - restoring TLS")
                _force_restore_all_corridor_tls(
                    traci_module,
                    manual_corridor_tls_hold_until,
                    manual_corridor_tls_original_state,
                )

            # Force restoration of TLS if no active corridors but some still held
            if not active_green_corridor_paths and manual_corridor_tls_hold_until:
                _force_restore_all_corridor_tls(
                    traci_module,
                    manual_corridor_tls_hold_until,
                    manual_corridor_tls_original_state,
                )


            corridor_tls_preempted, corridor_tls_restored = _apply_lane_level_tls_preemption(
                traci_module,
                sim_time=sim_time,
                hold_seconds=1.0,
                tls_targets=corridor_tls_targets,
                tls_hold_until=manual_corridor_tls_hold_until,
                tls_original_state=manual_corridor_tls_original_state,
            )

            if active_corridor_count > 0:
                corridor_signature = (
                    active_corridor_count,
                    corridor_tls_preempted,
                    len(corridor_tls_targets),
                )
                if step_idx % 20 == 0 or corridor_signature != last_corridor_log_signature:
                    print(
                        "[SUMO][GREEN_CORRIDOR] active_paths={paths} preempted_tls={pre} target_tls={targets} restored_tls={restored}".format(
                            paths=active_corridor_count,
                            pre=corridor_tls_preempted,
                            targets=len(corridor_tls_targets),
                            restored=corridor_tls_restored,
                        )
                    )
                last_corridor_log_signature = corridor_signature
            else:
                last_corridor_log_signature = None

            # ── MANUAL SIGNAL CONTROL: Force RED at Sovabazar + Hatibagan ───
            # START DELAYED to let SUMO stabilize first (avoid early crashes)
            manual_forced_congested_rsus: list[str] = []
            if args.scenario == "kolkata" and 110 <= step_idx <= 250:
                manual_target_junctions = [
                    ("Sovabazar", "cluster_10282080280_10846969131_11365834325_2281800978_#2more"),
                    ("Hatibagan", "cluster_11365834326_664447519_8507404876_8507404877_#1more"),
                ]
                manual_forced_congested_rsus = [jid for _, jid in manual_target_junctions]

                for junction_label, junction_id in manual_target_junctions:
                    try:
                        # Simple approach: Try to get phase count first to validate junction
                        try:
                            phase_count = traci_module.trafficlight.getPhaseNumber(junction_id)
                            if phase_count <= 0:
                                raise ValueError(f"Phase count invalid: {phase_count}")

                            # Set to phase 0 (first phase is typically all-red or safe)
                            current_phase = traci_module.trafficlight.getPhase(junction_id)
                            traci_module.trafficlight.setPhase(junction_id, 0)

                            if step_idx == 110:
                                print(f"[MANUAL_SIGNAL] START: {junction_label} RED at {junction_id[:40]}...")
                                print(f"  {junction_label} phase: {current_phase}, total phases: {phase_count}")
                        except Exception:
                            # If phase control fails, try state string as fallback
                            current_state = str(traci_module.trafficlight.getRedYellowGreenState(junction_id))
                            if current_state and len(current_state) > 0 and all(c in "rRyYgGoOuUsS" for c in current_state):
                                # SUMO TLS uses lowercase 'r' for red; uppercase 'R' can break state handling.
                                all_red = current_state.replace("G", "r").replace("g", "r").replace("y", "r").replace("Y", "r")
                                traci_module.trafficlight.setRedYellowGreenState(junction_id, all_red)
                                if step_idx == 110:
                                    print(f"[MANUAL_SIGNAL] START: {junction_label} state-control RED")
                    except Exception as e:
                        # Silently ignore errors to prevent cascade failures
                        if step_idx % 50 == 0:
                            print(f"[MANUAL_SIGNAL] Note [{junction_label}] (step {step_idx}): {type(e).__name__}")

                if step_idx == 250:
                    print(f"[MANUAL_SIGNAL] END: Released Sovabazar + Hatibagan at step {step_idx}")

            # ── Phase 4: RL adaptive signal control ──────────────────────────
            if rl_signal_controller is not None:
                # During active emergency preemption, avoid overriding forced TLS states.
                manual_corridor_preempting = bool(manual_corridor_tls_hold_until)
                if (args.enable_emergency_priority and emergency_vehicle_ids) or manual_corridor_preempting:
                    if step_idx % 100 == 0:
                        reason = "active emergency TLS preemption" if emergency_vehicle_ids else "active manual corridor TLS preemption"
                        print(f"[SUMO][RL] skipped this step due to {reason}")
                else:
                    try:
                        rl_signal_controller.step(sim_time, traci_module)
                    except Exception as _rl_step_exc:
                        if step_idx % 200 == 0:
                            print(f"[SUMO][RL] step error (step={step_idx}): {_rl_step_exc}")

            # ── T-GCN Neural Network Prediction & Training ────────────────────
            # OPTIMIZED: Batch vehicle position queries and use cached predictions
            tgcn_predictions = None
            tgcn_reroute_decision = None
            if tgcn_engine is not None and rsu_alias_table:
                try:
                    tgcn_range_m = max(5.0, args.rsu_range_m)

                    # OPTIMIZATION: Get all vehicle positions in one batch
                    # (reduces TraCI overhead by ~70%)
                    vehicle_positions_batch: dict[str, tuple[float, float]] = {}
                    vehicle_speeds_batch: dict[str, float] = {}
                    for vid in vehicle_ids:
                        try:
                            vehicle_positions_batch[vid] = traci_module.vehicle.getPosition(vid)
                            vehicle_speeds_batch[vid] = traci_module.vehicle.getSpeed(vid)
                        except Exception:
                            pass

                    # Build RSU states from cached positions
                    tgcn_rsu_states: dict[str, dict] = {}
                    for _alias, jid, rx, ry in rsu_alias_table:
                        vehicle_count = 0
                        total_speed = 0.0
                        queue_length = 0

                        # Use cached positions instead of per-vehicle TraCI calls
                        for vid, (vx, vy) in vehicle_positions_batch.items():
                            if math.hypot(vx - rx, vy - ry) <= tgcn_range_m:
                                vehicle_count += 1
                                spd = vehicle_speeds_batch.get(vid, 0.0)
                                total_speed += spd
                                if spd < 0.1:
                                    queue_length += 1

                        avg_speed = total_speed / vehicle_count if vehicle_count > 0 else 13.89

                        # Check if this junction is the forced congestion junction
                        is_congested_junction = False
                        if force_congestion_junction_raw and congestion_injected[0]:
                            force_jid = _resolve_rsu_identifier(force_congestion_junction_raw, rsu_alias_map)
                            is_congested_junction = (jid == force_jid)

                        tgcn_rsu_states[jid] = {
                            "vehicle_count": vehicle_count,
                            "avg_speed": avg_speed,
                            "queue_length": queue_length,
                            "incident": is_congested_junction,
                        }

                    # Get predictions from T-GCN (with internal caching)
                    tgcn_predictions = tgcn_engine.predict(tgcn_rsu_states, step_idx)

                    # Get reroute decision for proactive rerouting
                    tgcn_reroute_decision = tgcn_engine.get_reroute_decision(tgcn_rsu_states, step_idx)

                    # ═══════════════════════════════════════════════════════════════
                    # T-GCN REROUTING: DISABLED FOR PERFORMANCE
                    # Manual congestion injection handles rerouting
                    # ═══════════════════════════════════════════════════════════════
                    # OPTIMIZATION: Disable proactive rerouting - it's too slow
                    # The manual congestion at step 60 will handle rerouting instead

                    # Training step if enabled (optimized: only every 25 steps)
                    if getattr(args, "tgcn_train", False) and step_idx % 25 == 0:
                        # Create ground truth from actual RSU states
                        actual_congestion = {}
                        for jid, state in tgcn_rsu_states.items():
                            # Improved congestion scoring
                            vehicle_density = state["vehicle_count"] / 30.0
                            speed_ratio = max(0, 1 - state["avg_speed"] / 15.0)
                            queue_ratio = state["queue_length"] / max(1, state["vehicle_count"])

                            congestion = min(1.0, (
                                vehicle_density * 0.45 +
                                speed_ratio * 0.40 +
                                queue_ratio * 0.15
                            ))
                            actual_congestion[jid] = congestion

                        train_metrics = tgcn_engine.train_step(
                            tgcn_rsu_states,
                            actual_congestion
                        )

                    # Log high-risk predictions (REDUCED FREQUENCY)
                    if step_idx % 200 == 0:
                        high_risk_rsus = [
                            (jid, pred["p_congestion"], pred["risk_level"])
                            for jid, pred in tgcn_predictions.items()
                            if pred["risk_level"] in ("medium", "high")
                        ]
                        if high_risk_rsus:
                            risk_str = ", ".join(
                                f"{junction_to_rsu_name.get(jid, jid)[:15]}={p:.2f}"
                                for jid, p, _ in high_risk_rsus[:3]
                            )
                            print(f"[SUMO][T-GCN] Step {step_idx}: High-risk RSUs: {risk_str}")

                        # Log reroute decision
                        if tgcn_reroute_decision and tgcn_reroute_decision["should_reroute"]:
                            print(f"[SUMO][T-GCN] Predicted: frac={tgcn_reroute_decision['reroute_fraction']:.2f}, "
                                  f"max_cong={tgcn_reroute_decision['max_congestion']:.2f}, "
                                  f"conf={tgcn_reroute_decision['confidence']:.2f}")

                    # Save checkpoint periodically
                    if step_idx > 0 and step_idx % 1000 == 0:
                        checkpoint_path = Path(getattr(args, "tgcn_checkpoint_dir", "models/tgcn")) / f"tgcn_step_{step_idx}.pt"
                        tgcn_engine.save(str(checkpoint_path))

                except Exception as _tgcn_exc:
                    if step_idx % 200 == 0:
                        import traceback
                        print(f"[SUMO][T-GCN] step error (step={step_idx}): {_tgcn_exc}")
                        traceback.print_exc()

            # ── Step-0 initial detour: bias vehicles onto Dalhousie corridor ──
            if init_detour_edge_ids and not detour_injected[0] and step_idx == 0:
                n_det = _force_edge_congestion(traci_module, init_detour_edge_ids, penalty_seconds=9900.0)
                detour_injected[0] = True
                print(
                    f"[SUMO][DETOUR] Western-route bypass penalised: {n_det} edges "
                    f"→ Sealdah→ParkCircus cohort will route via Dalhousie Square"
                )

            # ── Forced junction congestion + mass reroute ─────────────────────
            # At the configured step, penalise every edge touching the blocked
            # junction and immediately reroute all vehicles to bypass it.
            # Rerouted vehicles will appear RED in the GUI for the rest of the run.
            if (
                forced_congestion_edge_ids
                and not congestion_injected[0]
                and step_idx >= getattr(args, "force_congestion_at_step", 30)
            ):
                n_penalized = _force_edge_congestion(traci_module, forced_congestion_edge_ids)
                congestion_injected[0] = True
                rerouted_count = 0
                highlight_duration = max(3600.0, float(getattr(args, "reroute_highlight_seconds", 3600.0)))
                print(
                    f"\n[SUMO][CONGESTION] *** Dalhousie Square FULLY CONGESTED at t={sim_time:.0f}s *** "
                    f"{n_penalized} edges penalized"
                )
                print(f"{'─'*80}")
                changed_log: list[tuple[str, str, str]] = []
                unchanged_count = 0
                for vid in vehicle_ids:
                    if not _is_reroute_safe_now(traci_module, vid):
                        continue
                    try:
                        # Snapshot CURRENT route before rerouting
                        before_route = list(traci_module.vehicle.getRoute(vid))
                        before_idx = int(traci_module.vehicle.getRouteIndex(vid))
                        before_remaining = before_route[max(0, before_idx):]

                        # Clear any via constraints so vehicle can freely reroute
                        traci_module.vehicle.setVia(vid, [])
                        traci_module.vehicle.rerouteTraveltime(vid)
                        reroute_highlight_until[vid] = sim_time + highlight_duration
                        rerouted_count += 1

                        # Snapshot NEW route after rerouting
                        after_route = list(traci_module.vehicle.getRoute(vid))
                        after_idx = int(traci_module.vehicle.getRouteIndex(vid))
                        after_remaining = after_route[max(0, after_idx):]

                        intended_rsus = _get_rsus_on_route(
                            before_remaining, junction_to_rsu_name, edge_to_junctions
                        )
                        rerouted_rsus = _get_rsus_on_route(
                            after_remaining, junction_to_rsu_name, edge_to_junctions
                        )
                        # Always log controlled cohort vehicles; only log others when path changes
                        is_controlled = vid.startswith("controlled_group_flow")
                        if intended_rsus != rerouted_rsus or is_controlled:
                            intended_str = " → ".join(intended_rsus) if intended_rsus else "(no RSU)"
                            rerouted_str = " → ".join(rerouted_rsus) if rerouted_rsus else "(no RSU)"
                            changed_log.append((vid, intended_str, rerouted_str))
                        else:
                            unchanged_count += 1
                    except Exception:
                        continue

                # Print table only for vehicles whose RSU path changed
                if changed_log:
                    print(f"{'─'*90}")
                    print(f"  {'VehicleID':<28}  {'Intended RSU path':<32}  Re-routed RSU path")
                    print(f"{'─'*90}")
                    for vid, intended_str, rerouted_str in changed_log:
                        print(f"  {vid:<28}  {intended_str:<32}  {rerouted_str}")
                    print(f"{'─'*90}")
                print(
                    f"[SUMO][CONGESTION] {rerouted_count} vehicles rerouted (shown RED) | "
                    f"path changed: {len(changed_log)} | path unchanged: {unchanged_count}\n"
                )

            if not args.enable_hybrid_uplink_stub:
                return

            if sim_time - last_hybrid_push_sim_time < max(0.1, args.hybrid_batch_seconds):
                return

            # ── Edge weight updates DISABLED ───────────────────────────────────
            # Testing showed that dynamic edge weight updates cause route oscillation
            # where vehicles all switch to alternate routes simultaneously.
            # TODO: Re-enable with per-vehicle randomization to prevent herding.
            # if step_idx % 10 == 0:
            #     edges_updated = _update_edge_weights_from_congestion(traci_module, conservative=True)
            #     if edges_updated > 10 and step_idx % 100 == 0:
            #         print(f"[SUMO][HYBRID] Updated {edges_updated} edge weights")

            # ── One-time RSU graph registration so GNN has real topology ──────
            if not rsu_graph_registered[0] and rsu_alias_table:
                register_url = args.server_url.rstrip("/") + "/graph/register"
                rsu_graph_registered[0] = _try_register_rsu_graph(
                    register_url,
                    rsu_alias_table,
                    rsu_display_name_by_jid=junction_to_rsu_name,
                    graph_edges=rsu_graph_edges,
                    k_neighbors=3,
                    timeout=max(0.5, args.route_timeout_seconds),
                )

            # ── Per-RSU vehicle segmentation ──────────────────────────────────
            # Collect vehicle positions to assign each vehicle to its nearest RSU.
            vehicle_positions: dict[str, tuple[float, float]] = {}
            vehicle_speeds: dict[str, float] = {}
            for vid in vehicle_ids:
                try:
                    vehicle_positions[vid] = traci_module.vehicle.getPosition(vid)
                    vehicle_speeds[vid] = float(traci_module.vehicle.getSpeed(vid))
                except Exception:
                    pass

            rsu_range_m = max(5.0, args.rsu_range_m)
            # Map junction_id → vehicles within coverage range
            rsu_vehicle_map: dict[str, list[str]] = {}
            rsu_speed_sum_map: dict[str, float] = {}
            rsu_batch_metrics: list[dict[str, object]] = []
            if rsu_alias_table:
                for _alias, jid, _rx, _ry in rsu_alias_table:
                    rsu_vehicle_map.setdefault(jid, [])
                    rsu_speed_sum_map.setdefault(jid, 0.0)

                for vid, (vx, vy) in vehicle_positions.items():
                    best_jid: str | None = None
                    best_dist = float("inf")
                    for _alias, jid, rx, ry in rsu_alias_table:
                        d = math.hypot(vx - rx, vy - ry)
                        if d < best_dist:
                            best_dist = d
                            best_jid = jid
                    if best_jid is not None and best_dist <= rsu_range_m:
                        rsu_vehicle_map.setdefault(best_jid, []).append(vid)
                        rsu_speed_sum_map[best_jid] = rsu_speed_sum_map.get(best_jid, 0.0) + vehicle_speeds.get(vid, 0.0)

                for _alias, jid, _rx, _ry in rsu_alias_table:
                    assigned_vehicles = rsu_vehicle_map.get(jid, [])
                    vehicle_count_for_rsu = len(assigned_vehicles)
                    speed_sum_for_rsu = rsu_speed_sum_map.get(jid, 0.0)
                    avg_speed_for_rsu = (speed_sum_for_rsu / vehicle_count_for_rsu) if vehicle_count_for_rsu > 0 else 13.89
                    rsu_batch_metrics.append({
                        "rsu_id": jid,
                        "vehicle_count": vehicle_count_for_rsu,
                        "avg_speed_mps": avg_speed_for_rsu,
                    })

            # Select dominant RSU (most vehicles within coverage) for this batch
            if rsu_batch_metrics:
                dominant_metric = max(
                    rsu_batch_metrics,
                    key=lambda item: int(item.get("vehicle_count", 0)),
                )
                dominant_jid = str(dominant_metric.get("rsu_id", "global_stub"))
                local_vehicles = rsu_vehicle_map[dominant_jid]
                avg_speed_mps = float(dominant_metric.get("avg_speed_mps", 0.0))
            else:
                dominant_jid = "global_stub"
                local_vehicles = vehicle_ids
                avg_speed_mps = (
                    sum(vehicle_speeds.get(vid, 0.0) for vid in local_vehicles) / len(local_vehicles)
                    if local_vehicles
                    else 0.0
                )

            congested_rsu_ids = _collect_congested_rsu_ids(
                rsu_batch_metrics,
                manual_forced_congested_rsus,
                min_vehicle_count=congestion_min_vehicle_count,
                max_avg_speed_mps=congestion_max_avg_speed_mps,
            )
            congested_edge_ids = _collect_edges_for_congested_rsus(
                congested_rsu_ids,
                rsu_edges_by_jid,
            )
            penalized_edges = 0
            if congested_edge_ids:
                # Keep penalties time-bounded so the system can recover when RSU congestion clears.
                penalized_edges = _apply_temporary_edge_penalties(
                    traci_module,
                    congested_edge_ids,
                    sim_time=sim_time,
                    penalty_seconds=congestion_edge_penalty_seconds,
                    hold_seconds=congestion_edge_hold_seconds,
                )

            _dominant_congested = dominant_jid in congested_rsu_ids
            _dominant_vehicle_count = len(local_vehicles)
            # avg_latency proxy: matches runtime_logger.py training formula exactly:
            #   base=0.02s + 0.002s/vehicle + speed_penalty(0.003*(5-spd) when spd<5)
            if avg_speed_mps < 5.0:
                _avg_latency = 0.02 + (0.002 * _dominant_vehicle_count) + max(0.0, (5.0 - avg_speed_mps) * 0.003)
            else:
                _avg_latency = 0.02 + (0.002 * _dominant_vehicle_count)
            # Update cumulative per-RSU packet/byte counters (matches training data format)
            rsu_cumulative_packets[dominant_jid] = rsu_cumulative_packets.get(dominant_jid, 0) + _dominant_vehicle_count
            rsu_cumulative_bytes[dominant_jid] = rsu_cumulative_bytes.get(dominant_jid, 0) + (_dominant_vehicle_count * 128)
            uplink_payload = {
                "rsu_id": dominant_jid,
                "timestamp": sim_time,
                "vehicle_count": _dominant_vehicle_count,
                "avg_speed_mps": avg_speed_mps,
                "vehicle_ids": local_vehicles,
                "emergency_vehicle_ids": emergency_vehicle_ids,
                "congested_rsu_ids": sorted(congested_rsu_ids),
                "rsu_batch_metrics": rsu_batch_metrics,
                "features": {
                    "scenario": config.scenario,
                    "seed": args.seed,
                    "traffic_scale": traffic_scale,
                    "step": step_idx,
                    "dominant_rsu_vehicle_count": _dominant_vehicle_count,
                    "total_vehicle_count": len(vehicle_ids),
                    # Telemetry fields needed by forecast model feature builder
                    # packets_received and bytes_received are cumulative (matches training data)
                    "registered_telemetry_count": float(_dominant_vehicle_count),
                    "packets_received": float(rsu_cumulative_packets[dominant_jid]),
                    "bytes_received": float(rsu_cumulative_bytes[dominant_jid]),
                    "avg_latency_s": _avg_latency,
                    "congested_local": 1.0 if _dominant_congested else 0.0,
                    "congested_global": 1.0 if congested_rsu_ids else 0.0,
                },
            }

            route_url = args.server_url.rstrip("/") + "/route"
            reroutes_applied = 0
            route_response = _post_json(route_url, uplink_payload, timeout_seconds=max(0.1, args.route_timeout_seconds))
            if route_response is not None:
                corridor_paths, corridor_hold_seconds = _extract_green_corridor_paths_from_route_response(route_response)
                if corridor_paths:
                    # Translate RSU alias IDs to SUMO junction IDs so that
                    # _collect_green_corridor_tls_targets can match them
                    # against the junction_adjacency graph.
                    # Build case-insensitive lookup since aliases may be mixed-case.
                    _ci_alias_map = {k.upper(): v for k, v in rsu_alias_map.items()}
                    def _translate_rsu(token: str) -> str:
                        t = token.strip()
                        if t in rsu_alias_map:
                            return rsu_alias_map[t]
                        upper = t.upper()
                        if upper in _ci_alias_map:
                            return _ci_alias_map[upper]
                        return t  # already a junction ID or unknown

                    translated_paths = []
                    for _cp in corridor_paths:
                        translated_paths.append([_translate_rsu(rsu_id) for rsu_id in _cp])
                    if translated_paths != corridor_paths:
                        print(f"[SUMO][GreenCorridor] Translated RSU aliases → junction IDs: {translated_paths}")
                    corridor_paths = translated_paths
                    active_green_corridor_paths = corridor_paths
                    active_green_corridor_until = sim_time + max(0.5, corridor_hold_seconds)

                    # Build junction→TLS mapping on first use (lazy initialization)
                    if not junction_to_tls_cache:
                        junction_to_tls_cache.update(_build_junction_to_tls_map(
                            traci_module,
                            edge_to_junctions,
                            tls_outgoing_edge_index_cache,
                        ))
                    if not incoming_tls_cache:
                        incoming_tls_cache.update(_build_incoming_tls_map(
                            traci_module,
                            edge_to_junctions,
                            tls_outgoing_edge_index_cache,
                        ))

                    immediate_corridor_targets = _collect_green_corridor_tls_targets(
                        traci_module,
                        corridor_paths=active_green_corridor_paths,
                        edge_to_junctions=edge_to_junctions,
                        tls_outgoing_edge_index_cache=tls_outgoing_edge_index_cache,
                        tls_id_cache=tls_id_cache,
                        segment_path_cache=segment_junction_path_cache,
                        junction_reachability_cache=junction_reachability_cache,
                        junction_to_tls_map=junction_to_tls_cache,
                        incoming_tls_map=incoming_tls_cache,
                    )
                    _apply_lane_level_tls_preemption(
                        traci_module,
                        sim_time=sim_time,
                        hold_seconds=1.0,
                        tls_targets=immediate_corridor_targets,
                        tls_hold_until=manual_corridor_tls_hold_until,
                        tls_original_state=manual_corridor_tls_original_state,
                    )
                else:
                    if active_green_corridor_paths and manual_corridor_tls_hold_until:
                        print(f"[SUMO][GreenCorridor] /route returned no corridor data - restoring TLS")
                        _force_restore_all_corridor_tls(
                            traci_module,
                            manual_corridor_tls_hold_until,
                            manual_corridor_tls_original_state,
                        )
                    active_green_corridor_paths = []
                    active_green_corridor_until = -1.0

                reroute_result = _apply_server_reroute_policy(
                    traci_module,
                    local_vehicles,
                    route_response,
                    sim_time=sim_time,
                    reroute_cooldown_until=reroute_cooldown_until,
                    reroute_cooldown_seconds=reroute_cooldown_seconds,
                )
                reroutes_applied = int(reroute_result.get("count", 0))
                if args.reroute_highlight_seconds > 0:
                    hold_until = sim_time + max(0.1, args.reroute_highlight_seconds)
                    for vid in reroute_result.get("vehicle_ids", []):
                        reroute_highlight_until[str(vid)] = hold_until
                print(
                    "[SUMO][HYBRID] /route rsu={rsu} p={p:.2f} u={u:.2f} c={c:.2f} "
                    "risk={risk} strategy={strat} reroutes={r}".format(
                        rsu=route_response.get("rsu_id", "?"),
                        p=float(route_response.get("p_congestion", 0.0)),
                        u=float(route_response.get("uncertainty", 1.0)),
                        c=float(route_response.get("confidence", 0.0)),
                        risk=route_response.get("risk_level", "unknown"),
                        strat=(
                            route_response.get("gnn_routing", {}).get("strategy")
                            or route_response.get("phase3", {}).get("strategy")
                            or route_response.get("forecast_source", "?")
                        ),
                        r=reroutes_applied,
                    )
                )

            local_congestion_reroute_result = {"count": 0, "vehicle_ids": [], "candidate_count": 0}
            if congested_edge_ids:
                emergency_vehicle_set = {str(vid) for vid in emergency_vehicle_ids}
                local_congestion_reroute_result = _reroute_vehicles_away_from_edges(
                    traci_module,
                    vehicle_ids,
                    blocked_edge_ids=congested_edge_ids,
                    sim_time=sim_time,
                    reroute_cooldown_until=reroute_cooldown_until,
                    reroute_cooldown_seconds=reroute_cooldown_seconds,
                    max_fraction=congestion_reroute_fraction,
                    skip_vehicle_ids=emergency_vehicle_set,
                )
                local_reroutes_applied = int(local_congestion_reroute_result.get("count", 0))
                if local_reroutes_applied > 0 and args.reroute_highlight_seconds > 0:
                    hold_until = sim_time + max(0.1, args.reroute_highlight_seconds)
                    for vid in local_congestion_reroute_result.get("vehicle_ids", []):
                        reroute_highlight_until[str(vid)] = hold_until

                if local_reroutes_applied > 0:
                    print(
                        "[SUMO][CONGESTION] active_rsus={a} edges={e} penalized={p} affected={c} reroutes={r}".format(
                            a=len(congested_rsu_ids),
                            e=len(congested_edge_ids),
                            p=penalized_edges,
                            c=int(local_congestion_reroute_result.get("candidate_count", 0)),
                            r=local_reroutes_applied,
                        )
                    )

            last_hybrid_push_sim_time = sim_time

        executed_steps = adapter.run_step_loop(
            max_steps=max_steps,
            stop_when_no_vehicles=config.stop_when_no_vehicles,
            on_step=_on_step
            if (
                args.enable_hybrid_uplink_stub
                or args.enable_emergency_priority
                or args.controlled_count > 0
                or args.emergency_count > 0
                or runtime_logger is not None
                or rl_signal_controller is not None
                or bool(forced_congestion_edge_ids)
                or tgcn_engine is not None
            )
            else None,
        )
        print(f"[SUMO] Executed steps: {executed_steps}")
        if rl_signal_controller is not None:
            rl_summary = rl_signal_controller.summary()
            print(
                "[SUMO][RL] summary: junctions={j} steps={s} signal_switches={sw}".format(
                    j=rl_summary.get("junctions_controlled", 0),
                    s=rl_summary.get("total_steps", 0),
                    sw=rl_summary.get("signal_switches", 0),
                )
            )

        # ── T-GCN Final Summary ───────────────────────────────────────────
        if tgcn_engine is not None:
            try:
                summary = tgcn_engine.get_metrics_summary()
                print("\n" + "=" * 70)
                print("T-GCN NEURAL NETWORK METRICS SUMMARY")
                print("=" * 70)

                train_metrics = summary.get("train", {})
                print(f"\n📊 Training Metrics (last {train_metrics.get('total_samples', 0)} samples):")
                print(f"   MAE  (Mean Absolute Error):     {train_metrics.get('mae', 0):.4f}")
                print(f"   RMSE (Root Mean Squared Error): {train_metrics.get('rmse', 0):.4f}")
                print(f"   MAPE (Mean Abs % Error):        {train_metrics.get('mape', 0):.2f}%")
                print(f"   Accuracy:                       {train_metrics.get('accuracy', 0):.2%}")
                print(f"   Precision:                      {train_metrics.get('precision', 0):.2%}")
                print(f"   Recall:                         {train_metrics.get('recall', 0):.2%}")
                print(f"   F1 Score:                       {train_metrics.get('f1_score', 0):.2%}")
                print(f"   Average Loss:                   {train_metrics.get('avg_loss', 0):.4f}")

                stats = summary.get("training_stats", {})
                print(f"\n🔧 Training Stats:")
                print(f"   Total training steps:  {stats.get('total_steps', 0)}")
                print(f"   Replay buffer size:    {stats.get('buffer_size', 0)}")
                print(f"   Device:                {stats.get('device', 'cpu')}")

                config = summary.get("config", {})
                print(f"\n🏗️  Model Architecture:")
                print(f"   Hidden dimension:      {config.get('hidden_dim', 'N/A')}")
                print(f"   Sequence length:       {config.get('seq_length', 'N/A')}")
                print(f"   Number of RSU nodes:   {config.get('num_nodes', 'N/A')}")
                print(f"   Learning rate:         {config.get('learning_rate', 'N/A')}")

                print("=" * 70)

                # Save final model
                final_model_path = Path(getattr(args, "tgcn_checkpoint_dir", "models/tgcn")) / "tgcn_final.pt"
                tgcn_engine.save(str(final_model_path))
                print(f"\n💾 Final model saved to: {final_model_path}")

                # Save metrics history to JSON
                metrics_path = Path(getattr(args, "tgcn_checkpoint_dir", "models/tgcn")) / "metrics_history.json"
                import json
                with open(metrics_path, "w") as f:
                    json.dump(summary, f, indent=2, default=str)
                print(f"📈 Metrics history saved to: {metrics_path}")
                print()

            except Exception as _tgcn_summary_exc:
                print(f"[SUMO][T-GCN] Could not generate summary: {_tgcn_summary_exc}")
    finally:
        if runtime_logger is not None:
            try:
                runtime_logger.close()
            except Exception:
                pass
        adapter.close(wait=True)


if __name__ == "__main__":
    main()
