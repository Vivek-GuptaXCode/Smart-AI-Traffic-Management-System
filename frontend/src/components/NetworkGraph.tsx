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
  const [corridorStartNodeId, setCorridorStartNodeId] = useState<string | null>(null);
  const [corridorEndNodeIds, setCorridorEndNodeIds] = useState<string[]>([]);
  const [isApplyingCorridor, setIsApplyingCorridor] = useState(false);

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

  const activeCorridorStartNodeId = (
    corridorStartNodeId && availableNodeIds.has(corridorStartNodeId)
      ? corridorStartNodeId
      : null
  );

  const activeCorridorEndNodeIds = useMemo(
    () => corridorEndNodeIds.filter((id) => availableNodeIds.has(id)),
    [corridorEndNodeIds, availableNodeIds],
  );

  // When no local selection, derive highlighted paths from ALL server corridors
  const serverCorridorRsuIds = useMemo(() => {
    if (activeCorridors.length === 0) return [];
    const allIds = new Set<string>();
    activeCorridors.forEach((c) => {
      c.rsu_ids.filter((id) => availableNodeIds.has(id)).forEach((id) => allIds.add(id));
    });
    return Array.from(allIds);
  }, [activeCorridors, availableNodeIds]);

  // Compute edge keys for ALL server corridors (preserving path order)
  const serverCorridorEdgeKeys = useMemo(() => {
    const keys = new Set<string>();
    activeCorridors.forEach((c) => {
      const ids = c.rsu_ids.filter((id) => availableNodeIds.has(id));
      for (let i = 0; i < ids.length - 1; i++) {
        keys.add(buildUndirectedEdgeKey(ids[i], ids[i + 1]));
      }
    });
    return keys;
  }, [activeCorridors, availableNodeIds]);

  // Compute Dijkstra paths for ALL local corridor pairs (forward only — reverse uses same edges)
  const allLocalCorridorPaths = useMemo(() => {
    if (!activeCorridorStartNodeId || activeCorridorEndNodeIds.length === 0) return [];
    const paths: string[][] = [];
    for (const endId of activeCorridorEndNodeIds) {
      const path = findShortestPathDijkstra(activeCorridorStartNodeId, endId, edges, nodePositions);
      if (path.length > 1) paths.push(path);
    }
    return paths;
  }, [activeCorridorStartNodeId, activeCorridorEndNodeIds, edges, nodePositions]);

  // Union of all corridor node IDs (local paths + server corridors)
  const corridorPathNodeIds = useMemo(() => {
    const allIds = new Set<string>();
    // Local paths
    allLocalCorridorPaths.forEach((path) => path.forEach((id) => allIds.add(id)));
    // Server corridors (when no local selection)
    serverCorridorRsuIds.forEach((id) => allIds.add(id));
    return Array.from(allIds);
  }, [allLocalCorridorPaths, serverCorridorRsuIds]);

  const corridorPathNodeSet = useMemo(
    () => new Set(corridorPathNodeIds),
    [corridorPathNodeIds],
  );

  // Union of all corridor edge keys (local + server)
  const corridorPathEdgeKeys = useMemo(() => {
    const nextKeys = new Set<string>();
    // Local paths
    allLocalCorridorPaths.forEach((path) => {
      for (let i = 0; i < path.length - 1; i++) {
        nextKeys.add(buildUndirectedEdgeKey(path[i], path[i + 1]));
      }
    });
    // Server corridors
    serverCorridorEdgeKeys.forEach((key) => nextKeys.add(key));
    return nextKeys;
  }, [allLocalCorridorPaths, serverCorridorEdgeKeys]);

  const hasCorridorSelection = Boolean(corridorStartNodeId && corridorEndNodeIds.length > 0);
  const hasValidCorridorPath = corridorPathNodeIds.length > 1;
  const totalCorridorHops = allLocalCorridorPaths.reduce(
    (sum, path) => sum + Math.max(0, path.length - 1), 0,
  );
  const normalizedServerUrl = String(serverUrl ?? '').trim();

  // Activate a corridor pair: forward + reverse (reverse takes a different route)
  const activateBidirectionalCorridor = useCallback(async (sourceNodeId: string, destinationNodeId: string) => {
    if (!normalizedServerUrl) {
      addSystemEvent('Server URL unavailable.', 'system');
      return;
    }

    setIsApplyingCorridor(true);
    try {
      // Forward corridor: source → destination
      const fwdResult = await triggerGreenCorridor(normalizedServerUrl, {
        anchor_rsu_id: sourceNodeId,
        source_rsu_id: sourceNodeId,
        destination_rsu_id: destinationNodeId,
        hold_seconds: 120,
        persistent: true,
        reason: 'manual_tactical_corridor',
        created_by: 'hub_commander',
      });

      if (fwdResult.ok) {
        addSystemEvent(`Corridor Active: ${sourceNodeId} → ${destinationNodeId}`, 'corridor', sourceNodeId);
      } else {
        addSystemEvent(`Corridor failed: ${fwdResult.message}`, 'alert');
      }

      // Build avoid_edges from the forward path so the reverse takes a different route
      const fwdRsuIds = fwdResult.rsu_ids ?? [];
      const avoidEdges: [string, string][] = [];
      for (let i = 0; i < fwdRsuIds.length - 1; i++) {
        avoidEdges.push([fwdRsuIds[i], fwdRsuIds[i + 1]]);
      }

      // Reverse corridor: destination → source (avoiding forward path edges)
      const revResult = await triggerGreenCorridor(normalizedServerUrl, {
        anchor_rsu_id: destinationNodeId,
        source_rsu_id: destinationNodeId,
        destination_rsu_id: sourceNodeId,
        hold_seconds: 120,
        persistent: true,
        reason: 'manual_tactical_corridor_reverse',
        created_by: 'hub_commander',
        ...(avoidEdges.length > 0 ? { avoid_edges: avoidEdges } : {}),
      });

      if (revResult.ok) {
        addSystemEvent(`Reverse Corridor Active: ${destinationNodeId} → ${sourceNodeId}`, 'corridor', destinationNodeId);
      } else {
        addSystemEvent(`Reverse corridor failed: ${revResult.message}`, 'alert');
      }
    } finally {
      setIsApplyingCorridor(false);
    }
  }, [normalizedServerUrl, triggerGreenCorridor, addSystemEvent]);

  const handleRsuNodeClick = (e: React.MouseEvent, nodeId: string) => {
    e.stopPropagation();
    onNodeClick?.(nodeId);

    // Non-shift click when source is already set: just update spotlight, no corridor logic
    if (!e.shiftKey && corridorStartNodeId) {
      return;
    }

    // First click (no source set): set source and clear all existing corridors
    if (!corridorStartNodeId) {
      setCorridorStartNodeId(nodeId);
      setCorridorEndNodeIds([]);
      if (normalizedServerUrl) {
        void clearGreenCorridors(normalizedServerUrl);
      }
      addSystemEvent(`Source Selected: ${nodeId}. Shift+Click to add destinations.`, 'system', nodeId);
      return;
    }

    // Click on source node again: deselect all
    if (nodeId === corridorStartNodeId) {
      setCorridorStartNodeId(null);
      setCorridorEndNodeIds([]);
      addSystemEvent(`Source Deselected: ${nodeId}`, 'system');
      return;
    }

    // Shift+Click on existing destination: toggle it off
    if (corridorEndNodeIds.includes(nodeId)) {
      setCorridorEndNodeIds((prev) => prev.filter((id) => id !== nodeId));
      addSystemEvent(`Destination Removed: ${nodeId}`, 'system');
      return;
    }

    // Shift+Click on new node: add as destination and create bidirectional corridors
    setCorridorEndNodeIds((prev) => [...prev, nodeId]);
    void activateBidirectionalCorridor(corridorStartNodeId, nodeId);
  };

  const resetCorridorSelection = () => {
    setCorridorStartNodeId(null);
    setCorridorEndNodeIds([]);
    addSystemEvent('All corridors cleared.', 'system');
    if (normalizedServerUrl) {
      void clearGreenCorridors(normalizedServerUrl);
    }
  };

  const displayNodes = nodes;

  // Build hint panel text
  const hintPanelText = useMemo(() => {
    // Server corridors (no local selection active)
    if (serverCorridorRsuIds.length > 0 && !activeCorridorStartNodeId) {
      const total = activeCorridors.length;
      const pairs = activeCorridors.map((c) => {
        const src = c.source_rsu_id || c.anchor_rsu_id;
        const dst = c.destination_rsu_id;
        return dst ? `${src} → ${dst}` : src;
      });
      const uniquePairs = [...new Set(pairs)];
      if (uniquePairs.length <= 2) {
        return `${total} Corridor${total > 1 ? 's' : ''}: ${uniquePairs.join(', ')}`;
      }
      return `${total} Corridors Active (${uniquePairs.length} routes)`;
    }

    // Local selection: no destinations yet
    if (activeCorridorStartNodeId && activeCorridorEndNodeIds.length === 0) {
      return `Source: ${activeCorridorStartNodeId}. Shift+Click to add destinations.`;
    }

    // Local selection: has destinations
    if (activeCorridorStartNodeId && activeCorridorEndNodeIds.length > 0) {
      const destList = activeCorridorEndNodeIds.join(', ');
      return `${activeCorridorEndNodeIds.length * 2} Corridors: ${activeCorridorStartNodeId} ↔ ${destList} (${totalCorridorHops} hops, fwd + alt return)`;
    }

    return '';
  }, [serverCorridorRsuIds, activeCorridorStartNodeId, activeCorridorEndNodeIds, activeCorridors, totalCorridorHops]);

  const showHintPanel = Boolean(
    activeCorridorStartNodeId || hasCorridorSelection || serverCorridorRsuIds.length > 0,
  );

  return (
    <div id="network-container" className={`${styles.glassPanel} ${styles.networkContainer}`} style={{ width: '100%', height: '100%', position: 'relative' }}>

      {/* 3D Perspective Glowing Grid Background */}
      <div className={styles.gridPerspective}>
        <div className={styles.gridPlane}></div>
      </div>

      {/* Corridor Hint Panel — supports multi-destination corridors */}
      {showHintPanel && (
        <div className={styles.corridorHintPanel}>
          <span className={styles.corridorHintText}>
            {hintPanelText}
          </span>
          <button
            className={styles.corridorResetButton}
            onClick={resetCorridorSelection}
            disabled={isApplyingCorridor}
          >
            Clear All
          </button>
        </div>
      )}

      {/* Edges */}
      {edges.map((edge, i) => {
        const fromPos = nodePositions[edge.from];
        const toPos = nodePositions[edge.to];

        if (!fromPos || !toPos) return null;

        const dx = toPos.x - fromPos.x;
        const dy = toPos.y - fromPos.y;
        const length = Math.sqrt(dx * dx + dy * dy);
        const angle = Math.atan2(dy, dx) * 180 / Math.PI;
        const isCorridorEdge = corridorPathEdgeKeys.has(buildUndirectedEdgeKey(edge.from, edge.to));
        const showCorridorHighlight = hasCorridorSelection || serverCorridorRsuIds.length > 0;
        const edgeClassName = showCorridorHighlight
          ? `${styles.edge} ${isCorridorEdge ? styles.edgeGreenCorridor : styles.edgeOutOfCorridor}`
          : styles.edge;

        return (
          <div
            key={`edge-${i}`}
            className={edgeClassName}
            style={{
              left: fromPos.x + 20, // center alignment (+ half node width)
              top: fromPos.y + 20,
              width: length,
              height: 2,
              transform: `rotate(${angle}deg)`
            }}
          >
            <div className={styles.edgePulse} />
          </div>
        );
      })}

      {/* Nodes */}
      {displayNodes.map((node) => {
        const pos = nodePositions[node.id];
        if (!pos) return null;

        const isCongested = congestionState[node.id]?.isCongested;
        const isGreenCorridor = Boolean(greenCorridorByRsu[node.id]);
        const isSelected = selectedNodeId === node.id;
        const isCorridorStart = activeCorridorStartNodeId === node.id;
        const isCorridorEnd = activeCorridorEndNodeIds.includes(node.id);
        const isOnCorridorPath = corridorPathNodeSet.has(node.id);
        const nodeDisplayName = String(node.display_name ?? '').trim() || `RSU ${node.id}`;
        const nodeShortLabel = nodeDisplayName.slice(0, 3).toUpperCase();

        const nodeStatusTitle = isCorridorStart
          ? 'CORRIDOR SOURCE'
          : (isCorridorEnd
            ? 'CORRIDOR DESTINATION'
            : (isOnCorridorPath
              ? 'IN CORRIDOR PATH'
              : (isGreenCorridor
                ? 'GREEN CORRIDOR'
                : (isCongested ? 'CONGESTED' : 'CLEAR'))));

        return (
          <motion.div
            key={node.id}
            className={`${styles.node} ${isCongested ? styles.congested : ''} ${isGreenCorridor ? styles.greenCorridor : ''} ${isSelected ? styles.selectedNode : ''} ${isOnCorridorPath ? styles.corridorPathNode : ''} ${isCorridorStart ? styles.corridorStartNode : ''} ${isCorridorEnd ? styles.corridorEndNode : ''}`}
            style={{ left: pos.x, top: pos.y }}
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 200, damping: 20 }}
            whileHover={{ scale: 1.1, zIndex: 20 }}
            onClick={(e) => handleRsuNodeClick(e, node.id)}
          >
            {isCongested && (
              <div className={`${styles.nodePulse} animate-pulse-ring`} />
            )}
            <span className={styles.nodeLabel}>
              {nodeShortLabel}
            </span>
            <span className={styles.nodeName}>
              {nodeDisplayName}
            </span>

            <div
              title={`${nodeDisplayName} - ${nodeStatusTitle}`}
              style={{ position: 'absolute', width: '100%', height: '100%' }}
            />
          </motion.div>
        );
      })}

      {displayNodes.length === 0 && (
        <div style={{ color: 'var(--text-secondary)', zIndex: 10 }}>Waiting for RSU Graph from Server...</div>
      )}
    </div>
  );
}
