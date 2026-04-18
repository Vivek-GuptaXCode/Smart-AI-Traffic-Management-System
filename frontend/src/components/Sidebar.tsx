'use client';

import React from 'react';
import { LayoutDashboard, Globe, Activity, ListOrdered, Cpu, FileText, Settings } from 'lucide-react';
import styles from './Layout.module.css';
import { useTrafficStore } from '@/store/useTrafficStore';

export default function Sidebar() {
  const { activeView, setActiveView } = useTrafficStore();

  const navItems = [
    { name: 'Dashboard', icon: <LayoutDashboard size={18} /> },
    { name: 'Global Topology', icon: <Globe size={18} /> },
    { name: 'Real-time Metrics', icon: <Activity size={18} /> },
    { name: 'Event Feed', icon: <ListOrdered size={18} /> },
    { name: 'AI Controls', icon: <Cpu size={18} /> },
    { name: 'Reports', icon: <FileText size={18} /> },
    { name: 'System Settings', icon: <Settings size={18} /> },
  ];

  return (
    <>
      <div className={styles.sidebarHeader}>
        {/* Placeholder for future branding */}
      </div>

      <nav className={styles.navMenu}>
        {navItems.map((item, idx) => {
          const isActive = activeView === item.name;
          return (
            <button
              key={idx}
              className={`${styles.navItem} ${isActive ? styles.navActive : ''}`}
              onClick={() => setActiveView(item.name)}
              style={{ background: 'none', border: 'none', textAlign: 'left', width: '100%' }}
            >
              <span className={styles.navIcon}>{item.icon}</span>
              <span className={styles.navLabel}>{item.name}</span>
            </button>
          );
        })}
      </nav>
    </>
  );
}
