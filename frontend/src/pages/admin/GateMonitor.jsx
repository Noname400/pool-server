import React, { useState, useEffect } from 'react';
import { getGateStats } from '../../api/client';

function fmtBytes(b) {
  if (!b) return '0 B';
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1048576).toFixed(1)} MB`;
}

function fmtUptime(sec) {
  if (!sec) return '—';
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
}

export default function GateMonitor() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      setStats(await getGateStats());
      setError(null);
    } catch (e) {
      if (!e.message?.includes('401')) setError(e.message);
    }
  };

  useEffect(() => {
    load();
    const iv = setInterval(load, 3000);
    return () => clearInterval(iv);
  }, []);

  if (error) return <div className="card" style={{ color: 'var(--red)' }}>Error: {error}</div>;
  if (!stats) return <div className="card">Loading...</div>;

  const total = stats.get_task + stats.task_done + stats.chunk;
  const alive = stats.last_seen && (Date.now() / 1000 - stats.last_seen) < 30;

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>Gate Monitor</h2>

      <div style={{
        display: 'inline-block',
        padding: '4px 12px',
        borderRadius: '12px',
        fontSize: '0.85rem',
        fontWeight: 600,
        marginBottom: '1.5rem',
        background: alive ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
        color: alive ? '#22c55e' : '#ef4444',
      }}>
        {alive ? 'ALIVE' : 'OFFLINE'}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', marginBottom: '2rem' }}>
        <Card label="Uptime" value={fmtUptime(stats.uptime_sec)} />
        <Card label="Machines" value={stats.machines} />
        <Card label="RPS" value={stats.rps} />
        <Card label="Total requests" value={total.toLocaleString()} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', marginBottom: '2rem' }}>
        <Card label="get_task" value={stats.get_task.toLocaleString()} color="#3b82f6" />
        <Card label="task_done" value={stats.task_done.toLocaleString()} color="#22c55e" />
        <Card label="chunk" value={stats.chunk.toLocaleString()} color="#f59e0b" />
        <Card label="Bytes sent" value={fmtBytes(stats.bytes_sent)} color="#8b5cf6" />
      </div>
    </div>
  );
}

function Card({ label, value, color }) {
  return (
    <div className="card" style={{ padding: '1rem 1.25rem' }}>
      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '0.35rem' }}>{label}</div>
      <div style={{ fontSize: '1.6rem', fontWeight: 700, color: color || 'var(--text-primary)' }}>{value}</div>
    </div>
  );
}
