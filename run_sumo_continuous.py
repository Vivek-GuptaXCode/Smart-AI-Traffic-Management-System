#!/usr/bin/env python
"""Keep SUMO running in cycles indefinitely for green corridor testing."""
import subprocess
import sys
import time
import os

max_runs = 100
run = 0

while run < max_runs:
    run += 1
    print(f"\n{'='*60}")
    print(f"SUMO RUN #{run}/{max_runs}")
    print('='*60)
    
    cmd = [
        sys.executable, "-m", "sumo.run_sumo_pipeline",
        "--scenario", "kolkata",
        "--rsu-config", "data/rsu_config_kolkata.json",
        "--max-steps", "10000"  # Extended for each cycle
    ]
    
    try:
        result = subprocess.run(cmd, timeout=None)
        print(f"SUMO run #{run} completed with code {result.returncode}")
    except KeyboardInterrupt:
        print("\nGreen corridor testing stopped by user")
        break
    except Exception as e:
        print(f"Error running SUMO: {e}")
    
    time.sleep(2)  # Brief pause between runs

print("Test complete")
