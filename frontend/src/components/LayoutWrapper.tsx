'use client';

import React, { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Sidebar from "./Sidebar";
import Header from "./Header";
import { useTrafficStore } from '@/store/useTrafficStore';
import { useAuthStore } from '@/store/useAuthStore';
import layoutStyles from "./Layout.module.css";

export default function LayoutWrapper({ children }: { children: React.ReactNode }) {
  const { sidebarOpen } = useTrafficStore();
  const { isAuthenticated } = useAuthStore();
  const pathname = usePathname();
  const router = useRouter();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (mounted) {
      if (!isAuthenticated && pathname !== '/auth') {
        router.push('/auth');
      } else if (isAuthenticated && pathname === '/auth') {
        router.push('/');
      }
    }
  }, [isAuthenticated, pathname, router, mounted]);

  // Prevent layout flicker on mount
  if (!mounted) return <div className={layoutStyles.layoutWrapper} style={{ background: '#0c101a' }} />;

  const isAuthPage = pathname === '/auth';

  if (isAuthPage) {
    return <>{children}</>;
  }

  return (
    <div className={layoutStyles.layoutWrapper}>
      <div className={`${layoutStyles.sidebar} ${!sidebarOpen ? layoutStyles.sidebarHidden : ''}`}>
        <Sidebar />
      </div>
      <div className={layoutStyles.mainContent}>
        <Header />
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {children}
        </div>
      </div>
    </div>
  );
}
