'use client';

import { useEffect, useState } from 'react';
import styles from './page.module.css';
import { useTrafficStore } from '@/store/useTrafficStore';
import dynamic from 'next/dynamic';

const NetworkGraph = dynamic(() => import('@/components/NetworkGraph'), { ssr: false });
const EventFeed = dynamic(() => import('@/components/EventFeed'), { ssr: false });
const RSUSpotlight = dynamic(() => import('@/components/RSUSpotlight'), { ssr: false });
const Reports = dynamic(() => import('@/components/Reports'), { ssr: false });
const TrafficChatbox = dynamic(() => import('@/components/TrafficChatbox'), { ssr: false });
const RSUTrafficTimeline = dynamic(() => import('@/components/RSUTrafficTimeline'), { ssr: false });
const SystemSettings = dynamic(() => import('@/components/SystemSettings'), { ssr: false });

const DEFAULT_SERVER_URL = (process.env.NEXT_PUBLIC_SERVER_URL ?? 'http://127.0.0.1:5000').trim();

export default function Dashboard() {
  const { fetchGraph, connect, disconnect, selectedNodeId, setSelectedNodeId, activeView } = useTrafficStore();
  const [serverUrl] = useState(DEFAULT_SERVER_URL || 'http://127.0.0.1:5000');

  useEffect(() => {
    connect(serverUrl);
    fetchGraph(serverUrl);
    const refreshInterval = setInterval(() => {
      fetchGraph(serverUrl);
    }, 5000);

    return () => {
      clearInterval(refreshInterval);
      disconnect();
    };
  }, [serverUrl, fetchGraph, connect, disconnect]);

  const renderView = () => {
    switch (activeView) {
      case 'Event Feed':
        return (
          <section className={styles.fullWidthSection}>
            <div className="flex justify-between items-center mb-4">
              <h2 className={styles.sectionTitle}>Real-time Event Log</h2>
              {/* <div className="text-[10px] text-secondary font-mono bg-white/5 px-2 py-1 rounded">
                MISSION_LOG_STREAM: {serverUrl}
              </div> */}
            </div>
            <div className="flex-1 overflow-hidden" style={{ minHeight: 0 }}>
              <EventFeed />
            </div>
          </section>
        );

      case 'Reports':
        return (
          <section className={styles.fullWidthSection}>
            <Reports />
          </section>
        );

      case 'Real-time Metrics':
        return (
          <section className={styles.fullWidthSection}>
            <div className="flex justify-between items-center mb-4">
              <h2 className={styles.sectionTitle}>Traffic Trend Timeline</h2>
            </div>
            <div className="flex-1 overflow-hidden" style={{ minHeight: 0 }}>
              <RSUTrafficTimeline serverUrl={serverUrl} />
            </div>
          </section>
        );

      case 'AI Controls':
        return (
          <section className={styles.fullWidthSection}>
            <div className="flex justify-between items-center mb-4">
              <h2 className={styles.sectionTitle}>RSU Insight Chat</h2>
            </div>
            <div className="flex-1 overflow-hidden" style={{ minHeight: 0 }}>
              <TrafficChatbox serverUrl={serverUrl} selectedRsuId={selectedNodeId} />
            </div>
          </section>
        );

      case 'System Settings':
        return (
          <section className={styles.fullWidthSection} style={{ padding: 0, background: 'transparent', border: 'none', backdropFilter: 'none' }}>
            <SystemSettings />
          </section>
        );

      case 'Global Topology':
      default:
        return (
          <div className={styles.mainGrid}>
            {/* Left Section: Real-time Network Visualization */}
            <section className={styles.mapSection}>
              <div className="flex justify-between items-center mb-2">
                <h2 className={styles.sectionTitle}>Global V2X Topology</h2>
                {/* <div className="text-[10px] text-secondary font-mono bg-white/5 px-2 py-1 rounded">
                  LIVE_STREAM: {serverUrl}
                </div> */}
              </div>
              <NetworkGraph
                serverUrl={serverUrl}
                selectedNodeId={selectedNodeId || undefined}
                onNodeClick={(id) => setSelectedNodeId(id)}
              />
            </section>

            {/* Right Section: Tactical Spotlight */}
            <aside className={styles.feedSection}>
              <div className="flex-1 flex flex-col gap-6 overflow-hidden">
                <div className="flex flex-col h-full">
                  <h2 className={styles.sectionTitle}>RSU Spotlight</h2>
                  <RSUSpotlight />
                </div>
              </div>
            </aside>
          </div>
        );
    }
  };

  return (
    <main className={styles.dashboard}>
      {renderView()}
    </main>
  );
}
