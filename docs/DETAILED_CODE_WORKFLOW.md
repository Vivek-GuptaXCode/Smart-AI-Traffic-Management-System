# Detailed Code Workflow

This document explains how the project runs end-to-end, from SUMO simulation and V2X server decisions to data logging, model training, and ablation evaluation.

## 1. System Purpose

The codebase implements a hybrid traffic-management stack with these major capabilities:

- SUMO-based simulation and scenario orchestration
- RSU-style telemetry aggregation and server uplink
- Congestion forecasting with uncertainty outputs
- Confidence-aware risk routing (Phase 3)
- RL-based adaptive traffic signal control (Phase 4)
- Hybrid fusion and ablation evaluation (Phase 5)
- Emergency vehicle priority with corridor preemption

## 2. Main Entry Points

- `server.py`
  - Flask + SocketIO central server
  - Exposes `/graph`, `/graph/register`, `/status`, `/route`
  - Combines deterministic, forecast, GNN, and Phase 3 routing logic behind feature flags

- `sumo/run_sumo_pipeline.py`
  - Main SUMO orchestrator
  - Parses all runtime flags, builds SUMO command, and executes step loop
  - Integrates runtime logging, hybrid uplink, emergency priority, RL signal control, and optional T-GCN

- `sumo/sumo_adapter.py`
  - Thin wrapper around TraCI/libsumo
  - Provides deterministic `run_step_loop(max_steps, on_step=...)`

## 3. High-Level Runtime Sequence

```
Start server.py
   -> preload RSU graph (optional from data/rsu_config_kolkata.json)
   -> wait for /route and /graph/register requests

Start sumo/run_sumo_pipeline.py
   -> load scenario contract + resolve sumocfg/net/routes
   -> build optional controlled and emergency cohorts
   -> start SUMO and enter step loop

Per step (or batch interval)
   -> collect vehicle state from SUMO
   -> optional emergency policy, RL signals, T-GCN metrics, and runtime logging
   -> optional /route uplink to server with RSU-local state
   -> apply reroute directives returned by server

After run
   -> close logger + SUMO adapter
   -> optional KPI files and model summaries are written
```

## 4. Server Workflow (`server.py`)

### 4.1 Initialization

At startup, `server.py`:

1. Creates Flask app, CORS, and SocketIO server.
2. Initializes an in-memory `networkx` RSU graph.
3. Attempts to preload RSUs from `data/rsu_config_kolkata.json`.
4. Builds static KNN connectivity between RSUs (3 nearest neighbors).
5. Initializes optional lazy-loaded components:
   - Forecast artifact engine (`models/forecast/inference.py`)
   - GNN reroute engine (`routing/gnn_reroute_engine.py`)
   - Route audit logger (`routing/route_audit_logger.py`)

### 4.2 HTTP Endpoints

- `GET /graph`
  - Returns current RSU graph nodes and edges.

- `POST /graph/register`
  - Accepts RSU topology payload (`nodes`, `edges`) from SUMO side.
  - Updates server-side RSU graph used by GNN routing.

- `GET /status`
  - Returns latest congestion-related event log.

- `POST /route`
  - Core policy endpoint consumed by SUMO runner.

### 4.3 `/route` Decision Pipeline

Given payload fields like `rsu_id`, `timestamp`, `vehicle_ids`, `avg_speed_mps`, and optional `forecast`:

1. Validate payload and types.
2. Build deterministic surrogate baseline:
   - `p_congestion` from vehicle count and speed
   - `confidence` from distance-to-boundary heuristic
3. Override/upgrade source in this priority order:
   - Explicit `forecast` from request payload
   - GNN reroute engine (if `HYBRID_ENABLE_GNN_ROUTING=1`)
   - Forecast artifact model (if `HYBRID_ENABLE_FORECAST_MODEL=1`)
4. Derive `risk_level` (`low`/`medium`/`high`).
5. Build default `recommended_action` and optional `route_directives`.
6. If `HYBRID_ENABLE_PHASE3_ROUTING=1`, run Phase 3 decision builder:
   - `routing.phase3_risk_router.build_phase3_decision(...)`
   - Replaces routing fields with confidence-aware risk policy.
7. Optionally audit decisions to JSONL when route audit logger is enabled.
8. Return response with forecast fields, recommended action, and diagnostics.

## 5. Phase 3 Routing Logic (`routing/phase3_risk_router.py`)

### 5.1 Config

`Phase3RoutingConfig.from_env()` reads tunables from env vars (examples):

- `HYBRID_P3_LOW_CONFIDENCE_THRESHOLD`
- `HYBRID_P3_UNCERTAINTY_WEIGHT`
- `HYBRID_P3_DELAY_SCALE_SECONDS`
- `HYBRID_P3_HIGH_RISK_SCORE`
- `HYBRID_P3_MEDIUM_RISK_SCORE`
- `HYBRID_P3_MAX_REROUTE_FRACTION`

### 5.2 Risk Computation

The router computes:

- `estimated_delay_s` from `vehicle_count`, `avg_speed_mps`, and `p_congestion`
- `delay_term = clamp(estimated_delay_s / delay_scale_seconds)`
- `uncertainty_term = clamp(uncertainty * uncertainty_penalty_weight)`
- `risk_score = clamp(delay_term + uncertainty_term)`

Decision branches:

- `emergency_override`: emergency vehicles get full priority reroute
- `confidence_fallback`: low-confidence predictions force conservative reroute policy
- `risk_aware_primary`: normal confidence-aware risk routing

Output includes `recommended_action`, `route_directives`, alternatives, and decision context for auditing.

## 6. SUMO Runtime Workflow (`sumo/run_sumo_pipeline.py`)

### 6.1 Startup and Preparation

`main()` performs:

1. Parse CLI flags (scenario, RSU config, logging, hybrid, RL, emergency, T-GCN, etc.).
2. Load scenario config from `sumo/scenarios/sumo_contract.json` via `load_scenario_config()`.
3. Resolve/additional route files from `.sumocfg`.
4. Build RSU table:
   - Custom from `--rsu-config`, or
   - Auto-selected junctions by lane-count + spacing.
5. Optional helper modes:
   - `--list-rsus`
   - `--suggest-near-junction`
6. Optional controlled cohort generation (`--controlled-*`).
7. Optional emergency cohort generation (`--emergency-*`, effective count is multiplied by 3).
8. Optional GUI overlays for RSU circles and labels.
9. Build SUMO command and start adapter.

### 6.2 Optional Modules Initialized Before Loop

- Runtime logger (`pipelines/logging/runtime_logger.py`) when `--enable-runtime-logging`
- RL signal controller (`controllers/rl/inference_hook.py`) when `--enable-rl-signal-control`
- T-GCN reroute engine (`routing/pytorch_gnn.py`) when `--enable-tgcn`
- Forced-congestion and initial-detour edge sets for controlled demonstrations

### 6.3 Per-Step Callback (`_on_step`)

Within adapter `run_step_loop(...)`, `_on_step(step_idx, sim_time, traci)` can perform:

1. Fetch live vehicle IDs.
2. Classify controlled vs emergency vehicles.
3. Apply first-insertion controlled-vehicle via-edge reroute (Dalhousie demo pathing).
4. Write 1 Hz runtime logs (RSU + edge snapshots).
5. Refresh visual markers and reroute highlights.
6. Run emergency priority policy at configured interval:
   - Emergency reroute to optimal path
   - Corridor edge preemption (stop non-emergency traffic temporarily)
   - Release traffic after timeout/passage
7. Run RL signal-control step at configured cadence.
8. Run optional T-GCN prediction/training bookkeeping.
9. Inject forced congestion and mass reroute if configured.
10. Hybrid uplink cycle (when enabled):
   - Register RSU graph once via `/graph/register`
   - Segment vehicles by nearest RSU (dominant RSU batch)
   - Send payload to `POST /route`
   - Apply returned server reroute policy via TraCI

### 6.4 Reroute Application Bridge

`_apply_server_reroute_policy(...)` handles runtime-safe rerouting:

- Checks confidence thresholds and reroute fraction caps
- Filters out vehicles unlikely to benefit from rerouting
- Prioritizes delayed vehicles first
- Supports `gnn_effort`, `travel_time`, and `dijkstra` modes
- Applies per-vehicle cooldown to reduce oscillations

## 7. Runtime Data Logging (Phase 1)

### 7.1 Logger

`pipelines/logging/runtime_logger.py` contains `SumoSimulationDataLogger`.

Every 1 simulation second it emits:

- `data/raw/<run_id>/rsu_features_1hz.csv`
- `data/raw/<run_id>/edge_flow_1hz.csv`
- `data/raw/<run_id>/logger_manifest.json`

### 7.2 Data Processing Workflow

`pipelines/processing/` scripts execute this sequence:

1. `horizon_labeler.py`
   - Adds horizon labels (default 60s/120s)
2. `temporal_split.py`
   - Chronological train/val/test split with temporal gap
3. `leakage_validator.py`
   - Validates chronology, overlap, and expected gap
4. `export_dataset_bundle.py`
   - Bundles datasets, manifests, hashes, and report metadata
5. `run_phase1_closure.sh`
   - One-command orchestrator for the above

## 8. Forecasting Workflow (Phase 2)

### 8.1 Training

Primary scripts in `models/forecast/`:

- `train_phase2_baselines.py` (v1 baseline ladder)
- `train_phase2_improved.py` (v2 expanded feature contract and model search)

### 8.2 Artifact and Inference

- Artifact metadata: `models/forecast/artifacts/latest/forecast_artifact.json`
- Model file: typically `model.pkl` (or model-specific format)
- Inference engine: `ForecastInferenceEngine` in `models/forecast/inference.py`

Inference engine behavior:

1. Loads artifact metadata.
2. Chooses feature builder (`v1` or `v2`) using `feature_contract.version`.
3. Maintains per-RSU rolling state.
4. Predicts `p_congestion`, then derives `confidence` and `uncertainty`.

## 9. RL Signal Control Workflow (Phase 4)

### 9.1 Runtime Inference

`controllers/rl/inference_hook.py` (`RLSignalController`) in SUMO loop:

1. Discovers controllable TLS IDs (or uses user-provided list).
2. Loads saved RL weights if available.
3. Falls back to MaxPressure policy if weights unavailable.
4. Applies actions with guardrails (min green, yellow transition, anti-oscillation).
5. Logs periodic diagnostics.

### 9.2 Training Driver

`controllers/rl/train_phase4.py` supports:

- Single-junction training
- Shared all-junction training
- Profile presets (`smoke`, `medium`, `full`)
- Optional reference-policy warm-start and demonstration pretraining

Typical outputs:

- `models/rl/artifacts/latest/weights.npz`
- `models/rl/artifacts/latest/meta.json`
- `evaluation/phase4_kpi_results.json` (or configured path)

## 10. Fusion and Ablation Workflow (Phase 5)

### 10.1 Orchestrator

`controllers/fusion/fusion_orchestrator.py` coordinates:

- Forecast inputs
- Routing outputs
- Signal outputs
- Coordination hints and emergency override state

It tracks pre-emptive triggers, subsystem call counts, and decision logs.

### 10.2 Ablation Runner

`controllers/fusion/run_ablation.py`:

1. Selects profile (`smoke`/`medium`/`full`) and ablation suite.
2. Ensures server is reachable (can auto-start managed `server.py`).
3. Runs `sumo/run_sumo_pipeline.py` per ablation x seed.
4. Parses KPI outputs (`tripinfo.xml`, summary text).
5. Computes mean/std/95% CI statistics.
6. Evaluates phase gates P5.1/P5.2/P5.3.
7. Writes report to `evaluation/phase5_ablation_results.json`.

Ablation definitions live in `controllers/fusion/ablation_configs.py`.

## 11. Environment Flags That Control Behavior

Common runtime flags used in production experiments:

- Forecast and Phase 3:
  - `HYBRID_ENABLE_FORECAST_MODEL=1`
  - `HYBRID_FORECAST_ARTIFACT=...`
  - `HYBRID_ENABLE_PHASE3_ROUTING=1`

- Optional GNN:
  - `HYBRID_ENABLE_GNN_ROUTING=1`

- Routing stability tuning:
  - `HYBRID_P3_MAX_REROUTE_FRACTION`
  - `HYBRID_REROUTE_FRACTION_CAP`
  - `HYBRID_REROUTE_COOLDOWN_SECONDS`
  - `HYBRID_REROUTE_MIN_CONF_FLOOR`

## 12. Typical End-to-End Operating Workflow

### A) Online Hybrid Simulation

1. Start server with required env flags.
2. Run SUMO pipeline with:
   - `--enable-hybrid-uplink-stub`
   - optional `--enable-rl-signal-control`
   - optional `--enable-emergency-priority`
   - optional `--enable-runtime-logging`
3. Monitor `/route` summaries in terminal logs and generated runtime artifacts.

### B) Offline Model Pipeline

1. Generate raw logs from multiple SUMO seeds.
2. Run Phase 1 processing and leakage validation.
3. Train/update Phase 2 forecast artifact.
4. Train/update Phase 4 RL artifact.
5. Run Phase 3 comparisons and Phase 5 ablations.
6. Archive KPI JSONs from `evaluation/` and reports from `docs/reports/`.

## 13. Key Output Locations

- Runtime logs: `data/raw/<run_id>/`
- Processed labels/splits: `data/processed/`, `data/splits/`
- Export bundles: `data/exports/`
- Forecast artifacts: `models/forecast/artifacts/latest/`
- RL artifacts: `models/rl/artifacts/latest/`
- Route audit log (default): `data/raw/route_audit/route_decisions.jsonl`
- Evaluations: `evaluation/*.json`

## 14. Notes for Maintainers

- Prefer feature flags for additive behavior; keep deterministic fallback paths intact.
- Keep route decisions auditable (`phase3` payload + `route_audit_id`).
- Preserve temporal split-before-normalize discipline in training pipelines.
- Use staged run profiles (`smoke` -> `medium` -> `full`) for reproducible iteration.
