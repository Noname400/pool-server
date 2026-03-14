import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import * as api from '../api/client';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    try {
      const stored = localStorage.getItem('user');
      return stored ? JSON.parse(stored) : null;
    } catch {
      return null;
    }
  });
  const [loading, setLoading] = useState(true);
  const [verified, setVerified] = useState(false);

  useEffect(() => {
    api.getMe()
      .then((data) => {
        const u = data.user || data;
        setUser(u);
        localStorage.setItem('user', JSON.stringify(u));
        setVerified(true);
      })
      .catch(() => {
        setUser(null);
        localStorage.removeItem('user');
        setVerified(false);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (apiKey) => {
    try {
      const data = await api.login(apiKey);
      const u = data.user || data;
      setUser(u);
      localStorage.setItem('user', JSON.stringify(u));
      setVerified(true);
      return u;
    } catch (err) {
      localStorage.removeItem('user');
      setVerified(false);
      throw err;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // ignore
    }
    setUser(null);
    localStorage.removeItem('user');
  }, []);

  const isAdmin = user?.role === 'admin';
  const isUser = user?.role === 'user';

  return (
    <AuthContext.Provider value={{ user, loading, verified, login, logout, isAdmin, isUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
