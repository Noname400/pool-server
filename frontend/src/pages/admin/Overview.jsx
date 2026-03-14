import React, { useState, useEffect, useMemo } from 'react';
import { getStats, getMachines } from '../../api/client';
import StatCard from '../../components/StatCard';

function timeSince(iso) {
  if (!iso) return '—';
  const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

const SEV = { critical: 0, warning: 1, info: 2 };
const SEV_COLORS = { critical: 'var(--red)', warning: 'var(--yellow)', info: 'var(--text-secondary)' };
const SEV_LABELS = { critical: 'CRIT', warning: 'WARN', info: 'INFO' };

export default function Overview() {
  const [stats, setStats] = useState(null);
  const [machines, setMachines] = useState([]);
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      const [s, m] = await Promise.all([getStats(), getMachines()]);
      setStats(s);
      setMachines(m);
      setError(null);
    } catch (e) {
      if (e.message?.includes('401')) return;
      setError(e.message);
    }
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const problems = useMemo(() => {
    const list = [];
    const byIp = new Map();

    machines.forEach(m => {
      const ip = m.ip || 'unknown';
      if (!byIp.has(ip)) byIp.set(ip, []);
      byIp.get(ip).push(m);
    });

    byIp.forEach((gpus, ip) => {
      const offline = gpus.filter(g => !g.online);
      const unverified = gpus.filter(g => g.online && !g.verified);
      const hostname = gpus[0]?.hostname || gpus[0]?.name || ip;

      if (offline.length === gpus.length && gpus.length > 0) {
        list.push({
          severity: 'critical',
          server: hostname,
          ip,
          message: `All ${gpus.length} GPUs offline`,
          detail: `Last seen: ${timeSince(gpus[0]?.last_seen)}`,
          gpuCount: gpus.length,
        });
      } else if (offline.length > 0) {
        list.push({
          severity: 'warning',
          server: hostname,
          ip,
          message: `${offline.length}/${gpus.length} GPUs offline`,
          detail: offline.map(g => g.machine_id?.slice(-8)).join(', '),
          gpuCount: offline.length,
        });
      }

      if (unverified.length > 0) {
        list.push({
          severity: 'warning',
          server: hostname,
          ip,
          message: `${unverified.length} GPU${unverified.length > 1 ? 's' : ''} unverified`,
          detail: 'Waiting for seed verification',
          gpuCount: unverified.length,
        });
      }
    });

    const totalOnline = machines.filter(m => m.online).length;
    const totalMachines = machines.length;
    if (totalMachines > 0 && totalOnline === 0) {
      list.push({
        severity: 'critical',
        server: 'FLEET',
        ip: '—',
        message: 'Entire fleet offline',
        detail: `${totalMachines} machines, 0 online`,
        gpuCount: totalMachines,
      });
    } else if (totalMachines > 0 && totalOnline / totalMachines < 0.5) {
      list.push({
        severity: 'warning',
        server: 'FLEET',
        ip: '—',
        message: `Low availability: ${totalOnline}/${totalMachines} online (${Math.round(totalOnline / totalMachines * 100)}%)`,
        detail: 'More than half the fleet is down',
        gpuCount: totalMachines - totalOnline,
      });
    }

    const noGpuInfo = machines.filter(m => !m.gpu_name || m.gpu_name === 'Unknown GPU');
    if (noGpuInfo.length > 0) {
      list.push({
        severity: 'info',
        server: '—',
        ip: '—',
        message: `${noGpuInfo.length} machine${noGpuInfo.length > 1 ? 's' : ''} missing GPU info`,
        detail: noGpuInfo.slice(0, 5).map(m => m.hostname || m.machine_id?.slice(-8)).join(', '),
        gpuCount: noGpuInfo.length,
      });
    }

    list.sort((a, b) => SEV[a.severity] - SEV[b.severity] || b.gpuCount - a.gpuCount);
    return list;
  }, [machines]);

  if (error) return <div className="alert alert-error">{error}</div>;
  if (!stats) return <p>Loading...</p>;

  const totalSpace = BigInt(2) ** BigInt(32) - BigInt(1);
  const stepBig = BigInt(stats.step || 0);
  const progress = totalSpace > 0n ? Number((stepBig * 10000n) / totalSpace) / 100 : 0;

  const healthPct = stats.machines_total > 0
    ? Math.round(stats.machines_online / stats.machines_total * 100)
    : 0;

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>Pool Overview</h2>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', marginBottom: '2rem' }}>
        <StatCard label="Machines Online" value={stats.machines_online} color="#22c55e" />
        <StatCard label="Machines Total" value={stats.machines_total} />
        <StatCard label="Inflight" value={(stats.inflight || 0).toLocaleString()} color="#f59e0b" />
        <StatCard label="Ready Queue" value={(stats.ready_queue || 0).toLocaleString()} color="#3b82f6" />
        <StatCard label="Completed" value={(stats.completed || 0).toLocaleString()} />
        <StatCard label="Requeued" value={(stats.requeued_total || 0).toLocaleString()} color="#f97316" />
        <StatCard label="Found Keys" value={stats.found_keys} color="#8b5cf6" />
        <StatCard label="Progress" value={`${progress.toFixed(4)}%`} />
      </div>

      <div style={{ marginBottom: '2rem' }}>
        <h3 style={{ marginBottom: '0.75rem' }}>Generator</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
          <div style={{ padding: '1rem', background: 'var(--bg-secondary)', borderRadius: '0.5rem' }}>
            <div className="text-muted" style={{ fontSize: '0.85rem' }}>Current Step</div>
            <div style={{ fontSize: '1.2rem', fontWeight: 600 }}>{(stats.step || 0).toLocaleString()}</div>
          </div>
          <div style={{ padding: '1rem', background: 'var(--bg-secondary)', borderRadius: '0.5rem' }}>
            <div className="text-muted" style={{ fontSize: '0.85rem' }}>Start Boundary</div>
            <div style={{ fontSize: '1.2rem', fontWeight: 600 }}>{(stats.start || 0).toLocaleString()}</div>
          </div>
        </div>
      </div>

      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem' }}>
          <h3 style={{ margin: 0 }}>Problems</h3>
          {problems.length === 0 ? (
            <span style={{ fontSize: '0.85rem', color: 'var(--green)', fontWeight: 600 }}>All clear</span>
          ) : (
            <span style={{
              fontSize: '0.75rem', padding: '2px 8px', borderRadius: '10px',
              background: problems.some(p => p.severity === 'critical') ? 'rgba(239,68,68,0.15)' : 'rgba(234,179,8,0.15)',
              color: problems.some(p => p.severity === 'critical') ? 'var(--red)' : 'var(--yellow)',
              fontWeight: 700,
            }}>
              {problems.length}
            </span>
          )}
        </div>

        {problems.length > 0 && (
          <div style={{ overflow: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <th style={{ textAlign: 'left', padding: '0.5rem 0.5rem', width: '55px', fontSize: '0.8rem' }}>Sev</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8rem' }}>Server</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8rem' }}>IP</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8rem' }}>Problem</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8rem' }}>Detail</th>
                </tr>
              </thead>
              <tbody>
                {problems.map((p, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '0.5rem', fontSize: '0.75rem', fontWeight: 700, color: SEV_COLORS[p.severity] }}>
                      {SEV_LABELS[p.severity]}
                    </td>
                    <td style={{ padding: '0.5rem', fontSize: '0.85rem', fontWeight: 500 }}>
                      {p.server}
                    </td>
                    <td style={{ padding: '0.5rem', fontFamily: 'monospace', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                      {p.ip}
                    </td>
                    <td style={{ padding: '0.5rem', fontSize: '0.85rem' }}>
                      {p.message}
                    </td>
                    <td style={{ padding: '0.5rem', fontSize: '0.8rem', color: 'var(--text-secondary)', maxWidth: '250px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {p.detail}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
