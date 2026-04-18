'use client';

import { Activity, Wifi, Zap, Car, AlertTriangle } from 'lucide-react';
import styles from '@/app/page.module.css';
import { useTrafficStore } from '@/store/useTrafficStore';
import { motion, AnimatePresence } from 'framer-motion';

export default function KPIBar() {
  const { throughput, avgSpeed, isConnected, congestionState } = useTrafficStore();

  const congestedCount = Object.values(congestionState).filter(c => c.isCongested).length;

  return (
    <div className={styles.kpiContainer}>
      <div className={`${styles.glassPanel} ${styles.kpiCard}`}>
        <div className={styles.kpiTitle}>
          <Wifi size={16} /> V2X Backend Status
        </div>
        <div className={`${styles.kpiValue} ${isConnected ? styles.statusOnline : ''}`}>
          {isConnected ? 'ONLINE' : 'OFFLINE'}
        </div>
      </div>

      <div className={`${styles.glassPanel} ${styles.kpiCard}`}>
        <div className={styles.kpiTitle}>
          <Activity size={16} /> System Throughput
        </div>
        <div className={styles.kpiValue}>
          <AnimatePresence mode="wait">
            <motion.span
              key={throughput}
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -5 }}
            >
              {throughput.toLocaleString()}
            </motion.span>
          </AnimatePresence>
          <span className={styles.kpiUnit}>veh/hr</span>
        </div>
      </div>

      <div className={`${styles.glassPanel} ${styles.kpiCard}`}>
        <div className={styles.kpiTitle}>
          <Car size={16} /> Avg System Speed
        </div>
        <div className={styles.kpiValue}>
          <motion.span
            animate={{ scale: [1, 1.02, 1] }}
            transition={{ duration: 2, repeat: Infinity }}
          >
            {avgSpeed.toFixed(1)}
          </motion.span>
          <span className={styles.kpiUnit}>m/s</span>
        </div>
      </div>

      <div className={`${styles.glassPanel} ${styles.kpiCard}`}>
        <div className={styles.kpiTitle}>
          <AlertTriangle size={16} className={congestedCount > 0 ? 'text-magenta animate-pulse' : ''} />
          Congested Zones
        </div>
        <div className={`${styles.kpiValue} ${congestedCount > 0 ? styles.congestionStripActive : ''}`}>
          {congestedCount} <span className={styles.kpiUnit}>RSUs</span>
        </div>
      </div>

      <div className={`${styles.glassPanel} ${styles.kpiCard}`}>
        <div className={styles.kpiTitle}>
          <Zap size={16} /> AI Reroute Rate
        </div>
        <div className={styles.kpiValue}>
          14.2 <span className={styles.kpiUnit}>%</span>
        </div>
      </div>
    </div>
  );
}
