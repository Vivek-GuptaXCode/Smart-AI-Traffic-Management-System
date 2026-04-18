'use client';

import React, { useEffect, useState, useMemo } from 'react';
import { useTrafficStore } from '@/store/useTrafficStore';
import styles from '@/app/page.module.css';
import { motion, AnimatePresence } from 'framer-motion';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  PieChart,
  Pie,
  Legend
} from 'recharts';
import {
  AlertTriangle,
  Activity,
  Database,
  RefreshCcw,
  PieChart as PieChartIcon,
  Search,
  Zap,
  Clock
} from 'lucide-react';

interface Hotspot {
  rsu_id: string;
  frequency: number;
}

interface Distribution {
  rsu_id: string;
  count: number;
}

interface MissionSummary {
  total_events: number;
  total_alerts: number;
}

export default function Reports() {
  const { nodes } = useTrafficStore();
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [distribution, setDistribution] = useState<Distribution[]>([]);
  const [summary, setSummary] = useState<MissionSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const [latency, setLatency] = useState<number | null>(null);

  const serverUrl = (process.env.NEXT_PUBLIC_SERVER_URL ?? 'http://127.0.0.1:5000').trim();

  // ✅ Optimized junction lookup (User implementation)
  const nodeMap = useMemo(() => {
    return new Map(nodes.map(n => [n.id, n.display_name]));
  }, [nodes]);

  const getDisplayName = (rsuId: string) => {
    return nodeMap.get(rsuId) || rsuId;
  };

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    const startTime = performance.now();
    try {
      const fetchOpts = { cache: 'no-store' as RequestCache };
      const [respHotspots, respSummary, respDist] = await Promise.all([
        fetch(`${serverUrl}/analytics/hotspots`, fetchOpts),
        fetch(`${serverUrl}/analytics/summary`, fetchOpts),
        fetch(`${serverUrl}/analytics/distributions`, fetchOpts)
      ]);

      // ✅ Safe processing (User implementation)
      const safeParse = async (resp: Response, name: string) => {
        if (!resp.ok) throw new Error(`${name}_FAILED`);
        return resp.json();
      };

      const [dataHotspots, dataSummary, dataDist] = await Promise.all([
        safeParse(respHotspots, 'HOTSPOTS'),
        safeParse(respSummary, 'SUMMARY'),
        safeParse(respDist, 'DISTRIBUTIONS')
      ]);

      setHotspots(dataHotspots.hotspots || []);
      setSummary(dataSummary || null);
      setDistribution(dataDist.distribution || []);
      setLatency(Math.round(performance.now() - startTime));
      setLastSync(new Date().toLocaleTimeString());
    } catch (err) {
      console.error('Fetch failed:', err);
      setError('BACKEND_LOG_UNAVAILABLE');
    } finally {
      setTimeout(() => setLoading(false), 500);
    }
  };

  // ✅ Safe polling with mount check (User implementation)
  useEffect(() => {
    let isMounted = true;
    const poll = async () => {
      if (!isMounted) return;
      await fetchData();
    };
    poll();
    const interval = setInterval(poll, 10000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const chartData = useMemo(() => {
    return hotspots.map(h => ({
      name: getDisplayName(h.rsu_id),
      frequency: h.frequency
    }));
  }, [hotspots, nodeMap]);

  // ✅ Grouping logic (User implementation)
  const pieData = useMemo(() => {
    if (distribution.length === 0) return [];
    const mapped = distribution.map(d => ({
      name: getDisplayName(d.rsu_id),
      value: d.count
    }));
    if (mapped.length > 7) {
      const top = mapped.slice(0, 6);
      const others = mapped.slice(6).reduce((acc, curr) => acc + curr.value, 0);
      return [...top, { name: 'OTHERS', value: others }];
    }
    return mapped;
  }, [distribution, nodeMap]);

  const COLORS = ['#0ffff0', '#a855f7', '#3b82f6', '#10b981', '#f59e0b', '#ec4899', '#f97316'];

  const renderEmptyState = (title: string, icon: React.ReactNode) => (
    <div className={styles.emptyState}>
      <div className={styles.emptyStateIcon}>{icon}</div>
      <div className={styles.emptyStateTitle}>{title}</div>
      <p className={styles.emptyStateText}>Waiting for simulation events to populate archives...</p>
    </div>
  );

  return (
    <div className={styles.reportContainer}>
      {/* Header */}
      {/* <div className="flex justify-between items-end mb-8">
        <div>
          <h2 className={styles.sectionTitle} style={{ marginBottom: '4px' }}>MISSION ARCHIVE AUDIT</h2>
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-accent-cyan animate-pulse"></span>
            <p className="text-secondary text-[10px] font-mono tracking-widest text-uppercase">
              {lastSync ? `LAST_SYNC: ${lastSync}` : 'SYNCING_DB...'}
            </p>
          </div>
        </div>
      </div> */}

      {error && (
        <div className="flex items-center gap-2 mb-6 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-500 text-[10px] font-mono uppercase tracking-wider">
          <AlertTriangle size={14} />
          System Error: {error}
        </div>
      )}

      {/* Summary HUD */}
      <div className={styles.reportGrid}>
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className={styles.summaryCard}>
          <div className={styles.cardHeader}>
            <Database size={14} className="text-accent-cyan" />
            <span>TOTAL_DATABASE_RECORDS</span>
          </div>
          <div className={styles.cardValue}>{summary?.total_events || 0}</div>
          <div className={styles.cardFooter}>Archived missions since system init</div>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className={styles.summaryCard}>
          <div className={styles.cardHeader}>
            <AlertTriangle size={14} style={{ color: '#ef4444' }} />
            <span>CRITICAL_ALERTS</span>
          </div>
          <div className={styles.cardValue} style={{ color: '#ef4444' }}>{summary?.total_alerts || 0}</div>
          <div className={styles.cardFooter}>Total congestion mitigation triggers</div>
        </motion.div>

        {/* <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }} className={styles.summaryCard}>
          <div className={styles.cardHeader}>
            <Zap size={14} className="text-accent-purple" />
            <span>OPTIMIZATION_INDEX</span>
          </div>
          <div className={styles.cardValue} style={{ color: 'var(--accent-purple)' }}>
            {summary?.total_events ? (((summary.total_events - summary.total_alerts) / summary.total_events) * 100).toFixed(1) : '0.0'}%
          </div>
          <div className={styles.cardFooter}>Network efficiency score</div>
        </motion.div> */}

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} className={styles.summaryCard}>
          <div className={styles.cardHeader}>
            <Activity size={14} style={{ color: latency && latency > 100 ? '#f59e0b' : '#10b981' }} />
            <span>SYSTEM_LATENCY</span>
          </div>
          <div className={styles.cardValue} style={{ color: latency && latency > 100 ? '#f59e0b' : '#10b981' }}>
            {latency !== null ? `${latency}ms` : '--'}
          </div>
          <div className={styles.cardFooter}>API response round-trip time</div>
        </motion.div>
      </div>

      {/* Charts Grid */}
      <div className={styles.chartsGrid}>
        {/* Bar Chart */}
        <motion.div initial={{ opacity: 0, scale: 0.99 }} animate={{ opacity: 1, scale: 1 }} className={styles.chartCard} style={{ flex: 3 }}>
          <div className={styles.chartHeader}>
            <div className="flex items-center gap-3">
              <Activity size={16} className="text-accent-cyan" />
              <h3 className="uppercase">Top 5 Congestion Hotspots</h3>
            </div>
          </div>

          <div className={styles.chartBody}>
            {chartData.length > 0 ? (
              <ResponsiveContainer width="100%" height={320}>
                <BarChart data={chartData} margin={{ top: 20, right: 30, left: 0, bottom: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
                  <XAxis dataKey="name" stroke="rgba(255,255,255,0.3)" fontSize={9} tickLine={false} axisLine={false} dy={10} />
                  <YAxis stroke="rgba(255,255,255,0.3)" fontSize={9} tickLine={false} axisLine={false} />
                  <Tooltip
                    cursor={{ fill: 'rgba(255,255,255,0.02)' }}
                    contentStyle={{ backgroundColor: '#0a0a0f', border: '1px solid #ffffff10', fontSize: '11px', borderRadius: '4px' }}
                  />
                  <Bar dataKey="frequency" radius={[2, 2, 0, 0]}>
                    {chartData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} fillOpacity={0.6} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : renderEmptyState('No Hotspots Detected', <Search size={40} />)}
          </div>
        </motion.div>

        {/* Pie Chart */}
        <motion.div initial={{ opacity: 0, scale: 0.99 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: 0.1 }} className={styles.chartCard}>
          <div className={styles.chartHeader}>
            <div className="flex items-center gap-3">
              <PieChartIcon size={16} className="text-accent-purple" />
              <h3 className="uppercase">Congestion Share by Junction</h3>
            </div>
          </div>

          <div className={styles.chartBody}>
            {pieData.length > 0 ? (
              <ResponsiveContainer width="100%" height={320}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="45%"
                    innerRadius={60}
                    outerRadius={80}
                    paddingAngle={5}
                    dataKey="value"
                    animationBegin={200}
                    stroke="none"
                  >
                    {pieData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} fillOpacity={0.6} />)}
                  </Pie>
                  <Tooltip
                    contentStyle={{ backgroundColor: '#0a0a0f', border: '1px solid #ffffff10', fontSize: '11px', borderRadius: '4px' }}
                    formatter={(value, name) => {
                      const v = Number(value);
                      const total = pieData.reduce((acc, curr) => acc + curr.value, 0);
                      const percent = ((v / total) * 100).toFixed(1);
                      return [`${v} incidents (${percent}%)`, String(name)];
                    }}
                  />
                  <Legend
                    verticalAlign="bottom"
                    height={36}
                    iconType="circle"
                    formatter={(value) => <span style={{ fontSize: '9px', color: '#888', letterSpacing: '1px' }}>{value}</span>}
                  />
                </PieChart>
              </ResponsiveContainer>
            ) : renderEmptyState('No Congestion Mix Found', <PieChartIcon size={40} />)}
          </div>
        </motion.div>
      </div>
    </div>
  );
}
