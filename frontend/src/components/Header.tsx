import React from 'react';
import { Search, Bell, Mail, User, PanelLeft, LogOut } from 'lucide-react';
import styles from './Layout.module.css';
import { useTrafficStore } from '@/store/useTrafficStore';
import { useAuthStore } from '@/store/useAuthStore';
import api from '@/utils/api';
import { useRouter } from 'next/navigation';

export default function Header() {
  const { sidebarOpen, setSidebarOpen } = useTrafficStore();
  const { user, logout } = useAuthStore();
  const router = useRouter();

  const handleLogout = async () => {
    try {
      await api.post('/auth/logout');
    } catch (err) {
      console.error('Logout failed', err);
    } finally {
      logout();
      router.push('/auth');
    }
  };

  return (
    <header className={styles.topHeader}>
      <div className={styles.headerLeft}>
        <button 
          className={styles.iconButton} 
          onClick={() => setSidebarOpen(!sidebarOpen)}
          style={{ marginRight: '12px' }}
        >
          <PanelLeft size={20} className={!sidebarOpen ? 'text-accent-cyan' : ''} />
        </button>
        <h1 className={styles.brandTitle}>Hybrid AI Traffic Control</h1>
        <div className={styles.statusBadge}>
          <span className={styles.statusDot}></span>
          LIVE FEED ACTIVE
        </div>
      </div>
      
      <div className={styles.headerRight}>
        <div className={styles.searchBar}>
          <Search size={16} className={styles.searchIcon} />
          <input type="text" placeholder="Search" className={styles.searchInput} />
        </div>
        
        <div className={styles.actionButtons}>
          <button className={styles.iconButton}><Mail size={18} /></button>
          <button className={styles.iconButton}><Bell size={18} /></button>
          
          <div className={styles.userProfile}>
            <div className={styles.userInfo}>
              <span className={styles.userRole}>Authorized Agent</span>
              <span className={styles.userEmail}>{user?.email || 'Unknown'}</span>
            </div>
            <div className={styles.profileAvatar}>
              <User size={18} />
            </div>
            <button 
              onClick={handleLogout}
              className={`${styles.iconButton} text-rose-500 hover:bg-rose-500/10 transition-colors ml-1`}
              style={{ color: '#f43f5e', marginLeft: '4px' }}
              title="Terminate Session"
            >
              <LogOut size={18} />
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}
