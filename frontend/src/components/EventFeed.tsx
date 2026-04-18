'use client';

import React, { useState, useMemo } from 'react';
import { useTrafficStore } from '@/store/useTrafficStore';
import styles from '@/app/page.module.css';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  AlertTriangle, 
  CheckCircle, 
  Info, 
  Terminal, 
  Activity, 
  Filter, 
  X, 
  ChevronLeft, 
  ChevronRight 
} from 'lucide-react';

export default function EventFeed() {
  const { eventsLog, nodes } = useTrafficStore();
  const [filterRsuId, setFilterRsuId] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;

  // Reset to first page when filter changes
  React.useEffect(() => {
    setCurrentPage(1);
  }, [filterRsuId]);

  const getDisplayName = (rsuId: string) => {
    if (rsuId === 'SYSTEM') return 'CMD_CENTER';
    const node = nodes.find(n => n.id === rsuId);
    return node?.display_name || (rsuId.length > 20 ? `${rsuId.substring(0, 15)}...` : rsuId);
  };

  const filteredEvents = useMemo(() => {
    if (!filterRsuId) return eventsLog;
    return eventsLog.filter(e => String(e.rsu_id).trim() === String(filterRsuId).trim());
  }, [eventsLog, filterRsuId]);

  const totalPages = Math.max(1, Math.ceil(filteredEvents.length / itemsPerPage));
  const paginatedEvents = useMemo(() => {
    const start = (currentPage - 1) * itemsPerPage;
    return filteredEvents.slice(start, start + itemsPerPage);
  }, [filteredEvents, currentPage]);

  const uniqueRsuIds = useMemo(() => {
    const ids = Array.from(
      new Set(
        eventsLog
          .map(e => String(e.rsu_id).trim())
          .filter(id => id && id.toUpperCase() !== 'SYSTEM')
      )
    );
    return ids.sort();
  }, [eventsLog]);

  const formatTime = (ts: string) => {
    try {
      const date = new Date(ts);
      if (isNaN(date.getTime())) return 'Now';
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return 'Now';
    }
  };

  const getIcon = (type: string) => {
    switch (type) {
      case 'alert': return <AlertTriangle size={16} />;
      case 'clear': return <CheckCircle size={16} />;
      case 'corridor': return <Activity size={16} />;
      case 'system': return <Terminal size={16} />;
      default: return <Info size={16} />;
    }
  };

  const getTypeClass = (type: string) => {
    switch (type) {
      case 'alert': return styles.eventAlert;
      case 'clear': return styles.eventClear;
      case 'corridor': return styles.eventCorridor;
      case 'system': return styles.eventSystem;
      default: return styles.eventSystem;
    }
  };

  return (
    <div className={`${styles.glassPanel} ${styles.eventFeedContainer}`}>
      <div className={styles.filterBar}>
        <div className={styles.filterGroup}>
          <Filter size={14} style={{ color: filterRsuId ? 'var(--accent-cyan)' : 'var(--text-secondary)' }} />
          <select 
            className={styles.filterSelect}
            value={filterRsuId || ''}
            onChange={(e) => setFilterRsuId(e.target.value || null)}
          >
            <option value="">ALL SIGNAL STREAMS</option>
            {uniqueRsuIds.map(id => (
              <option key={id} value={id}>
                {getDisplayName(id)}
              </option>
            ))}
          </select>
        </div>
        {filterRsuId && (
          <button className={styles.filterResetBtn} onClick={() => setFilterRsuId(null)}>
            <X size={14} /> CLEAR
          </button>
        )}
      </div>

      <div className={styles.eventLogScroll}>
        <AnimatePresence mode="popLayout" initial={false}>
          {paginatedEvents.length === 0 && (
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="py-12 text-center"
              style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}
            >
              {filterRsuId ? 'No matching logs for this RSU' : 'Waiting for real-time events...'}
            </motion.div>
          )}
          {paginatedEvents.map((event) => (
            <motion.div
              layout
              key={event.id}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, scale: 0.95 }}
              transition={{ duration: 0.2 }}
              className={`${styles.eventItem} ${getTypeClass(event.type)}`}
            >
              <div className={styles.eventIcon}>{getIcon(event.type)}</div>
              <div className={styles.eventContent}>
                <div className={styles.eventHeader}>
                  <span style={{ fontWeight: 600 }}>{getDisplayName(event.rsu_id)}</span>
                  <span>{formatTime(event.timestamp)}</span>
                </div>
                <div className={styles.eventMessage}>{event.message}</div>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      <div className={styles.paginationFooter}>
        <div className={styles.pageInfo}>
          RECORD_SET: <span className="text-secondary">{filteredEvents.length} ITEMS</span>
        </div>
        <div className={styles.pageControls}>
          <button 
            className={styles.pagedButton}
            onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
            disabled={currentPage === 1}
          >
            <ChevronLeft size={16} />
          </button>
          <div className={styles.pageIndicator}>
            <span style={{ color: 'var(--accent-cyan)' }}>{String(currentPage).padStart(2, '0')}</span>
            <span style={{ opacity: 0.3 }}>/</span>
            <span>{String(totalPages).padStart(2, '0')}</span>
          </div>
          <button 
            className={styles.pagedButton}
            onClick={() => setCurrentPage(prev => Math.min(totalPages, prev + 1))}
            disabled={currentPage === totalPages}
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
