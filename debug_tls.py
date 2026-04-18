import libsumo as traci

cfg_file = 'sumo/scenarios/kolkata_config.sumocfg'
try:
    traci.start(['sumo', '-c', cfg_file, '-S', '--start'])
    tls_ids = traci.trafficlight.getIDList()
    print('AVAILABLE TLS IDs IN SUMO:')
    print(f'Total TLS: {len(tls_ids)}')
    for tls_id in sorted(tls_ids)[:20]:
        print(f'  {tls_id}')
    traci.close()
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
