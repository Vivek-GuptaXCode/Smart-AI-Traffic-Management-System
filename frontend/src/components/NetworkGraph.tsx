'use client';

import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { GraphEdge, useTrafficStore } from '@/store/useTrafficStore';
import styles from '@/app/page.module.css';
import { motion } from 'framer-motion';

// A simple deterministic pseudo-random function to spread nodes
const pseudoRandom = (seed: number) => {
  const x = Math.sin(seed++) * 10000;
  return x - Math.floor(x);
};

const buildUndirectedEdgeKey = (fromId: string, toId: string): string => {
  return [String(fromId), String(toId)].sort().join('::');
};

/**
 * Dijkstra's algorithm for weighted shortest path between two RSUs.
 * Edge weights are based on Euclidean distance between nodes (if positions
 * are available), otherwise uniform weight of 1 (equivalent to BFS).
 */
const findShortestPathDijkstra = (
  startId: string,
  endId: string,
  graphEdges: GraphEdge[],
  nodePositions?: Record<string, { x: number; y: number }>,
): string[] => {
  const source = String(startId).trim();
  const target = String(endId).trim();
  if (!source || !target) return [];
  if (source === target) return [source];

  // Build adjacency list with weights
  const adjacencyMap = new Map<string, { neighbor: string; weight: number }[]>();

  const ensureNode = (id: string) => {
    if (!adjacencyMap.has(id)) adjacencyMap.set(id, []);
  };

  graphEdges.forEach((edge) => {
    const from = String(edge.from).trim();
    const to = String(edge.to).trim();
    if (!from || !to) return;

    ensureNode(from);
    ensureNode(to);

    // Compute weight: Euclidean distance if positions available, else 1
    let weight = 1;
    if (nodePositions && nodePositions[from] && nodePositions[to]) {
      const dx = nodePositions[to].x - nodePositions[from].x;
      const dy = nodePositions[to].y - nodePositions[from].y;
      weight = Math.sqrt(dx * dx + dy * dy);
    }

    // Undirected graph
    adjacencyMap.get(from)!.push({ neighbor: to, weight });
    adjacencyMap.get(to)!.push({ neighbor: from, weight });
  });

  if (!adjacencyMap.has(source) || !adjacencyMap.has(target)) return [];

  // Dijkstra with a simple priority queue (array-based, fine for <100 nodes)
  const dist = new Map<string, number>();
  const prev = new Map<string, string | null>();
  const visited = new Set<string>();

  // Initialize all distances to infinity
  adjacencyMap.forEach((_, nodeId) => {
    dist.set(nodeId, Infinity);
    prev.set(nodeId, null);
  });
  dist.set(source, 0);

  // Priority queue as sorted array of [nodeId, distance]
  const pq: [string, number][] = [[source, 0]];

  while (pq.length > 0) {
    // Extract minimum distance node
    pq.sort((a, b) => a[1] - b[1]);
    const [currentNode, currentDist] = pq.shift()!;

    if (visited.has(currentNode)) continue;
    visited.add(currentNode);

    if (currentNode === target) break;

    const neighbors = adjacencyMap.get(currentNode);
    if (!neighbors) continue;

    for (const { neighbor, weight } of neighbors) {
      if (visited.has(neighbor)) continue;
      const newDist = currentDist + weight;
      if (newDist < (dist.get(neighbor) ?? Infinity)) {
        dist.set(neighbor, newDist);
        prev.set(neighbor, currentNode);
        pq.push([neighbor, newDist]);
      }
    }
  }

  if (!visited.has(target)) return [];

  // Reconstruct path
  const path: string[] = [];
  let cursor: string | null = target;
  while (cursor !== null) {
    path.push(cursor);
    cursor = prev.get(cursor) ?? null;
  }
  return path.reverse();
};

interface NetworkGraphProps {
  selectedNodeId?: string;
  onNodeClick?: (nodeId: string) => void;
  serverUrl?: string;
}

export default function NetworkGraph({ selectedNodeId, onNodeClick, serverUrl }: NetworkGraphProps) {
  const { nodes, edges, congestionState, greenCorridorByRsu, activeCorridors, triggerGreenCorridor, clearGreenCorridors, addSystemEvent } = useTrafficStore();
  const [dimensions, setDimensions] = useState({ width: 800, height: 400 });

  // Corridor control panel state
  const [dropdownSource, setDropdownSource] = useState<string>('');
  const [dropdownDest, setDropdownDest] = useState<string>('');
  const [holdSeconds, setHoldSeconds] = useState<number>(120);
  const [isApplyingCorridor, setIsApplyingCorridor] = useState(false);
  // Tracks which corridor_ids were created as 'reverse' (purple) vs 'forward' (green)
  const [directedCorridorMap, setDirectedCorridorMap] = useState<Map<string, 'forward' | 'reverse'>>(new Map());

  const nodePositions = useMemo(() => {
    const posMap: Record<string, { x: number, y: number }> = {};
    const padding = 60;

    // 1. Calculate SUMO coordinate bounds
    let minX = Infinity, maxX = -Infinity;
    let minY = Infinity, maxY = -Infinity;
    let hasCoords = false;

    nodes.forEach(n => {
      if (n.x !== undefined && n.y !== undefined) {
        hasCoords = true;
        minX = Math.min(minX, n.x);
        maxX = Math.max(maxX, n.x);
        minY = Math.min(minY, n.y);
        maxY = Math.max(maxY, n.y);
      }
    });

    const rangeX = (maxX - minX) || 1;
    const rangeY = (maxY - minY) || 1;

    nodes.forEach((node, i) => {
      let x, y;

      if (hasCoords && node.x !== undefined && node.y !== undefined) {
        // Normalize coordinates independently for X and Y to fill container
        const nx = (node.x - minX) / rangeX;
        const ny = (node.y - minY) / rangeY;
        // Flip Y for DOM coordinates
        x = padding + (nx * (dimensions.width - padding * 2));
        y = dimensions.height - padding - (ny * (dimensions.height - padding * 2));
      } else {
        // Fallback for nodes without coordinates
        const seed = node.id.charCodeAt(0) + i;
        x = padding + (pseudoRandom(seed) * (dimensions.width - padding * 2));
        y = padding + (pseudoRandom(seed + 100) * (dimensions.height - padding * 2));
      }

      posMap[node.id] = { x, y };
    });

    return posMap;
  }, [nodes, dimensions]);

  // Handle window resizing
  useEffect(() => {
    const updateSize = () => {
      const container = document.getElementById('network-container');
      if (container) {
        setDimensions({
          width: container.clientWidth,
          height: container.clientHeight
        });
      }
    };

    window.addEventListener('resize', updateSize);
    updateSize(); // init
    // try again after a small delay to ensure CSS has applied
    setTimeout(updateSize, 100);

    return () => window.removeEventListener('resize', updateSize);
  }, []);

  const availableNodeIds = useMemo(
    () => new Set(nodes.map((node) => node.id)),
    [nodes],
  );

  const normalizedServerUrl = String(serverUrl ?? '').trim();

  // Sorted node list for dropdowns
  const sortedNodes = useMemo(
    () => [...nodes].sort((a, b) => {
      const nameA = String(a.display_name ?? a.id);
      const nameB = String(b.display_name ?? b.id);
      return nameA.localeCompare(nameB);
    }),
    [nodes],
  );

  // Edge key sets split by direction for colour coding
  const forwardEdgeKeys = useMemo(() => {
    const keys = new Set<string>();
    activeCorridors.forEach((c) => {
      if (directedCorridorMap.get(c.corridor_id) !== 'reverse') {
        const ids = c.rsu_ids.filter((id) => availableNodeIds.has(id));
        for (let i = 0; i < ids.length - 1; i++) keys.add(buildUndirectedEdgeKey(ids[i], ids[i + 1]));
      }
    });
    return keys;
  }, [activeCorridors, directedCorridorMap, availableNodeIds]);

  const reverseEdgeKeys = useMemo(() => {
    const keys = new Set<string>();
    activeCorridors.forEach((c) => {
      if (directedCorridorMap.get(c.corridor_id) === 'reverse') {
        const ids = c.rsu_ids.filter((id) => availableNodeIds.has(id));
        for (let i = 0; i < ids.length - 1; i++) keys.add(buildUndirectedEdgeKey(ids[i], ids[i + 1]));
      }
    });
    return keys;
  }, [activeCorridors, directedCorridorMap, availableNodeIds]);

  // All nodes that sit on any active corridor path (for node highlight)
  const corridorPathNodeSet = useMemo(() => {
    const s = new Set<string>();
    activeCorridors.forEach((c) => c.rsu_ids.filter((id) => availableNodeIds.has(id)).forEach((id) => s.add(id)));
    return s;
  }, [activeCorridors, availableNodeIds]);

  // Prune direction map when corridors expire / are cleared externally
  useEffect(() => {
    const activeCids = new Set(activeCorridors.map((c) => c.corridor_id));
    setDirectedCorridorMap((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const cid of next.keys()) {
        if (!activeCids.has(cid)) { next.delete(cid); changed = true; }
      }
      return changed ? next : prev;
    });
  }, [activeCorridors]);

  // ── Corridor actions ───────────────────────────────────────────
  const canActivate = Boolean(dropdownSource && dropdownDest && dropdownSource !== dropdownDest && normalizedServerUrl);

  const handleCreateForward = useCallback(async () => {
    if (!canActivate) return;
    setIsApplyingCorridor(true);
    try {
      const result = await triggerGreenCorridor(normalizedServerUrl, {
        anchor_rsu_id: dropdownSource,
        source_rsu_id: dropdownSource,
        destination_rsu_id: dropdownDest,
        hold_seconds: holdSeconds,
        persistent: true,
        reason: 'manual_forward_corridor',
        created_by: 'hub_commander',
      });
      if (result.ok && result.corridor_id) {
        setDirectedCorridorMap((prev) => new Map(prev).set(result.corridor_id!, 'forward'));
        addSystemEvent(`Forward Corridor: ${dropdownSource} → ${dropdownDest}`, 'corridor', dropdownSource);
      } else {
        addSystemEvent(`Forward corridor failed: ${result.message}`, 'alert');
      }
    } finally {
      setIsApplyingCorridor(false);
    }
  }, [canActivate, dropdownSource, dropdownDest, holdSeconds, normalizedServerUrl, triggerGreenCorridor, addSystemEvent]);

  const handleCreateReverse = useCallback(async () => {
    if (!canActivate) return;
    // Use existing forward corridor's path as avoid_edges so reverse takes a different route
    const existingFwd = activeCorridors.find(
      (c) => c.source_rsu_id === dropdownSource && c.destination_rsu_id === dropdownDest,
    );
    const avoidEdges: [string, string][] = [];
    if (existingFwd) {
      for (let i = 0; i < existingFwd.rsu_ids.length - 1; i++) {
        avoidEdges.push([existingFwd.rsu_ids[i], existingFwd.rsu_ids[i + 1]]);
      }
    }
    setIsApplyingCorridor(true);
    try {
      const result = await triggerGreenCorridor(normalizedServerUrl, {
        anchor_rsu_id: dropdownDest,
        source_rsu_id: dropdownDest,
        destination_rsu_id: dropdownSource,
        hold_seconds: holdSeconds,
        persistent: true,
        reason: 'manual_reverse_corridor',
        created_by: 'hub_commander',
        ...(avoidEdges.length > 0 ? { avoid_edges: avoidEdges } : {}),
      });
      if (result.ok && result.corridor_id) {
        setDirectedCorridorMap((prev) => new Map(prev).set(result.corridor_id!, 'reverse'));
        addSystemEvent(`Reverse Corridor: ${dropdownDest} → ${dropdownSource}`, 'corridor', dropdownDest);
      } else {
        addSystemEvent(`Reverse corridor failed: ${result.message}`, 'alert');
      }
    } finally {
      setIsApplyingCorridor(false);
    }
  }, [canActivate, dropdownSource, dropdownDest, holdSeconds, normalizedServerUrl, triggerGreenCorridor, addSystemEvent, activeCorridors]);

  const handleRemoveCorridor = useCallback(async (corridorId: string) => {
    await clearGreenCorridors(normalizedServerUrl, { corridorId });
    setDirectedCorridorMap((prev) => { const n = new Map(prev); n.delete(corridorId); return n; });
  }, [normalizedServerUrl, clearGreenCorridors]);

  const handleClearAll = useCallback(async () => {
    await clearGreenCorridors(normalizedServerUrl);
    setDirectedCorridorMap(new Map());
    addSystemEvent('All corridors cleared.', 'system');
  }, [normalizedServerUrl, clearGreenCorridors, addSystemEvent]);

  const handleRsuNodeClick = (e: React.MouseEvent, nodeId: string) => {
    e.stopPropagation();
    onNodeClick?.(nodeId);
  };

  const getDisplayName = useCallback((nodeId: string) => {
    const node = nodes.find((n) => n.id === nodeId);
    return String(node?.display_name ?? nodeId).trim() || nodeId;
  }, [nodes]);

  return (
    <div className={styles.networkMainWrapper}>
      {/* ── Corridor Control Panel ─────────────────────────── */}
      <div className={styles.corridorControlPanel}>
        <div className={styles.corridorPanelTitle}>Green Corridor Control</div>

        <div className={styles.corridorControlsRow}>
          {/* Source dropdown */}
          <div className={styles.corridorInputGroup}>
            <span className={styles.corridorInputLabel}>Source</span>
            <select
              className={styles.corridorSelect}
              value={dropdownSource}
              onChange={(e) => setDropdownSource(e.target.value)}
            >
              <option value="">— select —</option>
              {sortedNodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {String(n.display_name ?? n.id)}
                </option>
              ))}
            </select>
          </div>

          {/* Destination dropdown */}
          <div className={styles.corridorInputGroup}>
            <span className={styles.corridorInputLabel}>Dest</span>
            <select
              className={styles.corridorSelect}
              value={dropdownDest}
              onChange={(e) => setDropdownDest(e.target.value)}
            >
              <option value="">— select —</option>
              {sortedNodes
                .filter((n) => n.id !== dropdownSource)
                .map((n) => (
                  <option key={n.id} value={n.id}>
                    {String(n.display_name ?? n.id)}
                  </option>
                ))}
            </select>
          </div>

          {/* Hold seconds */}
          <div className={styles.corridorInputGroup}>
            <span className={styles.corridorInputLabel}>Hold</span>
            <div className={styles.corridorHoldWrapper}>
              <input
                type="number"
                className={styles.corridorHoldInput}
                min={10}
                max={3600}
                value={holdSeconds}
                onChange={(e) => setHoldSeconds(Math.max(10, Math.min(3600, Number(e.target.value))))}
              />
              <span className={styles.corridorInputSuffix}>s</span>
            </div>
          </div>

          {/* Action buttons */}
          <div className={styles.corridorBtnGroup}>
            <button
              className={styles.corridorBtnForward}
              disabled={!canActivate || isApplyingCorridor}
              onClick={handleCreateForward}
              title={`Create green corridor: ${dropdownSource || '?'} → ${dropdownDest || '?'}`}
            >
              → Forward
            </button>
            <button
              className={styles.corridorBtnReverse}
              disabled={!canActivate || isApplyingCorridor}
              onClick={handleCreateReverse}
              title={`Create purple return corridor: ${dropdownDest || '?'} → ${dropdownSource || '?'}`}
            >
              ← Reverse
            </button>
          </div>
        </div>

        {/* Active corridors strip */}
        {activeCorridors.length > 0 && (
          <div className={styles.corridorActiveStrip}>
            <div className={styles.corridorActiveListHorizontal}>
              {activeCorridors.map((c) => {
                const dir = directedCorridorMap.get(c.corridor_id);
                const src = c.source_rsu_id || c.anchor_rsu_id;
                const dst = c.destination_rsu_id;
                const label = dst ? `${getDisplayName(src)} → ${getDisplayName(dst)}` : getDisplayName(src);
                return (
                  <div key={c.corridor_id} className={styles.corridorActiveChip}>
                    <div className={`${styles.corridorBadge} ${dir === 'reverse' ? styles.corridorBadgeReverse : dir === 'forward' ? styles.corridorBadgeForward : styles.corridorBadgeUnknown}`} />
                    <span className={styles.corridorActiveLabel} title={label}>{label}</span>
                    <button
                      className={styles.corridorRemoveBtn}
                      onClick={() => void handleRemoveCorridor(c.corridor_id)}
                      title="Remove this corridor"
                    >
                      ×
                    </button>
                  </div>
                );
              })}
            </div>
            <button
              className={styles.corridorClearAllBtnCompact}
              disabled={isApplyingCorridor}
              onClick={() => void handleClearAll()}
            >
              Clear All
            </button>
          </div>
        )}

        {/* {activeCorridors.length === 0 && (
          <div className={styles.corridorEmptyMsgRow}>No active corridors</div>
        )} */}
      </div>

      <div id="network-container" className={`${styles.glassPanel} ${styles.networkContainer}`} style={{ width: '100%', height: '100%', position: 'relative' }}>
        {/* 3D Perspective Glowing Grid Background */}
        <div className={styles.gridPerspective}>
          <div className={styles.gridPlane}></div>
        </div>


        {/* Edges */}
        {edges.map((edge, i) => {
          const fromPos = nodePositions[edge.from];
          const toPos = nodePositions[edge.to];
          if (!fromPos || !toPos) return null;

          const dx = toPos.x - fromPos.x;
          const dy = toPos.y - fromPos.y;
          const length = Math.sqrt(dx * dx + dy * dy);
          const angle = Math.atan2(dy, dx) * 180 / Math.PI;
          const edgeKey = buildUndirectedEdgeKey(edge.from, edge.to);
          const isFwd = forwardEdgeKeys.has(edgeKey);
          const isRev = reverseEdgeKeys.has(edgeKey);
          const hasCorridors = activeCorridors.length > 0;

          let edgeClassName: string;
          if (isFwd) {
            edgeClassName = `${styles.edge} ${styles.edgeGreenCorridor}`;
          } else if (isRev) {
            edgeClassName = `${styles.edge} ${styles.edgeReverseCorridor}`;
          } else if (hasCorridors) {
            edgeClassName = `${styles.edge} ${styles.edgeOutOfCorridor}`;
          } else {
            edgeClassName = styles.edge;
          }

          return (
            <div
              key={`edge-${i}`}
              className={edgeClassName}
              style={{
                left: fromPos.x + 20,
                top: fromPos.y + 20,
                width: length,
                height: 2,
                transform: `rotate(${angle}deg)`,
              }}
            >
              <div className={styles.edgePulse} />
            </div>
          );
        })}

        {/* Nodes */}
        {nodes.map((node) => {
          const pos = nodePositions[node.id];
          if (!pos) return null;

          const isCongested = congestionState[node.id]?.isCongested;
          const isGreenCorridor = Boolean(greenCorridorByRsu[node.id]);
          const isSelected = selectedNodeId === node.id;
          const isOnCorridorPath = corridorPathNodeSet.has(node.id);
          const nodeDisplayName = String(node.display_name ?? '').trim() || `RSU ${node.id}`;
          const nodeShortLabel = nodeDisplayName.slice(0, 3).toUpperCase();

          const nodeStatusTitle = isOnCorridorPath
            ? 'IN CORRIDOR PATH'
            : (isGreenCorridor ? 'GREEN CORRIDOR' : (isCongested ? 'CONGESTED' : 'CLEAR'));

          return (
            <motion.div
              key={node.id}
              className={`${styles.node} ${isCongested ? styles.congested : ''} ${isGreenCorridor ? styles.greenCorridor : ''} ${isSelected ? styles.selectedNode : ''} ${isOnCorridorPath ? styles.corridorPathNode : ''}`}
              style={{ left: pos.x, top: pos.y }}
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: 'spring', stiffness: 200, damping: 20 }}
              whileHover={{ scale: 1.1, zIndex: 20 }}
              onClick={(e) => handleRsuNodeClick(e, node.id)}
              title={`${nodeDisplayName} - ${nodeStatusTitle}`}
            >
              {isCongested && <div className={`${styles.nodePulse} animate-pulse-ring`} />}
              <span className={styles.nodeLabel}>{nodeShortLabel}</span>
              <span className={styles.nodeName}>{nodeDisplayName}</span>
            </motion.div>
          );
        })}

        {nodes.length === 0 && (
          <div style={{ color: 'var(--text-secondary)', zIndex: 10 }}>Waiting for RSU Graph from Server...</div>
        )}
      </div>
    </div>
  );
}
