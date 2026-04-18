# # ============================================================
# # Sealdah → Park Circus  |  Dalhousie Square BLOCKED demo
# # ============================================================
# # Scenario:
# #   • 30 AI-controlled vehicles depart from Sealdah RSU
# #   • Dalhousie Square blocked at step 60
# #   • T-GCN prediction and RL signal control enabled
# # ============================================================

# # Stop on error
# $ErrorActionPreference = "Stop"

# # Get the directory where the script is located
# $SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Definition
# Set-Location $SCRIPT_DIR

# # 1. Activate Python Environment
# # Adjust this path if your virtual environment is named differently (e.g., .venv or venv)
# $VENV_PATH = "C:\Users\vivek\Desktop\ML-practice\ML-practice\Scripts\Activate.ps1"

# if (Test-Path $VENV_PATH) {
#     & $VENV_PATH
# } else {
#     Write-Warning "Virtual environment not found at $VENV_PATH. Attempting to use system python."
# }

# # 2. Create T-GCN model directory
# if (!(Test-Path "models\tgcn")) {
#     New-Item -ItemType Directory -Path "models\tgcn" -Force
# }

# # ── Junction IDs ─────────
# $SEALDAH_JID = "9491482575"
# $PARK_CIRCUS_JID = "cluster_10281986033_10302557856_10302557859_638354058"
# $DALHOUSIE_JID = "cluster_10281869257_12438122826_12438122827_663940666"

# Write-Host "==========================================" -ForegroundColor Cyan
# Write-Host "🚦 SUMO GUI Demo Starting (Windows)..." -ForegroundColor Cyan
# Write-Host "=========================================="
# Write-Host ""
# Write-Host "📋 Demo Configuration:"
# Write-Host "   • Route: Sealdah RSU -> Park Circus RSU"
# Write-Host "   • Controlled vehicles: 30 (shown in DEEP BLUE)"
# Write-Host "   • Congestion trigger: Step 60 at Dalhousie Square"
# Write-Host "   • T-GCN: ENABLED"
# Write-Host ""
# Write-Host "⚡ GUI will open in a moment..."
# Write-Host "   IMPORTANT: Click ▶️ PLAY button in SUMO GUI to start!"
# Write-Host ""
# Start-Sleep -Seconds 3

# # 3. Run the Python Pipeline
# # We use `python` (typical for Windows) instead of `python3`
# python -u sumo/run_sumo_pipeline.py `
#     --scenario            kolkata `
#     --gui `
#     --seed                11 `
#     --max-steps           3600 `
#     --traffic-scale       1.0 `
#     --rsu-config          data/rsu_config_kolkata.json `
#     --rsu-range-m         120 `
#     --rsu-min-inc-lanes   4 `
#     --controlled-count    30 `
#     --controlled-source   "$SEALDAH_JID" `
#     --controlled-destination "$PARK_CIRCUS_JID" `
#     --controlled-begin    10 `
#     --controlled-end      600 `
#     --force-congestion-at-junction "$DALHOUSIE_JID" `
#     --force-congestion-at-step     60 `
#     --reroute-highlight-seconds    3600 `
#     --enable-tgcn `
#     --tgcn-train `
#     --tgcn-log-interval   50 `
#     --tgcn-checkpoint-dir models/tgcn `
#     --enable-hybrid-uplink-stub `
#     --server-url          http://localhost:5000 `
#     --hybrid-batch-seconds 5 `
#     --route-timeout-seconds 1.5 `
#     --enable-rl-signal-control `
#     --rl-model-dir         models/rl/artifacts/research_backed_kolkata_reference_v2 `
#     --rl-min-green-seconds 15 `
#     --rl-yellow-duration-seconds 3 `
#     --rl-max-controlled-tls 96 `
#     --rl-step-interval-steps 5 `
#     --enable-runtime-logging `
#     --runtime-log-root     data/raw `
#     --marker-refresh-steps 4 `
#     --emergency-corridor-lookahead-edges 6 `
#     --emergency-hold-seconds 8 `
#     $args
# ============================================================
# Sealdah to Park Circus | Dalhousie Square BLOCKED demo
# ============================================================
# Scenario
#   - 30 AI-controlled vehicles depart from Sealdah RSU and
#     head to Park Circus RSU via the normal direct path.
#   - At step 60, Dalhousie Square RSU is declared FULLY CONGESTED.
#   - All vehicles are instantly re-routed around Dalhousie.
# ============================================================
$ErrorActionPreference = "Stop"

# Junction IDs
$SEALDAH_JID = "9491482575"
$PARK_CIRCUS_JID = "cluster_10281986033_10302557856_10302557859_638354058"

Write-Host "=========================================="
Write-Host "SUMO GUI Demo Starting..."
Write-Host "=========================================="
Write-Host ""
Write-Host "Demo Configuration:"
Write-Host "   • Route: Sealdah RSU -> Park Circus RSU"
Write-Host "   • Controlled vehicles: 30 (shown in DEEP BLUE)"
Write-Host "   • Real-time congestion detection enabled"
Write-Host "   • T-GCN: ENABLED"
Write-Host ""
Write-Host "Opening GUI..."
Write-Host "IMPORTANT: Click the PLAY button in SUMO GUI to start!"
Write-Host ""
Write-Host "=========================================="

Start-Sleep -Seconds 2

$argsList = @(
    "--scenario", "kolkata",
    "--gui",
    "--seed", "11",
    "--max-steps", "3600",
    "--traffic-scale", "1.0",
    "--rsu-config", "data/rsu_config_kolkata.json",
    "--rsu-range-m", "120",
    "--rsu-min-inc-lanes", "4",
    "--controlled-count", "30",
    "--controlled-source", $SEALDAH_JID,
    "--controlled-destination", $PARK_CIRCUS_JID,
    "--controlled-begin", "10",
    "--controlled-end", "600",
    "--reroute-highlight-seconds", "3600",
    "--enable-tgcn",
    "--tgcn-train",
    "--tgcn-log-interval", "50",
    "--tgcn-checkpoint-dir", "models/tgcn",
    "--enable-hybrid-uplink-stub",
    "--server-url", "http://localhost:5000",
    "--hybrid-batch-seconds", "5",
    "--route-timeout-seconds", "1.5",
    "--enable-rl-signal-control",
    "--rl-model-dir", "models/rl/artifacts/research_backed_kolkata_reference_v2",
    "--rl-min-green-seconds", "15",
    "--rl-yellow-duration-seconds", "3",
    "--rl-max-controlled-tls", "96",
    "--rl-step-interval-steps", "5",
    "--enable-runtime-logging",
    "--runtime-log-root", "data/raw",
    "--marker-refresh-steps", "4",
    "--emergency-corridor-lookahead-edges", "6",
    "--emergency-hold-seconds", "8"
)

python -u sumo/run_sumo_pipeline.py @argsList