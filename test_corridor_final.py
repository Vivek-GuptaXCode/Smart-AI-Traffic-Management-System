import requests
import json
import time

print('[USER] Sending green corridor request to trigger TLS preemption...')
body = {
    'action': 'activate',
    'anchor_rsu_id': 'Shyambazar',
    'source_rsu_id': 'Shyambazar',
    'destination_rsu_id': 'Sovabazar',
    'hold_seconds': 120,
    'persistent': True,
    'reason': 'test_corridor',
    'created_by': 'test_script',
}

try:
    resp = requests.post('http://localhost:5000/signals/green-corridor', json=body, timeout=5)
    result = resp.json()
    print(f'[USER] Response Status: {resp.status_code}')
    print(json.dumps(result, indent=2))

    corridor = result.get('corridor', {})
    print(f'\n[USER] Corridor ID:   {corridor.get("corridor_id")}')
    print(f'[USER] RSU Path:      {corridor.get("rsu_ids")}')
    print(f'[USER] Strategy:      {corridor.get("strategy")}')
    print(f'[USER] Hold Seconds:  {corridor.get("hold_seconds")}')
    print(f'[USER] Persistent:    {corridor.get("persistent")}')
    print(f'[USER] Remaining:     {corridor.get("remaining_seconds")}s')

    print('\n[USER] Verifying via GET /signals/green-corridor ...')
    time.sleep(1)
    get_resp = requests.get('http://localhost:5000/signals/green-corridor', timeout=5)
    get_result = get_resp.json()
    print(f'[USER] Active corridors: {get_result.get("active_count")}')
    for c in get_result.get('active_corridors', []):
        print(f'  - {c.get("corridor_id")}: {c.get("source_rsu_id")} -> {c.get("destination_rsu_id")} '
              f'persistent={c.get("persistent")} remaining={c.get("remaining_seconds")}s')

    print('\n[USER] Green corridor ACTIVATED! Watch SUMO output for TLS preemption logs...')
    print('[USER] To clear: POST /signals/green-corridor with {"action": "clear"}')

except Exception as e:
    print(f'[USER] Error: {e}')
