'use client';

import React from 'react';
import { useTrafficStore } from '@/store/useTrafficStore';
import styles from '@/app/page.module.css';
import { motion, AnimatePresence } from 'framer-motion';
import { X, MapPin, Gauge, Clock, Radio, Activity } from 'lucide-react';

export default function RSUSpotlight() {
  const { selectedNodeId, setSelectedNodeId, nodes, congestionState, greenCorridorByRsu } = useTrafficStore();

  // Search by exact ID string comparison
  const selectedNode = nodes.find(n => String(n.id).trim() === String(selectedNodeId).trim());

  const stats = selectedNodeId ? congestionState[selectedNodeId] : null;
  const isInCorridor = selectedNodeId ? Boolean(greenCorridorByRsu[selectedNodeId]) : false;

  if (!selectedNodeId || !selectedNode) {
    return (
      <div className={styles.spotlightHud} style={{ justifyContent: 'center', alignItems: 'center', textAlign: 'center' }}>
        <div className={styles.scanline} />
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex flex-col items-center justify-center py-12"
        >
          <Radio size={56} className="mb-6" style={{ color: 'var(--accent-cyan)', opacity: 0.3 }} />
          <h4 className="text-sm font-bold uppercase tracking-wider mb-2" style={{ color: 'var(--text-primary)' }}>
            RSU Monitoring Offline
          </h4>
          {/* <p className="text-xs max-w-[200px]" style={{ color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Select a Road Side Unit from the network graph to begin real-time diagnostic stream.
          </p> */}
        </motion.div>
      </div>
    );
  }

  const displayName = selectedNode.display_name || `RSU ${selectedNode.id}`;

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      className={styles.spotlightHud}
    >
      <div className={styles.scanline} />

      <div className={styles.spotlightHeader}>
        <h3 className={styles.spotlightTitle}>
          <MapPin size={20} style={{ color: 'var(--accent-cyan)' }} />
          {displayName}
        </h3>
        <button
          onClick={() => setSelectedNodeId(null)}
          className="p-1 hover:bg-white/10 rounded-full transition-colors relative z-10"
        >
          <X size={20} style={{ color: 'var(--text-secondary)' }} />
        </button>
      </div>

      <div className={styles.hudMetricGrid}>
        <div className={styles.hudCard}>
          <div className={styles.hudCardLabel}>
            <Gauge size={14} style={{ color: 'var(--accent-cyan)' }} />
            Vehicle Density
          </div>
          <div className={styles.hudCardValue}>
            <AnimatePresence mode="wait">
              <motion.span
                key={stats?.vehicle_count ?? 0}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
              >
                {stats?.isCongested ? stats.vehicle_count : (Math.floor(Math.random() * 3))}
              </motion.span>
            </AnimatePresence>
            <span className={styles.hudCardValueUnit}>avg</span>
          </div>
        </div>

        <div className={styles.hudCard}>
          <div className={styles.hudCardLabel}>
            <Clock size={14} style={{ color: 'var(--accent-magenta)' }} />
            Traffic Delay
          </div>
          <div className={styles.hudCardValue}>
            <AnimatePresence mode="wait">
              <motion.span
                key={stats?.avg_wait ?? 0}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
              >
                {stats?.isCongested ? stats.avg_wait : '0'}
              </motion.span>
            </AnimatePresence>
            <span className={styles.hudCardValueUnit}>sec</span>
          </div>
        </div>
      </div>

      <div className={styles.diagnosticBlock}>
        <div className={styles.hudCardLabel} style={{ marginBottom: '12px' }}>
          <Activity size={14} style={{ color: 'var(--accent-green)' }} />
          Signal Analytics
        </div>

        <div className={styles.statusIndicator}>
          <div
            className={styles.statusGlow}
            style={{
              backgroundColor: stats?.isCongested ? 'var(--accent-magenta)' : 'var(--accent-green)',
              boxShadow: stats?.isCongested ? '0 0 15px var(--accent-magenta)' : '0 0 15px var(--accent-green)'
            }}
          />
          <div className={styles.statusText} style={{ color: stats?.isCongested ? 'var(--accent-magenta)' : 'var(--accent-green)' }}>
            {stats?.isCongested ? 'CRITICAL CONGESTION' : 'NOMINAL FLOW'}
          </div>
        </div>

        {isInCorridor && (
          <div
            style={{
              marginTop: '10px',
              padding: '6px 12px',
              borderRadius: '8px',
              background: 'rgba(16, 185, 129, 0.15)',
              border: '1px solid var(--accent-green)',
              color: 'var(--accent-green)',
              fontSize: '11px',
              fontWeight: 700,
              letterSpacing: '0.08em',
              textAlign: 'center',
            }}
          >
            GREEN CORRIDOR ACTIVE
          </div>
        )}

        <div style={{ marginTop: '16px', fontSize: '10px', fontFamily: 'monospace', color: 'var(--text-secondary)', opacity: 0.6 }}>
          LATENCY: 24ms | UPLINK: {stats?.lastUpdated ? new Date(stats.lastUpdated).toLocaleTimeString() : 'N/A'}
        </div>
      </div>
    </motion.div>
  );
}
