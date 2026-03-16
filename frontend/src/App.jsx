import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './hooks/useAuth';
import LoginPage from './pages/LoginPage';
import AdminLayout from './layouts/AdminLayout';
import AdminOverview from './pages/admin/Overview';
import Machines from './pages/admin/Machines';
import FoundKeys from './pages/admin/FoundKeys';
import Connect from './pages/admin/Connect';
import AdminSettings from './pages/admin/Settings';
import Analytics from './pages/admin/Analytics';
import GateMonitor from './pages/admin/GateMonitor';
import LoadingSpinner from './components/LoadingSpinner';

function ProtectedRoute({ children }) {
  const { user, loading, verified } = useAuth();
  if (loading) return <LoadingSpinner fullPage />;
  if (!user) return <Navigate to="/login" replace />;
  if (!verified) return <LoadingSpinner fullPage />;
  return children;
}

function RootRedirect() {
  const { user, loading } = useAuth();
  if (loading) return <LoadingSpinner fullPage />;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to="/admin" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<RootRedirect />} />
      <Route
        path="/admin"
        element={
          <ProtectedRoute>
            <AdminLayout />
          </ProtectedRoute>
        }
      >
        <Route index element={<AdminOverview />} />
        <Route path="machines" element={<Machines />} />
        <Route path="found-keys" element={<FoundKeys />} />
        <Route path="connect" element={<Connect />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="gate" element={<GateMonitor />} />
        <Route path="settings" element={<AdminSettings />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
