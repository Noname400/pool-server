import React, { useState, useEffect, useCallback } from 'react';
import { getStatsHistory, updateSettings } from '../../api/client';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, AreaChart, Area,
} from 'recharts';

function parseTS(raw) {
  if (!raw) return null;
  const s = String(raw);
  if (s.endsWith('Z') || s.includes('+')) return new Date(s);
  return new Date(s + 'Z');
}

function fmtTime(raw, includeDate = false) {
  const d = parseTS(raw);
  if (!d || isNaN(d)) return '';
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (!includeDate) return time;
  const date = d.toLocaleDateString([], { day: '2-digit', month: '2-digit' });
  return `${date} ${time}`;
}

function fmtTooltipLabel(label, items) {
  const d = parseTS(items?.[0]?.payload?.ts);
  if (!d || isNaN(d)) return label;
  return d.toLocaleString([], { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtNum(v) {
  if (v == null) return '—';
  return v.toLocaleString();
}

const COLORS = {
  completed: '#22c55e',
  inflight: '#f59e0b',
  ready_queue: '#3b82f6',
  requeued_total: '#f97316',
  found_keys: '#8b5cf6',
  machines_online: '#06b6d4',
  machines_total: '#64748b',
  step: '#ec4899',
};

export default function Analytics() {
  const [data, setData] = useState(null);
  const [enabled, setEnabled] = useState(false);
  const [hours, setHours] = useState(6);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await getStatsHistory(hours);
      setData(res.points || []);
      setEnabled(res.enabled);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  }, [hours]);

  useEffect(() => {
    load();
    const iv = setInterval(load, 30000);
    return () => clearInterval(iv);
  }, [load]);

  const toggleDebug = async () => {
    setToggling(true);
    try {
      await updateSettings({ stats_debug: enabled ? '0' : '1' });
      setEnabled(!enabled);
    } catch (e) {
      setError(e.message);
    }
    setToggling(false);
  };

  const showDate = hours > 6;
  const chartData = (data || []).map(p => ({
    ...p,
    time: fmtTime(p.ts, showDate),
  }));

  const completedDelta = chartData.length >= 2
    ? chartData[chartData.length - 1].completed - chartData[0].completed
    : 0;
  const timeSpanMin = chartData.length >= 2
    ? Math.round((parseTS(chartData[chartData.length - 1].ts) - parseTS(chartData[0].ts)) / 60000)
    : 0;
  const rate = timeSpanMin > 0 ? Math.round(completedDelta / timeSpanMin) : 0;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '0.75rem' }}>
        <h2>Analytics</h2>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          {[1, 6, 12, 24].map(h => (
            <button
              key={h}
              className={`btn btn-sm ${hours === h ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setHours(h)}
            >
              {h}h
            </button>
          ))}
          <div style={{ width: 1, height: 24, background: 'var(--border)', margin: '0 0.25rem' }} />
          <button
            className={`btn btn-sm ${enabled ? 'btn-danger' : 'btn-primary'}`}
            onClick={toggleDebug}
            disabled={toggling}
          >
            {toggling ? '...' : enabled ? 'Disable Debug' : 'Enable Debug'}
          </button>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {!enabled && !loading && (
        <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
          <p style={{ fontSize: '1.1rem', marginBottom: '0.5rem' }}>Stats collection is disabled</p>
          <p style={{ fontSize: '0.85rem' }}>Click <strong>Enable Debug</strong> to start recording snapshots every 60 seconds. Data is auto-cleaned after 24 hours.</p>
        </div>
      )}

      {loading && <p>Loading...</p>}

      {enabled && chartData.length === 0 && !loading && (
        <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
          No data yet. Snapshots appear every 60 seconds after enabling debug.
        </div>
      )}

      {enabled && chartData.length > 0 && (
        <>
          {/* Summary cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0.75rem', marginBottom: '1.5rem' }}>
            <div style={{ padding: '0.75rem', background: 'var(--bg-secondary)', borderRadius: '0.5rem' }}>
              <div className="text-muted" style={{ fontSize: '0.75rem' }}>Rate</div>
              <div style={{ fontSize: '1.1rem', fontWeight: 700, color: COLORS.completed }}>{fmtNum(rate)}/min</div>
            </div>
            <div style={{ padding: '0.75rem', background: 'var(--bg-secondary)', borderRadius: '0.5rem' }}>
              <div className="text-muted" style={{ fontSize: '0.75rem' }}>Completed (period)</div>
              <div style={{ fontSize: '1.1rem', fontWeight: 700 }}>{fmtNum(completedDelta)}</div>
            </div>
            <div style={{ padding: '0.75rem', background: 'var(--bg-secondary)', borderRadius: '0.5rem' }}>
              <div className="text-muted" style={{ fontSize: '0.75rem' }}>Data points</div>
              <div style={{ fontSize: '1.1rem', fontWeight: 700 }}>{chartData.length}</div>
            </div>
            <div style={{ padding: '0.75rem', background: 'var(--bg-secondary)', borderRadius: '0.5rem' }}>
              <div className="text-muted" style={{ fontSize: '0.75rem' }}>Time span</div>
              <div style={{ fontSize: '1.1rem', fontWeight: 700 }}>{timeSpanMin} min</div>
            </div>
          </div>

          {/* Completed + Step */}
          <div style={{ marginBottom: '2rem' }}>
            <h3 style={{ marginBottom: '0.5rem', fontSize: '0.95rem' }}>Completed over time</h3>
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="time" tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
                <YAxis tick={{ fontSize: 11 }} stroke="var(--text-muted)" tickFormatter={fmtNum} />
                <Tooltip contentStyle={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} formatter={fmtNum} labelFormatter={fmtTooltipLabel} />
                <Area type="monotone" dataKey="completed" stroke={COLORS.completed} fill={COLORS.completed} fillOpacity={0.15} strokeWidth={2} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Inflight + Ready Queue */}
          <div style={{ marginBottom: '2rem' }}>
            <h3 style={{ marginBottom: '0.5rem', fontSize: '0.95rem' }}>Queue depth</h3>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="time" tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
                <YAxis tick={{ fontSize: 11 }} stroke="var(--text-muted)" tickFormatter={fmtNum} />
                <Tooltip contentStyle={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} formatter={fmtNum} labelFormatter={fmtTooltipLabel} />
                <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
                <Line type="monotone" dataKey="ready_queue" name="Ready" stroke={COLORS.ready_queue} strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="inflight" name="Inflight" stroke={COLORS.inflight} strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="requeued_total" name="Requeued" stroke={COLORS.requeued_total} strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Machines Online */}
          <div style={{ marginBottom: '2rem' }}>
            <h3 style={{ marginBottom: '0.5rem', fontSize: '0.95rem' }}>Machines</h3>
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="time" tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
                <YAxis tick={{ fontSize: 11 }} stroke="var(--text-muted)" allowDecimals={false} />
                <Tooltip contentStyle={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} labelFormatter={fmtTooltipLabel} />
                <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
                <Line type="stepAfter" dataKey="machines_online" name="Online" stroke={COLORS.machines_online} strokeWidth={2} dot={false} />
                <Line type="stepAfter" dataKey="machines_total" name="Total" stroke={COLORS.machines_total} strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Found Keys */}
          <div style={{ marginBottom: '1rem' }}>
            <h3 style={{ marginBottom: '0.5rem', fontSize: '0.95rem' }}>Found Keys</h3>
            <ResponsiveContainer width="100%" height={160}>
              <AreaChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="time" tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
                <YAxis tick={{ fontSize: 11 }} stroke="var(--text-muted)" allowDecimals={false} />
                <Tooltip contentStyle={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} labelFormatter={fmtTooltipLabel} />
                <Area type="monotone" dataKey="found_keys" stroke={COLORS.found_keys} fill={COLORS.found_keys} fillOpacity={0.15} strokeWidth={2} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  );
}
