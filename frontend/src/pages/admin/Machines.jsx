import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { getMachines, machineCommand, getMachineDetail } from '../../api/client';
import Modal from '../../components/Modal';

const DOT = 14;

function MachineDetailModal({ machineId, onClose }) {
  const [machine, setMachine] = useState(null);
  const [cmdResult, setCmdResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!machineId) return;
    getMachineDetail(machineId).then(setMachine).catch(e => setError(e.message));
  }, [machineId]);

  const runCommand = async (cmd) => {
    setCmdResult(null);
    try {
      const res = await machineCommand(machineId, cmd);
      setCmdResult(res);
    } catch (e) { setCmdResult({ error: e.message }); }
  };

  if (error) return <Modal title="Error" onClose={onClose}><p className="text-red">{error}</p></Modal>;
  if (!machine) return <Modal title="Loading..." onClose={onClose}><p>Loading...</p></Modal>;

  return (
    <Modal title={`GPU · ${machine.machine_id.slice(0, 12)}`} onClose={onClose} size="lg">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
        <div>
          <div className="text-muted text-xs">Machine ID</div>
          <div className="text-mono text-xs" style={{ wordBreak: 'break-all' }}>{machine.machine_id}</div>
        </div>
        <div>
          <div className="text-muted text-xs">Hostname</div>
          <div>{machine.hostname || '—'}</div>
        </div>
        <div>
          <div className="text-muted text-xs">IP</div>
          <div className="text-mono">{machine.ip || '—'}</div>
        </div>
        <div>
          <div className="text-muted text-xs">GPU</div>
          <div>{machine.gpu_name ? `${machine.gpu_name} ×${machine.gpu_count || 1}` : '—'}</div>
        </div>
        <div>
          <div className="text-muted text-xs">VRAM</div>
          <div>{machine.gpu_mem_mb ? `${machine.gpu_mem_mb} MB` : '—'}</div>
        </div>
        <div>
          <div className="text-muted text-xs">Version</div>
          <div className="text-mono text-xs">{machine.version || '—'}</div>
        </div>
        <div>
          <div className="text-muted text-xs">Status</div>
          <div style={{ color: machine.online ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>
            {machine.online ? '● Online' : '● Offline'}
          </div>
        </div>
        <div>
          <div className="text-muted text-xs">Verified</div>
          <div style={{ color: machine.verified ? 'var(--green)' : 'var(--yellow)', fontWeight: 700 }}>
            {machine.verified ? '✓ Yes' : '⏳ Pending'}
          </div>
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <div className="text-muted text-xs">Last seen</div>
          <div>{machine.last_seen ? new Date(machine.last_seen).toLocaleString() : '—'}</div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button className="btn btn-secondary btn-sm" onClick={() => runCommand('restart')}>Restart</button>
        <button className="btn btn-danger btn-sm" onClick={() => runCommand('stop')}>Stop</button>
        <button className="btn btn-secondary btn-sm" onClick={() => runCommand('pause')}>Pause</button>
      </div>
      {cmdResult && (
        <pre style={{ background: 'var(--bg-primary)', padding: '0.6rem', borderRadius: 'var(--radius-sm)', fontSize: '0.75rem', maxHeight: '120px', overflow: 'auto', whiteSpace: 'pre-wrap', border: '1px solid var(--border)', marginTop: '0.75rem' }}>
          {cmdResult.message || cmdResult.error || JSON.stringify(cmdResult, null, 2)}
        </pre>
      )}
    </Modal>
  );
}

function serverScore(gpus) {
  let score = 0;
  for (const m of gpus) {
    if (!m.online) score += 10;
    else if (!m.verified) score += 5;
  }
  return score;
}

export default function Machines() {
  const [machines, setMachines] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('problems');
  const [tooltip, setTooltip] = useState(null);

  const load = useCallback(async () => {
    try { setMachines(await getMachines()); }
    catch (e) { setError(e.message); }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, [load]);

  const machineMap = useMemo(() => {
    const m = {};
    machines.forEach(x => { m[x.machine_id] = x; });
    return m;
  }, [machines]);

  const totalOnline = useMemo(() => machines.filter(m => m.online).length, [machines]);
  const totalOffline = machines.length - totalOnline;
  const totalUnverified = useMemo(() => machines.filter(m => m.online && !m.verified).length, [machines]);
  const totalGpus = useMemo(() => machines.reduce((s, m) => s + (m.gpu_count || 1), 0), [machines]);

  const servers = useMemo(() => {
    const byIp = new Map();
    machines.forEach(m => {
      const ip = m.ip || 'unknown';
      if (!byIp.has(ip)) byIp.set(ip, []);
      byIp.get(ip).push(m);
    });

    let entries = [...byIp.entries()].map(([ip, gpus]) => {
      gpus.sort((a, b) => {
        if (a.online !== b.online) return b.online ? 1 : -1;
        if (a.verified !== b.verified) return a.verified ? -1 : 1;
        return (a.machine_id || '').localeCompare(b.machine_id || '');
      });

      const totalCards = gpus.reduce((s, g) => s + (g.gpu_count || 1), 0);
      const onCount = gpus.filter(g => g.online).reduce((s, g) => s + (g.gpu_count || 1), 0);
      const offCount = totalCards - onCount;
      const unvCount = gpus.filter(g => g.online && !g.verified).length;
      const gpuName = gpus.find(g => g.gpu_name)?.gpu_name || '—';
      const hostname = gpus[0]?.hostname || gpus[0]?.machine_id?.slice(0, 12) || ip;
      const score = serverScore(gpus);
      const gpuUnits = gpus.flatMap(g => Array(g.gpu_count || 1).fill(g));

      return { ip, gpus, gpuUnits, totalCards, onCount, offCount, unvCount, gpuName, hostname, score };
    });

    if (filter === 'online') entries = entries.filter(s => s.onCount > 0);
    if (filter === 'offline') entries = entries.filter(s => s.offCount > 0);
    if (filter === 'unverified') entries = entries.filter(s => s.unvCount > 0);

    if (search) {
      const q = search.toLowerCase();
      entries = entries.filter(s =>
        s.ip.includes(q) ||
        s.hostname.toLowerCase().includes(q) ||
        s.gpuName.toLowerCase().includes(q) ||
        s.gpus.some(g => (g.machine_id || '').toLowerCase().includes(q))
      );
    }

    const stable = (a, b) => a.ip.localeCompare(b.ip, undefined, { numeric: true }) || a.hostname.localeCompare(b.hostname);
    if (sortBy === 'problems') entries.sort((a, b) => b.score - a.score || b.totalCards - a.totalCards || stable(a, b));
    else if (sortBy === 'name') entries.sort((a, b) => a.hostname.localeCompare(b.hostname) || stable(a, b));
    else if (sortBy === 'ip') entries.sort((a, b) => a.ip.localeCompare(b.ip, undefined, { numeric: true }) || a.hostname.localeCompare(b.hostname));
    else if (sortBy === 'gpus') entries.sort((a, b) => b.totalCards - a.totalCards || stable(a, b));

    return entries;
  }, [machines, filter, search, sortBy]);

  const handleDotOver = useCallback((e) => {
    const mid = e.target.dataset?.mid;
    if (!mid) return;
    const m = machineMap[mid];
    if (!m) return;
    const rect = e.target.getBoundingClientRect();
    setTooltip({ x: rect.left + rect.width / 2, y: rect.top - 6, m });
  }, [machineMap]);

  const handleDotOut = useCallback((e) => {
    if (e.target.dataset?.mid) setTooltip(null);
  }, []);

  const handleDotClick = useCallback((e) => {
    const mid = e.target.dataset?.mid;
    if (mid) setSelected(mid);
  }, []);

  const filterBtns = [
    { key: 'all', label: 'All', count: machines.length, color: 'var(--cyan)' },
    { key: 'online', label: 'Online', count: totalOnline, color: 'var(--green)' },
    { key: 'offline', label: 'Offline', count: totalOffline, color: 'var(--red)' },
    { key: 'unverified', label: 'Unverif', count: totalUnverified, color: 'var(--yellow)' },
  ];

  const healthPct = machines.length > 0 ? (totalOnline / machines.length * 100) : 0;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.25rem', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div>
          <h2 style={{ marginBottom: '0.2rem' }}>
            Fleet
            <span style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', fontWeight: 400, marginLeft: '0.75rem' }}>
              {servers.length} servers · {totalGpus} GPUs
            </span>
          </h2>
          {machines.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.4rem' }}>
              <div style={{ width: 200, height: 6, borderRadius: 3, background: 'rgba(255,255,255,0.05)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${healthPct}%`, borderRadius: 3, background: healthPct > 90 ? 'var(--green)' : healthPct > 60 ? 'var(--yellow)' : 'var(--red)', boxShadow: `0 0 6px ${healthPct > 90 ? 'var(--green-glow)' : healthPct > 60 ? 'var(--yellow-glow)' : 'var(--red-glow)'}`, transition: 'width 0.5s' }} />
              </div>
              <span className="text-mono" style={{ fontSize: '0.8rem', color: healthPct > 90 ? 'var(--green)' : healthPct > 60 ? 'var(--yellow)' : 'var(--red)', fontWeight: 700 }}>
                {Math.round(healthPct)}%
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
        {filterBtns.map(f => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            style={{
              padding: '0.3rem 0.7rem', borderRadius: '2rem',
              border: `1px solid ${filter === f.key ? f.color : 'var(--border)'}`,
              background: filter === f.key ? `color-mix(in srgb, ${f.color} 12%, transparent)` : 'transparent',
              color: filter === f.key ? f.color : 'var(--text-secondary)',
              cursor: 'pointer', fontSize: '0.78rem', fontWeight: 600, fontFamily: 'var(--font-ui)',
              transition: 'all 150ms', display: 'flex', alignItems: 'center', gap: '0.35rem',
            }}
          >
            {f.label}
            <span className="text-mono" style={{ fontSize: '0.7rem', opacity: 0.85 }}>{f.count}</span>
          </button>
        ))}

        <div style={{ flex: 1, minWidth: '1rem' }} />

        <div className="fleet-legend">
          <span className="fleet-legend-item"><span className="fleet-legend-dot" style={{ background: 'var(--green)', boxShadow: '0 0 4px var(--green-glow)' }} />Online</span>
          <span className="fleet-legend-item"><span className="fleet-legend-dot" style={{ background: 'var(--yellow)', boxShadow: '0 0 4px var(--yellow-glow)' }} />Unverified</span>
          <span className="fleet-legend-item"><span className="fleet-legend-dot" style={{ background: 'rgba(255,255,255,0.07)' }} />Offline</span>
        </div>

        <input
          type="text"
          placeholder="Search IP, host, GPU..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="filter-input"
          style={{ minWidth: '140px', maxWidth: '220px' }}
        />
        <select
          value={sortBy}
          onChange={e => setSortBy(e.target.value)}
          className="filter-select"
          style={{ minWidth: '100px' }}
        >
          <option value="problems">Problems first</option>
          <option value="name">By name</option>
          <option value="ip">By IP</option>
          <option value="gpus">By GPU count</option>
        </select>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {loading ? (
        <div className="spinner-container"><div className="spinner" /></div>
      ) : servers.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
          {machines.length === 0 ? 'No machines connected' : 'No servers match filters'}
        </div>
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
          gap: '0.6rem',
        }}>
          {servers.map(srv => {
            const pct = srv.totalCards > 0 ? Math.round((srv.onCount / srv.totalCards) * 100) : 0;
            const hasProblem = srv.offCount > 0 || srv.unvCount > 0;
            const borderColor = hasProblem
              ? (srv.offCount > 0 ? 'rgba(239,68,68,0.35)' : 'rgba(234,179,8,0.3)')
              : 'var(--border)';
            const barColor = pct === 100 ? 'var(--green)' : pct > 50 ? 'var(--yellow)' : 'var(--red)';

            return (
              <div
                key={srv.ip}
                className="fleet-tile"
                style={{ borderColor }}
              >
                {/* Server header */}
                <div style={{ marginBottom: '0.4rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                    <span className="fleet-tile-name" title={srv.hostname}>{srv.hostname}</span>
                    <span className="fleet-tile-count" style={{
                      color: pct === 100 ? 'var(--green)' : srv.offCount > 0 ? 'var(--red)' : 'var(--text-secondary)'
                    }}>
                      {srv.onCount}<span style={{ opacity: 0.4 }}>/{srv.totalCards}</span>
                    </span>
                  </div>
                  <div className="text-mono" style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: '1px' }}>
                    {srv.ip}
                  </div>
                </div>

                {/* GPU type line */}
                <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginBottom: '0.4rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {srv.gpuName} ×{srv.totalCards}
                </div>

                {/* Bar */}
                <div className="fleet-tile-bar">
                  <div className="fleet-tile-bar-fill" style={{ width: `${pct}%`, background: barColor }} />
                </div>

                {/* GPU dots */}
                <div
                  className="fleet-grid"
                  style={{ gridTemplateColumns: `repeat(auto-fill, ${DOT}px)`, marginTop: '0.5rem' }}
                  onMouseOver={handleDotOver}
                  onMouseOut={handleDotOut}
                  onClick={handleDotClick}
                >
                  {srv.gpuUnits.map((m, idx) => (
                    <div
                      key={`${m.machine_id}-${idx}`}
                      data-mid={m.machine_id}
                      className={`mdot ${m.online ? (m.verified ? 'on' : 'unv') : 'off'}`}
                      style={{ width: DOT, height: DOT }}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Tooltip */}
      {tooltip && (
        <div className="fleet-tooltip" style={{ left: tooltip.x, top: tooltip.y }}>
          <div style={{ fontWeight: 600, color: 'var(--text-bright)', marginBottom: '2px' }}>
            {tooltip.m.machine_id.slice(0, 16)}
          </div>
          <div style={{ color: 'var(--text-secondary)' }}>
            {tooltip.m.gpu_name || '—'} · {tooltip.m.gpu_mem_mb ? `${tooltip.m.gpu_mem_mb} MB` : '—'}
          </div>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.68rem' }}>
            {tooltip.m.version || '—'}
          </div>
          <div style={{ marginTop: '2px', fontWeight: 600, color: tooltip.m.online ? 'var(--green)' : 'var(--red)' }}>
            {tooltip.m.online ? '● Online' : '● Offline'}
            {tooltip.m.online && !tooltip.m.verified && (
              <span style={{ color: 'var(--yellow)', marginLeft: '0.5rem' }}>Unverified</span>
            )}
          </div>
        </div>
      )}

      {selected && <MachineDetailModal machineId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
