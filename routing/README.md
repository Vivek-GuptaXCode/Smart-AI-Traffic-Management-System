# Routing

Phase 3: Uncertainty-Aware Risk Routing module.

## Overview

This module implements risk-aware vehicle routing that combines congestion probability predictions with uncertainty estimates to make robust routing decisions.

## Files

| File | Description |
|------|-------------|
| `phase3_risk_router.py` | Main risk-aware routing engine |
| `route_audit_logger.py` | JSONL audit trail for routing decisions |
| `gnn_reroute_engine.py` | GNN-based rerouting (experimental) |

## Architecture

```
Forecast Model (Phase 2)
    │
    ├── p_congestion (probability)
    └── uncertainty (model confidence)
            │
            ▼
    ┌─────────────────────────┐
    │   Risk Score Calculator  │
    │   risk = p + α * σ       │
    └─────────────────────────┘
            │
            ▼
    ┌─────────────────────────┐
    │   Confidence Fallback    │
    │   if uncertainty > τ     │
    │   → use default route    │
    └─────────────────────────┘
            │
            ▼
    ┌─────────────────────────┐
    │   Route Selection        │
    │   minimize risk score    │
    └─────────────────────────┘
```

## Usage

### Basic Usage

```python
from routing.phase3_risk_router import RiskAwareRouter
from models.forecast.inference import ForecastInferenceEngine

# Initialize
forecast_engine = ForecastInferenceEngine("models/forecast/artifacts/latest")
router = RiskAwareRouter(forecast_engine)

# Get routing decision
route = router.get_best_route(
    origin="RSU_A",
    destination="RSU_K",
    alternative_routes=["route_1", "route_2", "route_3"]
)
```

### With Audit Logging

```python
from routing.route_audit_logger import RouteAuditLogger

logger = RouteAuditLogger("data/raw/route_audit/")
router = RiskAwareRouter(forecast_engine, audit_logger=logger)

# Decisions are automatically logged to JSONL
route = router.get_best_route(origin, destination, alternatives)
```

## Risk Score Calculation

The risk score combines probability and uncertainty:

```python
risk_score = p_congestion + alpha * uncertainty
```

Where:
- `p_congestion`: Predicted probability of congestion [0, 1]
- `uncertainty`: Model uncertainty estimate [0, 1]
- `alpha`: Uncertainty weight (default: 0.5)

### Confidence Fallback

When uncertainty exceeds a threshold, the router falls back to default behavior:

```python
if uncertainty > confidence_threshold:
    return default_route  # Don't trust low-confidence predictions
```

Default threshold: `0.7`

## Configuration

```python
router = RiskAwareRouter(
    forecast_engine=engine,
    alpha=0.5,                    # Uncertainty weight
    confidence_threshold=0.7,     # Fallback threshold
    audit_logger=logger           # Optional logging
)
```

## Audit Log Format

Decisions are logged in JSONL format:

```json
{
  "timestamp": "2026-04-02T12:00:00Z",
  "vehicle_id": "veh_42",
  "origin": "RSU_A",
  "destination": "RSU_K",
  "selected_route": "route_2",
  "risk_scores": {
    "route_1": 0.72,
    "route_2": 0.45,
    "route_3": 0.68
  },
  "fallback_used": false,
  "uncertainty": 0.23
}
```

## Integration with Server

The router is integrated into `server.py`:

```python
# server.py
from routing.phase3_risk_router import RiskAwareRouter

if os.environ.get("HYBRID_ENABLE_PHASE3_ROUTING"):
    router = RiskAwareRouter(forecast_engine)
    
@socketio.on("route_request")
def handle_route_request(data):
    route = router.get_best_route(
        origin=data["origin"],
        destination=data["destination"],
        alternatives=data["alternatives"]
    )
    return {"route": route}
```

## Evaluation

See `evaluation/phase3_comparison.py` for baseline vs risk-aware comparison:

```bash
python3 evaluation/phase3_comparison.py
```

## KPI Targets

| Metric | Target |
|--------|--------|
| Congestion Avoidance Rate | ≥ 70% |
| Travel Time Delta vs Baseline | ≤ +5% |
| Prediction Accuracy | ≥ 80% |
