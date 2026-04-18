'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/store/useAuthStore';
import api from '@/utils/api';
import { Shield, Mail, Lock, KeyRound, ArrowRight, Loader2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import styles from './Auth.module.css';

const decodeToken = (token: string) => {
    try {
        const base64Url = token.split('.')[1];
        const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
        const jsonPayload = decodeURIComponent(atob(base64).split('').map(function (c) {
            return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
        return JSON.parse(jsonPayload);
    } catch (e) {
        return null;
    }
};

export default function AuthPage() {
    const [isLogin, setIsLogin] = useState(true);
    const [isVerifying, setIsVerifying] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [otp, setOtp] = useState('');

    const { setAuth } = useAuthStore();
    const router = useRouter();

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError('');
        try {
            const res = await api.post('/auth/login', { email, password });
            const { accessToken } = res.data;
            const decoded = decodeToken(accessToken);
            const user = { id: decoded?.id || 1, email: decoded?.email || email };
            setAuth(user, accessToken);
            router.push('/');
        } catch (err: any) {
            setError(err.response?.data?.message || 'Login failed');
        } finally {
            setLoading(false);
        }
    };

    const handleRegister = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError('');
        try {
            await api.post('/auth/register', { email, password });
            setIsVerifying(true);
            setSuccess('OTP sent! Check server console.');
        } catch (err: any) {
            setError(err.response?.data?.message || 'Registration failed');
        } finally {
            setLoading(false);
        }
    };

    const handleVerifyOtp = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError('');
        try {
            await api.post('/auth/verify-otp', { email, otp, password });
            setSuccess('Account created! You can now login.');
            setIsVerifying(false);
            setIsLogin(true);
            setOtp('');
        } catch (err: any) {
            setError(err.response?.data?.message || 'Verification failed');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className={styles.authContainer}>
            <div className={styles.bgElement1} />
            <div className={styles.bgElement2} />

            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className={styles.mountNode}
            >
                <div className={styles.logoWrapper}>
                    <div className={styles.logo}>
                        <Shield className="text-white w-8 h-8" />
                    </div>
                </div>

                <div className={styles.authCard}>
                    <div className={styles.cardHeader}>
                        <h1 className={styles.title}>
                            {isVerifying ? 'Verify Identity' : (isLogin ? 'Mission Access' : 'Recruit Base')}
                        </h1>
                        <p className={styles.subtitle}>
                            {isVerifying ? 'Enter the security code to proceed' : (isLogin ? 'Authorized personnel only' : 'Create your security credentials')}
                        </p>
                    </div>

                    <AnimatePresence mode="wait">
                        {isVerifying ? (
                            <motion.form
                                key="otp"
                                initial={{ opacity: 0, x: 20 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: -20 }}
                                onSubmit={handleVerifyOtp}
                                className={styles.form}
                            >
                                <div className={styles.inputGroup}>
                                    <label className={styles.label}>Secure OTP</label>
                                    <div className={styles.inputWrapper}>
                                        <KeyRound className={styles.inputIcon} />
                                        <input
                                            type="text"
                                            placeholder="Enter 6-digit code"
                                            className={`${styles.input} ${styles.otpInput}`}
                                            value={otp}
                                            onChange={(e) => setOtp(e.target.value)}
                                            required
                                        />
                                    </div>
                                </div>
                                <button
                                    disabled={loading}
                                    className={`${styles.submitButton} ${styles.registerButton}`}
                                >
                                    {loading ? <Loader2 className="animate-spin" /> : 'Authorize Access'}
                                    {!loading && <ArrowRight className={styles.arrowIcon} />}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setIsVerifying(false)}
                                    className={styles.switchButton}
                                >
                                    Cancel
                                </button>
                            </motion.form>
                        ) : (
                            <motion.form
                                key={isLogin ? 'login' : 'register'}
                                initial={{ opacity: 0, x: isLogin ? -20 : 20 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: isLogin ? 20 : -20 }}
                                onSubmit={isLogin ? handleLogin : handleRegister}
                                className={styles.form}
                            >
                                <div className={styles.inputGroup}>
                                    <label className={styles.label}>Email Terminal</label>
                                    <div className={styles.inputWrapper}>
                                        <Mail className={styles.inputIcon} />
                                        <input
                                            type="email"
                                            placeholder="name@agency.com"
                                            className={styles.input}
                                            value={email}
                                            onChange={(e) => setEmail(e.target.value)}
                                            required
                                        />
                                    </div>
                                </div>
                                <div className={styles.inputGroup}>
                                    <label className={styles.label}>Security Key</label>
                                    <div className={styles.inputWrapper}>
                                        <Lock className={styles.inputIcon} />
                                        <input
                                            type="password"
                                            placeholder="••••••••"
                                            className={styles.input}
                                            value={password}
                                            onChange={(e) => setPassword(e.target.value)}
                                            required
                                        />
                                    </div>
                                </div>

                                {error && <p className={styles.errorMessage}>{error}</p>}
                                {success && <p className={styles.successMessage}>{success}</p>}

                                <button
                                    disabled={loading}
                                    className={isLogin ? styles.submitButton : `${styles.submitButton} ${styles.registerButton}`}
                                >
                                    {loading ? <Loader2 className="animate-spin" /> : (isLogin ? 'Initiate Link' : 'Generate Identity')}
                                    {!loading && <ArrowRight className={styles.arrowIcon} />}
                                </button>

                                <div style={{ textAlign: 'center' }}>
                                    <button
                                        type="button"
                                        onClick={() => { setIsLogin(!isLogin); setError(''); setSuccess(''); }}
                                        className={styles.switchButton}
                                    >
                                        {isLogin ? "Requirement: New Identity?" : "Authorized already? Identity Link"}
                                    </button>
                                </div>
                            </motion.form>
                        )}
                    </AnimatePresence>
                </div>
            </motion.div>
        </div>
    );
}
