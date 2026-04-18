'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Activity, RefreshCcw } from 'lucide-react';
import { useTrafficStore } from '@/store/useTrafficStore';
import styles from './RSUTrafficTimeline.module.css';

interface TimelineSeries {
  series_id: string;
  rsu_id: string;
  label: string;
  values: number[];
  total: number;
}

interface AvailableRsu {
  rsu_id: string;
  label: string;
}

interface TimelineResponse {
  status: string;
  metric: string;
  window_minutes: number;
  bucket_minutes: number;
  bucket_labels: string[];
  bucket_timestamps: string[];
  series: TimelineSeries[];
  available_rsus: AvailableRsu[];
  server_timestamp: string;
}

interface ChartSeries extends TimelineSeries {
  chartKey: string;
  color: string;
}

interface RSUTrafficTimelineProps {
  serverUrl: string;
}

type MetricKey = 'congestion_count' | 'event_count' | 'clear_count' | 'avg_wait' | 'avg_vehicle_count';

const METRIC_OPTIONS: Array<{ value: MetricKey; label: string; unit: string }> = [
  { value: 'congestion_count', label: 'Congestion Alerts', unit: 'alerts' },
  { value: 'event_count', label: 'All Events', unit: 'events' },
  { value: 'clear_count', label: 'Clear Events', unit: 'events' },
  { value: 'avg_wait', label: 'Average Wait', unit: 'seconds' },
  { value: 'avg_vehicle_count', label: 'Average Vehicle Count', unit: 'vehicles' },
];

const WINDOW_OPTIONS = [30, 60, 180, 360, 720];
const BUCKET_OPTIONS = [1, 5, 10, 15, 30];
const SERIES_COLORS = ['#0ffff0', '#34d399', '#f59e0b', '#f43f5e', '#a855f7', '#3b82f6', '#f97316'];

export default function RSUTrafficTimeline({ serverUrl }: RSUTrafficTimelineProps) {
  const { nodes, selectedNodeId } = useTrafficStore();

  const [metric, setMetric] = useState<MetricKey>('congestion_count');
  const [windowMinutes, setWindowMinutes] = useState<number>(180);
  const [bucketMinutes, setBucketMinutes] = useState<number>(5);
  const [includeAll, setIncludeAll] = useState<boolean>(true);
  const [selectedRsuIds, setSelectedRsuIds] = useState<string[]>([]);
  const [timelineData, setTimelineData] = useState<TimelineResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>('');

  const selectedSignature = selectedRsuIds.join('|');

  useEffect(() => {
    let isActive = true;

    const fetchTimeline = async (silent: boolean) => {
      if (!silent) {
        setLoading(true);
      }
      setError('');

      try {
        const params = new URLSearchParams();
        params.set('metric', metric);
        params.set('window_minutes', String(windowMinutes));
        params.set('bucket_minutes', String(bucketMinutes));
        params.set('include_all', includeAll ? 'true' : 'false');
        if (selectedRsuIds.length > 0) {
          params.set('rsu_ids', selectedRsuIds.join(','));
        }

        const response = await fetch(`${serverUrl}/analytics/traffic-timeseries?${params.toString()}`, {
          cache: 'no-store',
        });
        const payload = (await response.json()) as TimelineResponse & { message?: string };

        if (!response.ok || payload.status !== 'ok') {
          throw new Error(payload.message || `Failed to load timeline (HTTP ${response.status}).`);
        }

        if (isActive) {
          setTimelineData(payload);
        }
      } catch (err) {
        if (!isActive) {
          return;
        }
        const message = err instanceof Error ? err.message : 'Unable to load RSU timeline.';
        setError(message);
      } finally {
        if (isActive && !silent) {
          setLoading(false);
        }
      }
    };

    void fetchTimeline(false);
    const pollInterval = setInterval(() => {
      void fetchTimeline(true);
    }, 15000);

    return () => {
      isActive = false;
      clearInterval(pollInterval);
    };
  }, [serverUrl, metric, windowMinutes, bucketMinutes, includeAll, selectedSignature, selectedRsuIds]);

  const metricMeta = useMemo(
    () => METRIC_OPTIONS.find((entry) => entry.value === metric) ?? METRIC_OPTIONS[0],
    [metric],
  );

  const availableRsuOptions = useMemo(() => {
    const fromApi = timelineData?.available_rsus ?? [];
    if (fromApi.length > 0) {
      return fromApi;
    }

    return nodes
      .map((node) => ({
        rsu_id: String(node.id),
        label: String(node.display_name || node.id),
      }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [timelineData, nodes]);

  const chartSeries = useMemo<ChartSeries[]>(() => {
    const sourceSeries = timelineData?.series ?? [];
    return sourceSeries.map((entry, index) => ({
      ...entry,
      chartKey: `s_${index}`,
      color: SERIES_COLORS[index % SERIES_COLORS.length],
    }));
  }, [timelineData]);

  const chartRows = useMemo(() => {
    if (!timelineData) {
      return [];
    }

    return timelineData.bucket_labels.map((label, index) => {
      const row: Record<string, number | string> = {
        label,
        timestamp: timelineData.bucket_timestamps[index] || label,
      };

      chartSeries.forEach((series) => {
        const rawValue = Number(series.values[index] ?? 0);
        row[series.chartKey] = Number.isFinite(rawValue) ? rawValue : 0;
      });

      return row;
    });
  }, [timelineData, chartSeries]);

  const toggleRsuSelection = (rsuId: string) => {
    setSelectedRsuIds((previous) => {
      if (previous.includes(rsuId)) {
        return previous.filter((id) => id !== rsuId);
      }

      if (previous.length >= 8) {
        return previous;
      }

      return [...previous, rsuId];
    });
  };

  const addSelectedMapRsu = () => {
    const candidate = String(selectedNodeId || '').trim();
    if (!candidate) {
      return;
    }

    setSelectedRsuIds((previous) => {
      if (previous.includes(candidate) || previous.length >= 8) {
        return previous;
      }
      return [...previous, candidate];
    });
  };

  const clearCompareSelection = () => {
    setSelectedRsuIds([]);
  };

  return (
    <div className={styles.wrapper}>
      <div className={styles.controlsRow}>
        <label className={styles.controlGroup}>
          <span>Metric</span>
          <select value={metric} onChange={(event) => setMetric(event.target.value as MetricKey)}>
            {METRIC_OPTIONS.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>

        <label className={styles.controlGroup}>
          <span>Window</span>
          <select value={windowMinutes} onChange={(event) => setWindowMinutes(Number(event.target.value))}>
            {WINDOW_OPTIONS.map((minutes) => (
              <option key={minutes} value={minutes}>
                Last {minutes}m
              </option>
            ))}
          </select>
        </label>

        <label className={styles.controlGroup}>
          <span>Bucket</span>
          <select value={bucketMinutes} onChange={(event) => setBucketMinutes(Number(event.target.value))}>
            {BUCKET_OPTIONS.map((minutes) => (
              <option key={minutes} value={minutes}>
                {minutes}m
              </option>
            ))}
          </select>
        </label>

        <label className={styles.toggleGroup}>
          <input
            type="checkbox"
            checked={includeAll}
            onChange={(event) => setIncludeAll(event.target.checked)}
          />
          Include All RSUs aggregate
        </label>

        <button type="button" className={styles.secondaryButton} onClick={addSelectedMapRsu}>
          <Activity size={14} />
          Add Selected Map RSU
        </button>
      </div>

      <div className={styles.comparePanel}>
        <div className={styles.compareHeader}>
          <h3>Compare RSUs</h3>
          <div className={styles.compareActions}>
            <span>{selectedRsuIds.length}/8 selected</span>
            <button type="button" className={styles.clearButton} onClick={clearCompareSelection}>
              Clear
            </button>
          </div>
        </div>

        <div className={styles.rsuList}>
          {availableRsuOptions.map((option) => {
            const checked = selectedRsuIds.includes(option.rsu_id);
            return (
              <label key={option.rsu_id} className={styles.rsuOption}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleRsuSelection(option.rsu_id)}
                />
                <span>{option.label}</span>
              </label>
            );
          })}
        </div>
      </div>

      <div className={styles.chartCard}>
        <div className={styles.chartHeader}>
          <div>
            <h3>{metricMeta.label} vs Time</h3>
            <p>
              {timelineData?.window_minutes ?? windowMinutes}m window, {timelineData?.bucket_minutes ?? bucketMinutes}m buckets
            </p>
          </div>
          {loading ? (
            <span className={styles.loadingBadge}>
              <RefreshCcw size={14} className={styles.spin} />
              Loading
            </span>
          ) : null}
        </div>

        {error ? <p className={styles.errorText}>{error}</p> : null}

        {!error && chartRows.length > 0 && chartSeries.length > 0 ? (
          <div className={styles.chartBody}>
            <ResponsiveContainer width="100%" height={360}>
              <LineChart data={chartRows} margin={{ top: 16, right: 20, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="label" stroke="rgba(255,255,255,0.55)" tickLine={false} axisLine={false} />
                <YAxis stroke="rgba(255,255,255,0.55)" tickLine={false} axisLine={false} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#07111d',
                    border: '1px solid rgba(255,255,255,0.16)',
                    borderRadius: '8px',
                    color: '#d7e3f4',
                  }}
                  labelFormatter={(label, payload) => {
                    const timestamp = payload?.[0]?.payload?.timestamp;
                    return timestamp ? `${label} (${timestamp})` : String(label);
                  }}
                  formatter={(value) => [`${Number(value)} ${metricMeta.unit}`, metricMeta.label]}
                />
                <Legend />

                {chartSeries.map((series) => (
                  <Line
                    key={series.series_id}
                    type="monotone"
                    dataKey={series.chartKey}
                    name={series.label}
                    stroke={series.color}
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4 }}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>

            <div className={styles.seriesSummary}>
              {chartSeries.map((series) => (
                <div key={series.series_id} className={styles.seriesChip}>
                  <span className={styles.colorDot} style={{ backgroundColor: series.color }} />
                  <span className={styles.seriesName}>{series.label}</span>
                  <strong>{series.total}</strong>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {!error && chartRows.length === 0 ? (
          <div className={styles.emptyState}>
            <p>No timeline data available for the selected filters yet.</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
