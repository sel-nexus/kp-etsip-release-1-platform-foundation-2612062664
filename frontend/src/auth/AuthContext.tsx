import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { api } from '../api';
import type { Session, User } from '../types';

interface AuthState {
  user: User | null; token: string | null; loading: boolean;
  signIn: (email: string, password: string) => Promise<User>;
  signOut: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<User>;
  changeLanding: (defaultLandingPage: User['defaultLandingPage']) => Promise<void>;
}
const AuthContext = createContext<AuthState | null>(null);
const key = 'ketsip.token';

/** Provide a database-checked session and profile operations. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(key));
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(Boolean(token));
  useEffect(() => {
    const expire = () => { setToken(null); setUser(null); };
    window.addEventListener('ketsip:session-expired', expire);
    return () => window.removeEventListener('ketsip:session-expired', expire);
  }, []);
  useEffect(() => {
    if (!token) { setUser(null); setLoading(false); return; }
    let active = true;
    setLoading(true);
    api<User>('/auth/me', {}, token).then(value => { if (active) setUser(value); }).catch(() => {
      if (active) { localStorage.removeItem(key); setToken(null); setUser(null); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [token]);
  function store(session: Session) {
    localStorage.setItem(key, session.token);
    setUser(session.user);
    setToken(session.token);
  }
  async function signIn(email: string, password: string) {
    const session = await api<Session>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) });
    store(session);
    return session.user;
  }
  async function signOut() {
    try { if (token) await api<void>('/auth/logout', { method: 'POST' }, token); }
    finally { localStorage.removeItem(key); setToken(null); setUser(null); }
  }
  async function changePassword(currentPassword: string, newPassword: string) {
    const session = await api<Session>('/auth/password', { method: 'POST', body: JSON.stringify({ currentPassword, newPassword }) }, token || undefined);
    store(session);
    return session.user;
  }
  async function changeLanding(defaultLandingPage: User['defaultLandingPage']) {
    const updated = await api<User>('/auth/landing', { method: 'PUT', body: JSON.stringify({ defaultLandingPage }) }, token || undefined);
    setUser(updated);
  }
  return <AuthContext.Provider value={{ user, token, loading, signIn, signOut, changePassword, changeLanding }}>{children}</AuthContext.Provider>;
}

/** Read authentication state within the provider. */
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('AuthProvider is required');
  return value;
}
