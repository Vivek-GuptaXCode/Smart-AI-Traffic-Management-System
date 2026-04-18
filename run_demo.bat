@echo off
REM ============================================================
REM Sealdah → Park Circus  |  Dalhousie Square BLOCKED demo
REM ============================================================

SETLOCAL EnableDelayedExpansion

:: 1. Activate Python Environment
:: Adjust this path to match your Windows user profile
CALL "venv\Scripts\activate.bat"

:: 2. Create T-GCN model directory
IF NOT EXIST "models\tgcn" (
    mkdir "models\tgcn"
)

:: ── Junction IDs ─────────
SET "SEALDAH_JID=9491482575"
SET "PARK_CIRCUS_JID=cluster_10281986033_10302557856_10302557859_638354058"

echo ==========================================
echo SUMO GUI Demo Starting (Batch Mode)...
echo ==========================================
echo.
echo Demo Configuration:
echo    - Route: Sealdah RSU --^> Park Circus RSU
echo    - Controlled vehicles: 30
echo    - Profile: Stable GUI-first (T-GCN/RL disabled by default)
echo.
echo NOTE:
echo    To enable full AI stack, pass flags manually after run_demo.bat,
echo    for example: run_demo.bat --enable-tgcn --enable-rl-signal-control
echo.
echo GUI will open in a moment...
echo    IMPORTANT: Click PLAY in SUMO GUI!
echo ==========================================

timeout /t 3 /nobreak > nul

:: 3. Run the Python Pipeline
:: Use ^ to handle multi-line commands in Batch
python -u sumo/run_sumo_pipeline.py ^
    --scenario            kolkata ^
    --gui ^
    --seed                11 ^
    --max-steps           3600 ^
    --traffic-scale       1.0 ^
    --rsu-config          data/rsu_config_kolkata.json ^
    --rsu-range-m         120 ^
    --rsu-min-inc-lanes   4 ^
    --controlled-count    30 ^
    --controlled-source   "%SEALDAH_JID%" ^
    --controlled-destination "%PARK_CIRCUS_JID%" ^
    --controlled-begin    10 ^
    --controlled-end      600 ^
    --reroute-highlight-seconds    3600 ^
    --enable-hybrid-uplink-stub ^
    --server-url          http://localhost:5000 ^
    --hybrid-batch-seconds 5 ^
    --route-timeout-seconds 1.5 ^
    --enable-runtime-logging ^
    --runtime-log-root     data/raw ^
    --marker-refresh-steps 4 ^
    --emergency-corridor-lookahead-edges 6 ^
    --emergency-hold-seconds 8 ^
    %*

IF ERRORLEVEL 1 (
    echo.
    echo [ERROR] Demo execution failed. SUMO GUI may have closed immediately.
    echo [HINT] Verify SUMO install and try a minimal command:
    echo        python sumo/run_sumo_pipeline.py --scenario kolkata --gui --max-steps 300
)

PAUSE