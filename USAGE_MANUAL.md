# V2X Green Corridor Dashboard — Usage Manual

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10+ |
| Node.js | 18+ |
| SUMO | 1.18+ (`sumo`, `sumo-gui`) |
| Python venv | `/home/vivek/Desktop/ML-practice/ML-practice/` |

---

## 1. Start the Backend (Flask Server)

```bash
cd "/home/vivek/research/NIT hackathon/final_year_project"
source /home/vivek/Desktop/ML-practice/ML-practice/bin/activate
python server.py
```

- Listens on **http://localhost:5000**
- Loads RSU graph from `data/rsu_config_kolkata.json`
- Manages active green corridors in-memory
- Emits Socket.IO events: `junction_broadcast`, `junction_clear_broadcast`, `green_corridor_broadcast`

---

## 2. Start the Frontend (Next.js Dashboard)

Open a **new terminal**:

```bash
cd "/home/vivek/research/NIT hackathon/final_year_project/frontend"
npm run dev
```

- Opens at **http://localhost:3000**
- Hot-reloads on code changes

---

## 3. Run the SUMO Simulation

Open a **new terminal**:

```bash
cd "/home/vivek/research/NIT hackathon/final_year_project"
source /home/vivek/Desktop/ML-practice/ML-practice/bin/activate
python sumo/run_sumo_pipeline.py \
  --scenario kolkata \
  --server-url http://localhost:5000 \
  --enable-hybrid-uplink-stub
```

Add `--gui` to open the SUMO GUI.

### All useful flags

| Flag | Effect |
|---|---|
| `--headless` | No GUI (faster) |
| `--enable-hybrid-uplink-stub` | Enables backend polling for green corridor updates |
| `--emergency-corridor-hold-seconds N` | How long TLS preemption holds (default 30s sim-time) |
| `--backend-url URL` | Flask server URL |

---

## 4. Using the Green Corridor Feature

### Activate a corridor from the dashboard

1. Open **http://localhost:3000** → **Global Topology** view
2. **Click** any RSU node → sets it as the **corridor source** (hint panel appears)
3. **Shift + Click** a second RSU node → triggers corridor activation
4. The graph highlights the path in green; all other edges dim
5. The **Event Feed** (right panel) shows:
   `Green corridor ACTIVE: Shyambazar → Sealdah (5 RSUs, 4 hops)`

### Clear an active corridor

- Click the **Clear** button in the green hint panel at the top of the graph
- The corridor is cleared on the server; TLS signals return to normal cycling
- Event Feed logs: `Green corridor CLEARED: Shyambazar → Sealdah`

### Corridor state after SUMO ends

- The dashboard shows the last active server corridor (green path + badge) until explicitly cleared
- The **Clear** button remains accessible even with no local selection — it reads the server state and clears it

---

## 5. One-Shot Script (start everything)

Save as `run_demo.sh` and `chmod +x run_demo.sh`:

```bash
#!/usr/bin/env bash
set -e
VENV="/home/vivek/Desktop/ML-practice/ML-practice/bin/activate"
PROJECT="/home/vivek/research/NIT hackathon/final_year_project"

source "$VENV"

# 1. Backend
cd "$PROJECT"
python server.py &
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID"
sleep 2

# 2. Frontend
cd "$PROJECT/frontend"
npm run dev &
FRONTEND_PID=$!
echo "Frontend PID: $FRONTEND_PID"
sleep 3

# 3. SUMO
cd "$PROJECT"
python sumo/run_sumo_pipeline.py \
  --scenario kolkata \
  --server-url http://localhost:5000 \
  --enable-hybrid-uplink-stub

# Cleanup on exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
```

---

## 6. API Quick Reference

### Activate a corridor (curl)

```bash
curl -s -X POST http://localhost:5000/signals/green-corridor \
  -H "Content-Type: application/json" \
  -d '{
    "action": "activate",
    "anchor_rsu_id": "Shyambazar",
    "source_rsu_id": "Shyambazar",
    "destination_rsu_id": "Sealdah",
    "hold_seconds": 120,
    "persistent": true
  }' | python3 -m json.tool
```

### Clear all corridors

```bash
curl -s -X POST http://localhost:5000/signals/green-corridor \
  -H "Content-Type: application/json" \
  -d '{"action": "clear"}' | python3 -m json.tool
```

### List active corridors

```bash
curl -s http://localhost:5000/signals/green-corridor | python3 -m json.tool
```

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard shows "Waiting for RSU Graph" | Backend not running or wrong port. Confirm `python server.py` started |
| Graph corridor not highlighted after SUMO ends | Click any node then **Clear** to resync, or wait for socket `green_corridor_broadcast` |
| `Port 3000 is in use` on npm run dev | A previous Next.js instance is running: `kill $(lsof -t -i:3000)` then retry |
| SUMO exits with `traci connection refused` | SUMO/TraCI binary not in PATH. Run `which sumo` to verify |
| Corridor activated but no TLS preemption in SUMO | Ensure `--enable-hybrid-uplink-stub` is set; check SUMO log for `[SUMO][GreenCorridor]` lines |
| RSU alias not resolved in SUMO | RSU names must match `rsu_alias_map` keys in `data/rsu_config_kolkata.json` (case-insensitive) |

---

## 8. Service Ports

| Service | Port | URL |
|---|---|---|
| Flask backend | 5000 | http://localhost:5000 |
| Next.js frontend | 3000 | http://localhost:3000 |

---

## 9. Key File Locations

```
final_year_project/
├── server.py                          # Flask + Socket.IO backend
├── sumo/
│   └── run_sumo_pipeline.py           # SUMO TraCI simulation controller
├── data/
│   └── rsu_config_kolkata.json        # RSU definitions (alias → SUMO junction ID)
├── frontend/
│   ├── src/
│   │   ├── store/useTrafficStore.ts   # Zustand state + Socket.IO handlers
│   │   └── components/
│   │       ├── NetworkGraph.tsx       # Graph + green corridor UI
│   │       └── RSUSpotlight.tsx       # RSU detail panel
│   └── .env.local                     # NEXT_PUBLIC_SERVER_URL=http://localhost:5000
└── USAGE_MANUAL.md                    # This file
```
