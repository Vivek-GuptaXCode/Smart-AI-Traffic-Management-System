import { create } from 'zustand';
import { io, Socket } from 'socket.io-client';

export interface GraphNode {
  id: string;
  x?: number;
  y?: number;
  display_name?: string;
}

export interface GraphEdge {
  from: string;
  to: string;
}

export interface CongestionData {
  isCongested: boolean;
  vehicle_count: number;
  avg_wait: number;
  lastUpdated: string;
}

export interface SystemEvent {
  id: string;
  type: string;
  rsu_id: string;
  message: string;
  timestamp: string;
}

export interface GreenCorridorControlPayload {
  anchor_rsu_id: string;
  source_rsu_id?: string;
  destination_rsu_id?: string;
  radius_hops?: number;
  hold_seconds?: number;
  persistent?: boolean;
  emergency_vehicle_ids?: string[];
  reason?: string;
  created_by?: string;
  avoid_edges?: [string, string][];
}

export interface GreenCorridorPlan {
  corridor_id: string;
  anchor_rsu_id: string;
  source_rsu_id: string;
  destination_rsu_id: string;
  rsu_ids: string[];
  radius_hops: number;
  hold_seconds: number;
  created_at: string;
  expires_at: string;
  remaining_seconds: number;
  reason: string;
  created_by: string;
  strategy: string;
  emergency_vehicle_ids: string[];
}

export type TrafficChatRole = 'assistant' | 'user' | 'system';

export interface TrafficChatMessage {
  id: string;
  role: TrafficChatRole;
  content: string;
  timestamp: string;
  intent?: string;
  confidence?: number;
}

interface GreenCorridorNodeState {
  corridorId: string;
  expiresAt: string;
  lastUpdated: string;
}

interface ClearGreenCorridorOptions {
  corridorId?: string;
  anchorRsuId?: string;
}

interface ActionResult {
  ok: boolean;
  message: string;
  rsu_ids?: string[];
  corridor_id?: string;
}

const GRAPH_FETCH_LOG_INTERVAL_MS = 15000;
let lastGraphFetchLogAt = 0;

const parseJsonResponse = async (
  response: Response,
  endpointName: string,
): Promise<Record<string, unknown>> => {
  const contentType = String(response.headers.get('content-type') ?? '').toLowerCase();
  const rawBody = await response.text();
  const trimmedBody = rawBody.trim();

  if (!trimmedBody) {
    return {};
  }

  // Guard against HTML/error pages returned by wrong host/route proxies.
  if (!contentType.includes('application/json')) {
    const preview = trimmedBody.slice(0, 120).replace(/\s+/g, ' ');
    throw new Error(
      `${endpointName} returned non-JSON response (content-type=${contentType || 'unknown'}, status=${response.status}). Preview: ${preview}`,
    );
  }

  try {
    const parsed = JSON.parse(trimmedBody);
    if (parsed && typeof parsed === 'object') {
      return parsed as Record<string, unknown>;
    }
    return {};
  } catch (error) {
    throw new Error(
      `${endpointName} returned invalid JSON (status=${response.status}): ${String(error)}`,
    );
  }
};

const normalizeIsoTimestamp = (rawValue: unknown): string => {
  if (typeof rawValue === 'string' && rawValue.trim()) {
    const trimmed = rawValue.trim();
    // If it looks like HH:MM:SS (old server format), prepend today's date
    if (/^\d{2}:\d{2}:\d{2}/.test(trimmed)) {
      return `${new Date().toISOString().split('T')[0]}T${trimmed}`;
    }
    return trimmed;
  }
  return new Date().toISOString();
};

const normalizeGraphNode = (rawValue: unknown): GraphNode | null => {
  if (rawValue && typeof rawValue === 'object') {
    const row = rawValue as Record<string, unknown>;
    const id = String(row.id ?? '').trim();
    if (!id) {
      return null;
    }
    return {
      id,
      x: typeof row.x === 'number' ? row.x : undefined,
      y: typeof row.y === 'number' ? row.y : undefined,
      display_name: typeof row.display_name === 'string' ? row.display_name : undefined,
    };
  }

  const id = String(rawValue ?? '').trim();
  if (!id) {
    return null;
  }
  return { id };
};

const normalizeGreenCorridorPlan = (rawValue: unknown): GreenCorridorPlan | null => {
  if (!rawValue || typeof rawValue !== 'object') {
    return null;
  }

  const row = rawValue as Record<string, unknown>;
  const corridorId = String(row.corridor_id ?? '').trim();
  const anchorRsuId = String(row.anchor_rsu_id ?? '').trim();
  if (!corridorId || !anchorRsuId) {
    return null;
  }

  const rsuIdsRaw = Array.isArray(row.rsu_ids) ? row.rsu_ids : [];
  const emergencyIdsRaw = Array.isArray(row.emergency_vehicle_ids) ? row.emergency_vehicle_ids : [];

  return {
    corridor_id: corridorId,
    anchor_rsu_id: anchorRsuId,
    source_rsu_id: String(row.source_rsu_id ?? ''),
    destination_rsu_id: String(row.destination_rsu_id ?? ''),
    rsu_ids: rsuIdsRaw.map((id) => String(id)).filter(Boolean),
    radius_hops: Number(row.radius_hops ?? 1),
    hold_seconds: Number(row.hold_seconds ?? 0),
    created_at: normalizeIsoTimestamp(row.created_at),
    expires_at: normalizeIsoTimestamp(row.expires_at),
    remaining_seconds: Number(row.remaining_seconds ?? 0),
    reason: String(row.reason ?? 'manual_dashboard_trigger'),
    created_by: String(row.created_by ?? 'dashboard'),
    strategy: String(row.strategy ?? 'rsu_hop_expansion_v1'),
    emergency_vehicle_ids: emergencyIdsRaw.map((id) => String(id)).filter(Boolean),
  };
};

const normalizeGreenCorridorPlanList = (rawValue: unknown): GreenCorridorPlan[] => {
  if (!Array.isArray(rawValue)) {
    return [];
  }
  const plans: GreenCorridorPlan[] = [];
  rawValue.forEach((row) => {
    const plan = normalizeGreenCorridorPlan(row);
    if (plan) {
      plans.push(plan);
    }
  });
  return plans;
};

const buildGreenCorridorIndex = (
  plans: GreenCorridorPlan[],
  nowIso: string,
): Record<string, GreenCorridorNodeState> => {
  const index: Record<string, GreenCorridorNodeState> = {};
  plans.forEach((plan) => {
    plan.rsu_ids.forEach((rsuId) => {
      const key = String(rsuId).trim();
      if (!key) {
        return;
      }
      index[key] = {
        corridorId: plan.corridor_id,
        expiresAt: plan.expires_at,
        lastUpdated: nowIso,
      };
    });
  });
  return index;
};

interface TrafficState {
  socket: Socket | null;
  isConnected: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
  congestionState: Record<string, CongestionData>;
  greenCorridorByRsu: Record<string, GreenCorridorNodeState>;
  activeCorridors: GreenCorridorPlan[];
  eventsLog: SystemEvent[];
  chatMessages: TrafficChatMessage[];
  throughput: number;
  avgSpeed: number;
  selectedNodeId: string | null;

  connect: (url: string) => void;
  disconnect: () => void;
  setSelectedNodeId: (id: string | null) => void;
  fetchGraph: (url: string) => Promise<void>;
  fetchGreenCorridors: (url: string) => Promise<void>;
  appendChatMessage: (message: TrafficChatMessage) => void;
  setChatMessages: (messages: TrafficChatMessage[]) => void;
  clearChatMessages: () => void;
  addSystemEvent: (message: string, type?: string, rsuId?: string) => void;
  activeView: string;
  setActiveView: (view: string) => void;
  sidebarOpen: boolean;
  setSidebarOpen: (isOpen: boolean) => void;
  triggerGreenCorridor: (url: string, payload: GreenCorridorControlPayload) => Promise<ActionResult>;
  clearGreenCorridors: (url: string, options?: ClearGreenCorridorOptions) => Promise<ActionResult>;
  simulateKPIs: () => void;
}

export const useTrafficStore = create<TrafficState>((set, get) => ({
  socket: null,
  isConnected: false,
  nodes: [],
  edges: [],
  congestionState: {},
  greenCorridorByRsu: {},
  activeCorridors: [],
  eventsLog: [],
  chatMessages: [],
  throughput: 3200,
  avgSpeed: 24.5,
  selectedNodeId: null,
  sidebarOpen: true,
  setSidebarOpen: (isOpen) => set({ sidebarOpen: isOpen }),

  connect: (url) => {
    if (get().socket) return;

    const socket = io(url, { transports: ['websocket', 'polling'] });

    socket.on('connect', () => {
      set({ isConnected: true, socket });

      const newEvent = {
        id: Date.now().toString(),
        type: 'system',
        rsu_id: 'SYSTEM',
        message: 'Connected to V2X Server.',
        timestamp: new Date().toISOString(),
      };
      set((state) => ({ eventsLog: [newEvent, ...state.eventsLog].slice(0, 50) }));
    });

    socket.on('disconnect', () => {
      set({ isConnected: false });
    });

    socket.on('junction_broadcast', (data: unknown) => {
      const payload = (data && typeof data === 'object') ? (data as Record<string, unknown>) : {};
      const fromRsu = String(payload.from_rsu ?? '?');
      const vehicleCount = Number(payload.vehicle_count ?? 0);
      const avgWait = Number(payload.avg_wait ?? 0);
      const timestamp = normalizeIsoTimestamp(payload.timestamp);

      set((state) => {
        const newCongestionState = { ...state.congestionState };
        newCongestionState[fromRsu] = {
          isCongested: true,
          vehicle_count: Number.isFinite(vehicleCount) ? vehicleCount : 0,
          avg_wait: Number.isFinite(avgWait) ? avgWait : 0,
          lastUpdated: timestamp,
        };

        const newEvent = {
          id: Date.now().toString() + Math.random().toString(),
          type: 'alert',
          rsu_id: fromRsu,
          message: `High congestion reported (${vehicleCount} veh, ${avgWait} frames).`,
          timestamp,
        };

        // Slight reduction in avgSpeed to simulate impact
        const newSpeed = Math.max(10, state.avgSpeed - 0.2);

        return {
          congestionState: newCongestionState,
          eventsLog: [newEvent, ...state.eventsLog].slice(0, 50),
          avgSpeed: newSpeed
        };
      });
    });

    socket.on('junction_clear_broadcast', (data: unknown) => {
      const payload = (data && typeof data === 'object') ? (data as Record<string, unknown>) : {};
      const fromRsu = String(payload.from_rsu ?? '?');
      const timestamp = normalizeIsoTimestamp(payload.timestamp);

      set((state) => {
        const newCongestionState = { ...state.congestionState };
        if (newCongestionState[fromRsu]) {
          newCongestionState[fromRsu].isCongested = false;
          newCongestionState[fromRsu].lastUpdated = timestamp;
        }

        const newEvent = {
          id: Date.now().toString() + Math.random().toString(),
          type: 'clear',
          rsu_id: fromRsu,
          message: `Traffic cleared at junction.`,
          timestamp,
        };

        // Slight recovery in avgSpeed to simulate impact
        const newSpeed = Math.min(45, state.avgSpeed + 0.1);

        return {
          congestionState: newCongestionState,
          eventsLog: [newEvent, ...state.eventsLog].slice(0, 50),
          avgSpeed: newSpeed
        };
      });
    });

    socket.on('green_corridor_broadcast', (data: unknown) => {
      const payload = (data && typeof data === 'object') ? (data as Record<string, unknown>) : {};
      const action = String(payload.action ?? 'activated').toLowerCase();
      const timestamp = normalizeIsoTimestamp(payload.timestamp);

      set((state) => {
        let nextActiveCorridors = state.activeCorridors;
        let rsuForEvent = 'SYSTEM';
        let message = 'Green corridor state updated.';

        if (action === 'activated') {
          const nextPlan = normalizeGreenCorridorPlan(payload.corridor);
          if (nextPlan) {
            nextActiveCorridors = [
              nextPlan,
              ...state.activeCorridors.filter((plan) => plan.corridor_id !== nextPlan.corridor_id),
            ];
            rsuForEvent = nextPlan.anchor_rsu_id;
            const _src = nextPlan.source_rsu_id || nextPlan.anchor_rsu_id;
            const _dst = nextPlan.destination_rsu_id;
            const _hops = Math.max(0, nextPlan.rsu_ids.length - 1);
            message = `Green corridor ACTIVE: ${_src}${_dst ? ` → ${_dst}` : ''} (${nextPlan.rsu_ids.length} RSUs, ${_hops} hop${_hops !== 1 ? 's' : ''}).`;
          }
        } else if (action === 'cleared') {
          const clearedPlans = normalizeGreenCorridorPlanList(payload.corridors);
          const clearedIds = new Set(clearedPlans.map((plan) => plan.corridor_id));
          if (clearedIds.size > 0) {
            nextActiveCorridors = state.activeCorridors.filter((plan) => !clearedIds.has(plan.corridor_id));
            if (clearedPlans.length === 1) {
              const _cp = clearedPlans[0];
              const _csrc = _cp.source_rsu_id || _cp.anchor_rsu_id;
              const _cdst = _cp.destination_rsu_id;
              message = `Green corridor CLEARED: ${_csrc}${_cdst ? ` → ${_cdst}` : ''}.`;
            } else {
              message = `Green corridors CLEARED: ${clearedIds.size} plans removed.`;
            }
          } else if (Array.isArray(payload.corridors) && (payload.corridors as unknown[]).length === 0) {
            // Server explicitly sent an empty list → all cleared
            nextActiveCorridors = [];
            message = 'All green corridors CLEARED.';
          }
          // else: normalization returned empty due to bad data — do not wipe existing state
        }

        const nowIso = new Date().toISOString();
        const nextIndex = buildGreenCorridorIndex(nextActiveCorridors, nowIso);
        const event: SystemEvent = {
          id: `${Date.now()}_${Math.random().toString(36).slice(2)}`,
          type: 'corridor',
          rsu_id: rsuForEvent,
          message,
          timestamp,
        };

        return {
          activeCorridors: nextActiveCorridors,
          greenCorridorByRsu: nextIndex,
          eventsLog: [event, ...state.eventsLog].slice(0, 50),
        };
      });
    });

    set({ socket });
  },

  addSystemEvent: (message: string, type: string = 'system', rsuId: string = 'SYSTEM') => {
    const { eventsLog } = get();
    const newEvent: SystemEvent = {
      id: `sys-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`,
      type,
      rsu_id: rsuId,
      message,
      timestamp: new Date().toISOString(),
    };

    // Keep only the most recent 50 events
    set({
      eventsLog: [newEvent, ...eventsLog].slice(0, 50)
    });
  },

  appendChatMessage: (message: TrafficChatMessage) => {
    set((state) => ({
      chatMessages: [...state.chatMessages, message].slice(-150),
    }));
  },

  setChatMessages: (messages: TrafficChatMessage[]) => {
    set({
      chatMessages: messages.slice(-150),
    });
  },

  clearChatMessages: () => {
    set({ chatMessages: [] });
  },

  activeView: 'Global Topology',
  setActiveView: (view: string) => set({ activeView: view }),

  setSelectedNodeId: (id) => {
    set({ selectedNodeId: id ? String(id).trim() : null });
  },

  disconnect: () => {
    const socket = get().socket;
    if (socket) {
      socket.disconnect();
      set({ socket: null, isConnected: false });
    }
  },

  fetchGraph: async (url) => {
    const normalizedUrl = String(url ?? '').trim();
    if (!normalizedUrl) {
      return;
    }

    const candidateBases = [normalizedUrl];
    if (normalizedUrl.includes('localhost')) {
      candidateBases.push(normalizedUrl.replace('localhost', '127.0.0.1'));
    } else if (normalizedUrl.includes('127.0.0.1')) {
      candidateBases.push(normalizedUrl.replace('127.0.0.1', 'localhost'));
    }

    let lastError: unknown = null;

    for (const candidateBase of candidateBases) {
      try {
        const response = await fetch(`${candidateBase}/graph`, { cache: 'no-store' });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const data = await parseJsonResponse(response, '/graph');

        if (data && Array.isArray(data.nodes) && Array.isArray(data.edges)) {
          const normalizedNodes = (data.nodes as unknown[])
            .map(normalizeGraphNode)
            .filter((node): node is GraphNode => node !== null);

          const uniqueNodes = Array.from(
            new Map(normalizedNodes.map((node) => [node.id, node])).values(),
          );

          let refreshedCongestionState = get().congestionState;
          try {
            const statusResponse = await fetch(`${candidateBase}/status`, { cache: 'no-store' });
            if (statusResponse.ok) {
              const statusPayload = await statusResponse.json();
              if (Array.isArray(statusPayload)) {
                const nextState: Record<string, CongestionData> = {};
                statusPayload.forEach((entry) => {
                  if (!entry || typeof entry !== 'object') {
                    return;
                  }
                  const row = entry as Record<string, unknown>;
                  const eventType = String(row.type ?? '').toLowerCase();
                  const rsuId = String(row.from_rsu ?? '').trim();
                  if (!rsuId || (eventType !== 'congestion' && eventType !== 'clear')) {
                    return;
                  }

                  if (eventType === 'congestion') {
                    const rawVehicleCount = Number(row.vehicle_count ?? 0);
                    const rawAvgWait = Number(row.avg_wait ?? 0);
                    nextState[rsuId] = {
                      isCongested: true,
                      vehicle_count: Number.isFinite(rawVehicleCount) ? rawVehicleCount : 0,
                      avg_wait: Number.isFinite(rawAvgWait) ? rawAvgWait : 0,
                      lastUpdated: normalizeIsoTimestamp(row.timestamp),
                    };
                    return;
                  }

                  if (eventType === 'clear') {
                    const previous = nextState[rsuId];
                    nextState[rsuId] = {
                      isCongested: false,
                      vehicle_count: previous?.vehicle_count ?? 0,
                      avg_wait: previous?.avg_wait ?? 0,
                      lastUpdated: normalizeIsoTimestamp(row.timestamp),
                    };
                  }
                });

                refreshedCongestionState = nextState;
              }
            }
          } catch {
            // Keep previous in-memory congestion state if /status is unavailable.
          }

          set({
            nodes: uniqueNodes,
            edges: data.edges as GraphEdge[],
            congestionState: refreshedCongestionState,
          });
          return;
        }
      } catch (error) {
        lastError = error;
      }
    }

    const now = Date.now();
    if (now - lastGraphFetchLogAt >= GRAPH_FETCH_LOG_INTERVAL_MS) {
      lastGraphFetchLogAt = now;
      console.warn('RSU graph fetch skipped: backend is temporarily unavailable.', lastError);
    }
  },

  fetchGreenCorridors: async (url) => {
    const normalizedUrl = String(url ?? '').trim();
    if (!normalizedUrl) {
      return;
    }

    try {
      const response = await fetch(`${normalizedUrl}/signals/green-corridor`);
      const data = await parseJsonResponse(response, '/signals/green-corridor GET');
      const activeCorridors = normalizeGreenCorridorPlanList(data?.active_corridors);
      const nowIso = new Date().toISOString();
      set({
        activeCorridors,
        greenCorridorByRsu: buildGreenCorridorIndex(activeCorridors, nowIso),
      });
    } catch (error) {
      console.error('Failed to fetch green corridors:', error);
    }
  },

  triggerGreenCorridor: async (url, payload) => {
    const normalizedUrl = String(url ?? '').trim();
    if (!normalizedUrl) {
      return { ok: false, message: 'Server URL is missing.' };
    }

    const anchorRsuId = String(payload.anchor_rsu_id ?? '').trim();
    if (!anchorRsuId) {
      return { ok: false, message: 'Anchor RSU is required.' };
    }

    try {
      const response = await fetch(`${normalizedUrl}/signals/green-corridor`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          action: 'activate',
          anchor_rsu_id: anchorRsuId,
          source_rsu_id: payload.source_rsu_id ?? undefined,
          destination_rsu_id: payload.destination_rsu_id ?? undefined,
          radius_hops: payload.radius_hops ?? 1,
          hold_seconds: payload.hold_seconds ?? 30,
          persistent: payload.persistent ?? false,
          emergency_vehicle_ids: payload.emergency_vehicle_ids ?? [],
          reason: payload.reason ?? 'manual_dashboard_trigger',
          created_by: payload.created_by ?? 'frontend_dashboard',
          ...(payload.avoid_edges ? { avoid_edges: payload.avoid_edges } : {}),
        }),
      });

      const data = await parseJsonResponse(response, '/signals/green-corridor POST activate');
      if (!response.ok || data?.status !== 'ok') {
        return {
          ok: false,
          message: String(data?.message ?? 'Failed to activate green corridor.'),
        };
      }

      const corridor = normalizeGreenCorridorPlan(data?.corridor);
      if (corridor) {
        const nextActiveCorridors = [
          corridor,
          ...get().activeCorridors.filter((plan) => plan.corridor_id !== corridor.corridor_id),
        ];
        const nowIso = new Date().toISOString();
        set({
          activeCorridors: nextActiveCorridors,
          greenCorridorByRsu: buildGreenCorridorIndex(nextActiveCorridors, nowIso),
        });
      }

      return {
        ok: true,
        message: `Green corridor activated for ${anchorRsuId}.`,
        rsu_ids: corridor?.rsu_ids ?? [],
        corridor_id: corridor?.corridor_id,
      };
    } catch (error) {
      console.error('Failed to activate green corridor:', error);
      return {
        ok: false,
        message: 'Request failed while activating green corridor.',
      };
    }
  },

  clearGreenCorridors: async (url, options) => {
    const normalizedUrl = String(url ?? '').trim();
    if (!normalizedUrl) {
      return { ok: false, message: 'Server URL is missing.' };
    }

    const corridorId = String(options?.corridorId ?? '').trim();
    const anchorRsuId = String(options?.anchorRsuId ?? '').trim();

    try {
      const response = await fetch(`${normalizedUrl}/signals/green-corridor`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          action: 'clear',
          corridor_id: corridorId || undefined,
          anchor_rsu_id: anchorRsuId || undefined,
        }),
      });

      const data = await parseJsonResponse(response, '/signals/green-corridor POST clear');
      if (!response.ok || data?.status !== 'ok') {
        return {
          ok: false,
          message: String(data?.message ?? 'Failed to clear green corridor.'),
        };
      }

      const activeCorridors = normalizeGreenCorridorPlanList(data?.active_corridors);
      const nowIso = new Date().toISOString();
      set({
        activeCorridors,
        greenCorridorByRsu: buildGreenCorridorIndex(activeCorridors, nowIso),
      });

      return {
        ok: true,
        message: corridorId
          ? 'Green corridor cleared.'
          : (anchorRsuId ? `Corridor cleared for ${anchorRsuId}.` : 'All green corridors cleared.'),
      };
    } catch (error) {
      console.error('Failed to clear green corridor:', error);
      return {
        ok: false,
        message: 'Request failed while clearing green corridor.',
      };
    }
  },

  simulateKPIs: () => {
    // A small function to gently wiggle the throughput/speed KPIs for dashboard liveness
    set((state) => {
      const wiggle = Math.random() > 0.5 ? 1 : -1;
      const newThroughput = Math.max(2500, Math.min(4000, state.throughput + (wiggle * Math.floor(Math.random() * 20))));
      const newSpeed = Math.max(15, Math.min(45, state.avgSpeed + (wiggle * Math.random() * 0.5)));
      return { throughput: newThroughput, avgSpeed: newSpeed };
    });
  }
}));
