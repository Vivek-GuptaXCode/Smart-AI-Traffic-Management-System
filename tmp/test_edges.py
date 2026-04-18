import json
import math

def _build_rsu_knn_edges(rsu_list, k=3):
    nodes = [(rsu['id'], rsu['x'], rsu['y']) for rsu in rsu_list]
    edges = set()
    for i, (jid_a, xa, ya) in enumerate(nodes):
        distances = sorted(
            (round(math.hypot(xa - xb, ya - yb), 4), jid_b)
            for j, (jid_b, xb, yb) in enumerate(nodes)
            if i != j
        )
        # Check for ties at the k-th position
        if len(distances) > k:
            if distances[k-1][0] == distances[k][0]:
                print(f"TIE at {jid_a}: {distances[k-1]} and {distances[k]}")
        
        for _dist, jid_b in distances[:k]:
            edge = tuple(sorted((jid_a, jid_b)))
            edges.add(edge)
    return edges

with open('data/rsu_config_kolkata.json', 'r') as f:
    config = json.load(f)
    rsus = config['rsus']
    edges = _build_rsu_knn_edges(rsus, k=3)
    print(f"Edge count: {len(edges)}")
