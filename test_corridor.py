#!/usr/bin/env python
import requests
import json
import time

time.sleep(3)

body = {
    'anchor_rsu_id': '9491482575',
    'source_rsu_id': '9491482575',
    'destination_rsu_id': 'cluster_10282080280_10846969131_11365834325_2281800978_#2more',
    'strategy': 'rsu_shortest_path_v1'
}

try:
    print("[TEST] Sending green corridor request...")
    resp = requests.post('http://localhost:5000/signals/green-corridor', json=body, timeout=5)
    print(f"[TEST] Status: {resp.status_code}")
    result = resp.json()
    corridor = result.get('corridor', {})
    print(f"[TEST] Corridor ID: {corridor.get('corridor_id')}")
    print(f"[TEST] RSU Path: {corridor.get('rsu_ids')}")
    print(f"[TEST] Hold Seconds: {corridor.get('hold_seconds')}")
    print("[TEST] Waiting for SUMO to process...")
except Exception as e:
    print(f"[TEST] Error: {type(e).__name__}: {e}")
