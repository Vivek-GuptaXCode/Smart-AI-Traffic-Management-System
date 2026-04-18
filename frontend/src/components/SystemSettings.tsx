'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useAuthStore } from '@/store/useAuthStore';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Shield, Server, Wifi, Cpu, Bell, Moon, Sun, Zap, RefreshCw,
    ToggleLeft, ToggleRight, ChevronRight, Lock, Activity,
    Database, Globe, AlertCircle, CheckCircle, Sliders, Eye, EyeOff,
    Radio, BarChart3, Volume2, VolumeX
} from 'lucide-react';
import styles from './SystemSettings.module.css';

// ─── Types ───────────────────────────────────────────────────────────────────

interface SystemHealth {
    cpu: number;
    memory: number;
    network: number;
    latency: number;
}

interface ToggleSetting {
    id: string;
    label: string;
    description: string;
    value: boolean;
    color: 'cyan' | 'purple' | 'green';
}

// ─── Animated Ring Gauge ───────────────────────────────────────────────────
function RingGauge({ value, label, color }: { value: number; label: string; color: string }) {
    const radius = 28;
    const stroke = 4;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference - (value / 100) * circumference;

    return (
        <div className={styles.ringGauge}>
            <svg width={72} height={72} style={{ transform: 'rotate(-90deg)' }}>
                <circle cx={36} cy={36} r={radius} fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth={stroke} />
                <circle
                    cx={36} cy={36} r={radius} fill="none"
                    stroke={color} strokeWidth={stroke}
                    strokeDasharray={circumference}
                    strokeDashoffset={offset}
                    strokeLinecap="round"
                    style={{ transition: 'stroke-dashoffset 1s ease', filter: `drop-shadow(0 0 4px ${color})` }}
                />
            </svg>
            <div className={styles.ringGaugeInner}>
                <span className={styles.ringValue} style={{ color }}>{value}%</span>
            </div>
            <span className={styles.ringLabel}>{label}</span>
        </div>
    );
}

// ─── Animated Toggle ──────────────────────────────────────────────────────
function Toggle({ enabled, onToggle, color }: { enabled: boolean; onToggle: () => void; color: string }) {
    return (
        <button
            onClick={onToggle}
            className={styles.toggleTrack}
            style={{
                background: enabled ? `${color}33` : 'rgba(255,255,255,0.05)',
                borderColor: enabled ? color : 'rgba(255,255,255,0.1)',
            }}
        >
            <motion.div
                className={styles.toggleThumb}
                animate={{ x: enabled ? 20 : 0 }}
                transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                style={{ background: enabled ? color : '#475569', boxShadow: enabled ? `0 0 8px ${color}` : 'none' }}
            />
        </button>
    );
}

// ─── Animated Slider ──────────────────────────────────────────────────────
function GlowSlider({ value, onChange, color, min = 0, max = 100, label }: {
    value: number; onChange: (v: number) => void; color: string; min?: number; max?: number; label: string;
}) {
    const pct = ((value - min) / (max - min)) * 100;
    return (
        <div className={styles.sliderRow}>
            <span className={styles.sliderLabel}>{label}</span>
            <div className={styles.sliderTrack}>
                <div className={styles.sliderFill} style={{ width: `${pct}%`, background: color, boxShadow: `0 0 8px ${color}80` }} />
                <input
                    type="range" min={min} max={max} value={value}
                    onChange={e => onChange(Number(e.target.value))}
                    className={styles.sliderInput}
                />
            </div>
            <span className={styles.sliderValue} style={{ color }}>{value}</span>
        </div>
    );
}

// ─── Animated Stat Ticker ─────────────────────────────────────────────────
function LiveTicker({ value, unit, color }: { value: number; unit: string; color: string }) {
    return (
        <span style={{ color, fontFamily: 'var(--font-geist-mono)', fontWeight: 700, fontSize: '1.1rem' }}>
            {value.toFixed(1)}<span style={{ fontSize: '0.7rem', color: '#64748b', marginLeft: 2 }}>{unit}</span>
        </span>
    );
}

// ─── Section Header ───────────────────────────────────────────────────────
function SectionHeader({ icon, title, badge }: { icon: React.ReactNode; title: string; badge?: string }) {
    return (
        <div className={styles.sectionHeader}>
            <span className={styles.sectionIcon}>{icon}</span>
            <h2 className={styles.sectionTitle}>{title}</h2>
            {badge && <span className={styles.sectionBadge}>{badge}</span>}
        </div>
    );
}

// ─── Main Component ───────────────────────────────────────────────────────
export default function SystemSettings() {
    const { user } = useAuthStore();
    const [activeTab, setActiveTab] = useState<'system' | 'network' | 'security' | 'appearance'>('system');
    const [health, setHealth] = useState<SystemHealth>({ cpu: 34, memory: 61, network: 88, latency: 14 });
    const [uptime, setUptime] = useState(0);
    const [showSessionKey, setShowSessionKey] = useState(false);
    const [audioEnabled, setAudioEnabled] = useState(true);
    const [brightness, setBrightness] = useState(80);
    const [refreshRate, setRefreshRate] = useState(5);
    const [alertThreshold, setAlertThreshold] = useState(75);
    const [scanProgress, setScanProgress] = useState(0);
    const [scanning, setScanning] = useState(false);
    const [scanResult, setScanResult] = useState<'idle' | 'clean' | 'warning'>('idle');
    const [logLevel, setLogLevel] = useState<'INFO' | 'DEBUG' | 'WARN' | 'ERROR'>('INFO');

    const [toggles, setToggles] = useState<ToggleSetting[]>([
        { id: 'realtime', label: 'Real-time Data Feed', description: 'Live streaming from V2X infrastructure', value: true, color: 'cyan' },
        { id: 'notifications', label: 'Alert Notifications', description: 'Push alerts for congestion events', value: true, color: 'cyan' },
        { id: 'ai_assist', label: 'AI Routing Assist', description: 'Predictive traffic routing recommendations', value: true, color: 'purple' },
        { id: 'darkmode', label: 'Dark Mode', description: 'Reduced emission display profile', value: true, color: 'purple' },
        { id: 'telemetry', label: 'Usage Telemetry', description: 'Anonymous diagnostics sharing', value: false, color: 'cyan' },
        { id: '2fa', label: 'Session 2FA', description: 'Enforce OTP on every login', value: true, color: 'green' },
    ]);

    // Simulate live system vitals
    const wiggle = useCallback(() => {
        setHealth(prev => ({
            cpu: Math.max(10, Math.min(95, prev.cpu + (Math.random() - 0.5) * 8)),
            memory: Math.max(30, Math.min(90, prev.memory + (Math.random() - 0.5) * 4)),
            network: Math.max(50, Math.min(100, prev.network + (Math.random() - 0.5) * 6)),
            latency: Math.max(4, Math.min(40, prev.latency + (Math.random() - 0.5) * 5)),
        }));
    }, []);

    useEffect(() => {
        const hInterval = setInterval(wiggle, 2000);
        const uInterval = setInterval(() => setUptime(u => u + 1), 1000);
        return () => { clearInterval(hInterval); clearInterval(uInterval); };
    }, [wiggle]);

    const formatUptime = (s: number) => {
        const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
        return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
    };

    const handleFlipToggle = (id: string) => {
        setToggles(prev => prev.map(t => t.id === id ? { ...t, value: !t.value } : t));
    };

    const runScan = () => {
        if (scanning) return;
        setScanning(true);
        setScanProgress(0);
        setScanResult('idle');
        let p = 0;
        const interval = setInterval(() => {
            p += Math.random() * 12;
            if (p >= 100) {
                p = 100;
                clearInterval(interval);
                setScanning(false);
                setScanResult(Math.random() > 0.2 ? 'clean' : 'warning');
            }
            setScanProgress(Math.min(100, p));
        }, 180);
    };

    const tabs = [
        { id: 'system', label: 'System', icon: <Cpu size={14} /> },
        { id: 'network', label: 'Network', icon: <Wifi size={14} /> },
        { id: 'security', label: 'Security', icon: <Shield size={14} /> },
        { id: 'appearance', label: 'Appearance', icon: <Sliders size={14} /> },
    ] as const;

    const colorMap = { cyan: '#0ffff0', purple: '#c026d3', green: '#10b981' };

    return (
        <div className={styles.container}>
            {/* ── Header ── */}
            <div className={styles.pageHeader}>
                <div>
                    <h1 className={styles.pageTitle}>System Control Panel</h1>
                    <p className={styles.pageSubtitle}>
                        <span className={styles.uptimeLabel}>SESSION UPTIME</span>
                        <span className={styles.uptimeValue}>{formatUptime(uptime)}</span>
                    </p>
                </div>
                <div className={styles.agentBadge}>
                    <Shield size={14} className={styles.agentBadgeIcon} />
                    <span>{user?.email || 'Unknown Agent'}</span>
                </div>
            </div>

            {/* ── Live Health Bar ── */}
            <div className={styles.healthBar}>
                <RingGauge value={Math.round(health.cpu)} label="CPU" color="#0ffff0" />
                <RingGauge value={Math.round(health.memory)} label="MEM" color="#c026d3" />
                <RingGauge value={Math.round(health.network)} label="NET" color="#10b981" />
                <div className={styles.latencyBox}>
                    <span className={styles.latencyLabel}>LATENCY</span>
                    <LiveTicker value={health.latency} unit="ms" color="#f59e0b" />
                    <div className={styles.latencyBar}>
                        {Array.from({ length: 12 }).map((_, i) => (
                            <div
                                key={i}
                                className={styles.latencySegment}
                                style={{
                                    height: `${20 + Math.random() * 30}px`,
                                    background: health.latency < 15 ? '#10b981' : health.latency < 30 ? '#f59e0b' : '#f43f5e',
                                    opacity: i < Math.round(health.latency / 5) ? 1 : 0.15,
                                }}
                            />
                        ))}
                    </div>
                </div>
                <div className={styles.serverStatusBox}>
                    <Radio size={14} className={styles.serverStatusIcon} style={{ color: '#10b981' }} />
                    <span className={styles.serverStatusText}>ALL NODES ONLINE</span>
                    <span className={styles.serverStatusCount}>4/4</span>
                </div>
            </div>

            {/* ── Tab Nav ── */}
            <div className={styles.tabNav}>
                {tabs.map(t => (
                    <button
                        key={t.id}
                        className={`${styles.tab} ${activeTab === t.id ? styles.tabActive : ''}`}
                        onClick={() => setActiveTab(t.id)}
                    >
                        {t.icon}
                        <span>{t.label}</span>
                        {activeTab === t.id && <motion.div className={styles.tabIndicator} layoutId="tab-indicator" />}
                    </button>
                ))}
            </div>

            {/* ── Tab Content ── */}
            <AnimatePresence mode="wait">
                <motion.div
                    key={activeTab}
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.2 }}
                    className={styles.tabContent}
                >
                    {/* ═══ SYSTEM TAB ═══ */}
                    {activeTab === 'system' && (
                        <div className={styles.grid2col}>
                            {/* Toggles */}
                            <div className={styles.card}>
                                <SectionHeader icon={<Zap size={16} />} title="Feature Controls" badge="LIVE" />
                                <div className={styles.toggleList}>
                                    {toggles.slice(0, 4).map(t => (
                                        <div key={t.id} className={styles.toggleRow}>
                                            <div className={styles.toggleInfo}>
                                                <span className={styles.toggleLabel}>{t.label}</span>
                                                <span className={styles.toggleDesc}>{t.description}</span>
                                            </div>
                                            <Toggle
                                                enabled={t.value}
                                                onToggle={() => handleFlipToggle(t.id)}
                                                color={colorMap[t.color]}
                                            />
                                        </div>
                                    ))}
                                </div>
                            </div>

                            {/* Sliders */}
                            <div className={styles.card}>
                                <SectionHeader icon={<BarChart3 size={16} />} title="Performance Tuning" />
                                <div className={styles.sliderList}>
                                    <GlowSlider
                                        label="Data Refresh Rate"
                                        value={refreshRate} onChange={setRefreshRate}
                                        color="#0ffff0" min={1} max={30}
                                    />
                                    <GlowSlider
                                        label="Alert Threshold %"
                                        value={alertThreshold} onChange={setAlertThreshold}
                                        color="#c026d3"
                                    />
                                    <GlowSlider
                                        label="Display Brightness"
                                        value={brightness} onChange={setBrightness}
                                        color="#f59e0b"
                                    />
                                </div>

                                {/* Log level picker */}
                                <div className={styles.logLevelSection}>
                                    <span className={styles.sliderLabel}>Log Level</span>
                                    <div className={styles.logLevelPicker}>
                                        {(['INFO', 'DEBUG', 'WARN', 'ERROR'] as const).map(lvl => {
                                            const colors = { INFO: '#0ffff0', DEBUG: '#94a3b8', WARN: '#f59e0b', ERROR: '#f43f5e' };
                                            return (
                                                <button
                                                    key={lvl}
                                                    onClick={() => setLogLevel(lvl)}
                                                    className={styles.logLevelBtn}
                                                    style={{
                                                        borderColor: logLevel === lvl ? colors[lvl] : 'transparent',
                                                        color: logLevel === lvl ? colors[lvl] : '#64748b',
                                                        background: logLevel === lvl ? `${colors[lvl]}15` : 'transparent',
                                                    }}
                                                >{lvl}</button>
                                            );
                                        })}
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* ═══ NETWORK TAB ═══ */}
                    {activeTab === 'network' && (
                        <div className={styles.grid2col}>
                            <div className={styles.card}>
                                <SectionHeader icon={<Globe size={16} />} title="Connection Status" badge="LIVE" />
                                <div className={styles.connList}>
                                    {[
                                        { name: 'V2X Core Server', host: '127.0.0.1:5000', status: 'ONLINE', latency: health.latency },
                                        { name: 'Auth API', host: 'localhost:3001', status: 'ONLINE', latency: Math.round(health.latency * 0.4) },
                                        { name: 'SUMO Simulator', host: 'localhost:8813', status: toggles[0].value ? 'ONLINE' : 'STANDBY', latency: Math.round(health.latency * 1.2) },
                                        { name: 'Analytics Engine', host: '127.0.0.1:5001', status: 'ONLINE', latency: Math.round(health.latency * 0.7) },
                                    ].map((conn, i) => (
                                        <div key={i} className={styles.connRow}>
                                            <div className={styles.connDot} style={{ background: conn.status === 'ONLINE' ? '#10b981' : '#f59e0b', boxShadow: `0 0 6px ${conn.status === 'ONLINE' ? '#10b981' : '#f59e0b'}` }} />
                                            <div className={styles.connInfo}>
                                                <span className={styles.connName}>{conn.name}</span>
                                                <span className={styles.connHost}>{conn.host}</span>
                                            </div>
                                            <span className={styles.connLatency}>{conn.latency}ms</span>
                                            <span className={styles.connStatus} style={{ color: conn.status === 'ONLINE' ? '#10b981' : '#f59e0b' }}>{conn.status}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className={styles.card}>
                                <SectionHeader icon={<Activity size={16} />} title="Traffic Bandwidth" />
                                <div className={styles.bwChart}>
                                    {Array.from({ length: 20 }).map((_, i) => {
                                        const h = 20 + Math.sin(i * 0.8 + Date.now() * 0.001) * 20 + Math.random() * 15;
                                        return (
                                            <div key={i} className={styles.bwBar} style={{ height: `${Math.max(8, h)}px` }} />
                                        );
                                    })}
                                </div>
                                <div className={styles.bwStats}>
                                    <div className={styles.bwStat}>
                                        <span className={styles.bwStatLabel}>↑ UPLOAD</span>
                                        <LiveTicker value={health.network * 0.4} unit="Kbps" color="#0ffff0" />
                                    </div>
                                    <div className={styles.bwStat}>
                                        <span className={styles.bwStatLabel}>↓ DOWNLOAD</span>
                                        <LiveTicker value={health.network * 1.1} unit="Kbps" color="#c026d3" />
                                    </div>
                                    <div className={styles.bwStat}>
                                        <span className={styles.bwStatLabel}>PACKETS/S</span>
                                        <LiveTicker value={health.network * 2.3} unit="pkt" color="#f59e0b" />
                                    </div>
                                </div>
                                <div className={styles.toggleRow} style={{ marginTop: 20, borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 16 }}>
                                    <div className={styles.toggleInfo}>
                                        <span className={styles.toggleLabel}>WebSocket Streaming</span>
                                        <span className={styles.toggleDesc}>Real-time event delivery via WS</span>
                                    </div>
                                    <Toggle enabled={toggles[0].value} onToggle={() => handleFlipToggle('realtime')} color="#0ffff0" />
                                </div>
                            </div>
                        </div>
                    )}

                    {/* ═══ SECURITY TAB ═══ */}
                    {activeTab === 'security' && (
                        <div className={styles.grid2col}>
                            <div className={styles.card}>
                                <SectionHeader icon={<Shield size={16} />} title="Security Scan" />
                                <div className={styles.scanArea}>
                                    <button onClick={runScan} disabled={scanning} className={styles.scanButton}>
                                        {scanning ? <RefreshCw size={16} className={styles.spinIcon} /> : <Shield size={16} />}
                                        {scanning ? 'Scanning…' : 'Run Security Scan'}
                                    </button>

                                    {(scanning || scanResult !== 'idle') && (
                                        <div className={styles.scanProgress}>
                                            <div className={styles.scanBar}>
                                                <div
                                                    className={styles.scanFill}
                                                    style={{
                                                        width: `${scanProgress}%`,
                                                        background: scanResult === 'warning' ? '#f43f5e' : '#10b981',
                                                    }}
                                                />
                                            </div>
                                            <span className={styles.scanPct}>{Math.round(scanProgress)}%</span>
                                        </div>
                                    )}

                                    <AnimatePresence>
                                        {scanResult === 'clean' && (
                                            <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className={styles.scanResultGreen}>
                                                <CheckCircle size={16} /> All Clear — No Threats Detected
                                            </motion.div>
                                        )}
                                        {scanResult === 'warning' && (
                                            <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className={styles.scanResultRed}>
                                                <AlertCircle size={16} /> Warning — Anomalous Pattern Detected
                                            </motion.div>
                                        )}
                                    </AnimatePresence>
                                </div>

                                <div className={styles.toggleList} style={{ marginTop: 20 }}>
                                    {toggles.slice(4).map(t => (
                                        <div key={t.id} className={styles.toggleRow}>
                                            <div className={styles.toggleInfo}>
                                                <span className={styles.toggleLabel}>{t.label}</span>
                                                <span className={styles.toggleDesc}>{t.description}</span>
                                            </div>
                                            <Toggle enabled={t.value} onToggle={() => handleFlipToggle(t.id)} color={colorMap[t.color]} />
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className={styles.card}>
                                <SectionHeader icon={<Lock size={16} />} title="Session Info" />
                                <div className={styles.sessionInfo}>
                                    <div className={styles.sessionRow}>
                                        <span className={styles.sessionKey}>Agent</span>
                                        <span className={styles.sessionVal}>{user?.email || 'Unknown'}</span>
                                    </div>
                                    <div className={styles.sessionRow}>
                                        <span className={styles.sessionKey}>Role</span>
                                        <span className={styles.sessionVal} style={{ color: '#0ffff0' }}>AUTHORIZED OPERATOR</span>
                                    </div>
                                    <div className={styles.sessionRow}>
                                        <span className={styles.sessionKey}>Token</span>
                                        <span className={styles.sessionVal} style={{ fontFamily: 'monospace', letterSpacing: '0.1em' }}>
                                            {showSessionKey ? 'eyJhbGci...V2XPrivKey' : '••••••••••••••••••••'}
                                        </span>
                                        <button onClick={() => setShowSessionKey(p => !p)} className={styles.eyeBtn}>
                                            {showSessionKey ? <EyeOff size={14} /> : <Eye size={14} />}
                                        </button>
                                    </div>
                                    <div className={styles.sessionRow}>
                                        <span className={styles.sessionKey}>Uptime</span>
                                        <span className={styles.sessionVal} style={{ color: '#10b981', fontFamily: 'monospace' }}>{formatUptime(uptime)}</span>
                                    </div>
                                    <div className={styles.sessionRow}>
                                        <span className={styles.sessionKey}>Encryption</span>
                                        <span className={styles.sessionVal} style={{ color: '#10b981' }}>AES-256 + JWT RS256</span>
                                    </div>
                                    <div className={styles.sessionRow}>
                                        <span className={styles.sessionKey}>Last Login</span>
                                        <span className={styles.sessionVal}>{new Date().toLocaleString()}</span>
                                    </div>
                                </div>
                                <div className={styles.threatLevel}>
                                    <span className={styles.threatLabel}>THREAT LEVEL</span>
                                    <div className={styles.threatBlocks}>
                                        {['LOW', 'MED', 'HIGH', 'CRITICAL'].map((t, i) => (
                                            <div key={t} className={styles.threatBlock} style={{ background: i === 0 ? '#10b981' : 'rgba(255,255,255,0.05)', color: i === 0 ? '#fff' : '#475569' }}>
                                                {t}
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* ═══ APPEARANCE TAB ═══ */}
                    {activeTab === 'appearance' && (
                        <div className={styles.grid2col}>
                            <div className={styles.card}>
                                <SectionHeader icon={<Sun size={16} />} title="Display Settings" />
                                <div className={styles.sliderList}>
                                    <GlowSlider label="Brightness" value={brightness} onChange={setBrightness} color="#f59e0b" />
                                </div>

                                <div className={styles.themeSection}>
                                    <span className={styles.sliderLabel}>Accent Color</span>
                                    <div className={styles.colorPalette}>
                                        {['#0ffff0', '#c026d3', '#10b981', '#f59e0b', '#3b82f6', '#f43f5e'].map(c => (
                                            <button
                                                key={c}
                                                className={styles.colorSwatch}
                                                style={{ background: c, boxShadow: `0 0 12px ${c}80` }}
                                            />
                                        ))}
                                    </div>
                                </div>

                                <div className={styles.toggleList} style={{ marginTop: 16 }}>
                                    <div className={styles.toggleRow}>
                                        <div className={styles.toggleInfo}>
                                            <span className={styles.toggleLabel}>Dark Mode</span>
                                            <span className={styles.toggleDesc}>Enable dark tactical display</span>
                                        </div>
                                        <Toggle enabled={toggles.find(t => t.id === 'darkmode')!.value} onToggle={() => handleFlipToggle('darkmode')} color="#c026d3" />
                                    </div>
                                    <div className={styles.toggleRow}>
                                        <div className={styles.toggleInfo}>
                                            {audioEnabled ? <Volume2 size={14} style={{ display: 'inline', marginRight: 4, color: '#0ffff0' }} /> : <VolumeX size={14} style={{ display: 'inline', marginRight: 4, color: '#64748b' }} />}
                                            <span className={styles.toggleLabel}>Alert Audio</span>
                                            <span className={styles.toggleDesc}>Sound cues for critical alerts</span>
                                        </div>
                                        <Toggle enabled={audioEnabled} onToggle={() => setAudioEnabled(p => !p)} color="#0ffff0" />
                                    </div>
                                </div>
                            </div>

                            <div className={styles.card}>
                                <SectionHeader icon={<Database size={16} />} title="Data Preferences" />
                                <div className={styles.sliderList}>
                                    <GlowSlider
                                        label="Refresh Rate (s)"
                                        value={refreshRate} onChange={setRefreshRate}
                                        color="#0ffff0" min={1} max={30}
                                    />
                                </div>

                                <div className={styles.prefsGrid}>
                                    {[
                                        { label: 'Events Stored', value: '50', unit: 'max', color: '#0ffff0' },
                                        { label: 'Cache Size', value: '12', unit: 'MB', color: '#c026d3' },
                                        { label: 'Graph TTL', value: '5', unit: 'sec', color: '#10b981' },
                                        { label: 'API Timeout', value: '10', unit: 'sec', color: '#f59e0b' },
                                    ].map((p, i) => (
                                        <div key={i} className={styles.prefCard}>
                                            <span className={styles.prefLabel}>{p.label}</span>
                                            <span className={styles.prefValue} style={{ color: p.color }}>{p.value}<span className={styles.prefUnit}>{p.unit}</span></span>
                                        </div>
                                    ))}
                                </div>

                                <div className={styles.toggleRow} style={{ marginTop: 20, borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 16 }}>
                                    <div className={styles.toggleInfo}>
                                        <span className={styles.toggleLabel}>Telemetry Reporting</span>
                                        <span className={styles.toggleDesc}>Send anonymous diagnostics</span>
                                    </div>
                                    <Toggle enabled={toggles.find(t => t.id === 'telemetry')!.value} onToggle={() => handleFlipToggle('telemetry')} color="#0ffff0" />
                                </div>
                            </div>
                        </div>
                    )}
                </motion.div>
            </AnimatePresence>
        </div>
    );
}
