import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { getMachines, machineCommand, getMachineDetail } from '../../api/client';
import Modal from '../../components/Modal';

const DOT = 14;
const BLOCK_SIZE = 20;

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

function isStaleOffline(m) {
  if (m.online) return false;
  if (!m.last_seen) return true;
  const age = Date.now() - new Date(m.last_seen).getTime();
  return age > 3600_000;
}

export default function Machines() {
  const [machines, setMachines] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('status');
  const [tooltip, setTooltip] = useState(null);
  const [showStaleOffline, setShowStaleOffline] = useState(false);

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

  const staleCount = useMemo(() => machines.filter(isStaleOffline).length, [machines]);
  const totalOnline = useMemo(() => machines.filter(m => m.online).length, [machines]);
  const totalOffline = machines.length - totalOnline;
  const totalUnverified = useMemo(() => machines.filter(m => m.online && !m.verified).length, [machines]);
  const totalGpus = useMemo(() => machines.reduce((s, m) => s + (m.gpu_count || 1), 0), [machines]);

  const visibleMachines = useMemo(() => {
    let list = [...machines];

    if (!showStaleOffline) {
      list = list.filter(m => !isStaleOffline(m));
    }

    if (filter === 'online') list = list.filter(m => m.online);
    if (filter === 'offline') list = list.filter(m => !m.online);
    if (filter === 'unverified') list = list.filter(m => m.online && !m.verified);

    if (search) {
      const q = search.toLowerCase();
      list = list.filter(m =>
        (m.ip || '').toLowerCase().includes(q) ||
        (m.hostname || '').toLowerCase().includes(q) ||
        (m.gpu_name || '').toLowerCase().includes(q) ||
        (m.machine_id || '').toLowerCase().includes(q)
      );
    }

    const stableId = (a, b) => (a.machine_id || '').localeCompare(b.machine_id || '');

    if (sortBy === 'status') {
      list.sort((a, b) => {
        const ao = a.online ? 0 : 1;
        const bo = b.online ? 0 : 1;
        return ao - bo || stableId(a, b);
      });
    } else if (sortBy === 'ip') {
      list.sort((a, b) => (a.ip || '').localeCompare(b.ip || '', undefined, { numeric: true }) || stableId(a, b));
    } else if (sortBy === 'gpus') {
      list.sort((a, b) => (b.gpu_count || 1) - (a.gpu_count || 1) || stableId(a, b));
    } else if (sortBy === 'name') {
      list.sort((a, b) => (a.hostname || '').localeCompare(b.hostname || '') || stableId(a, b));
    }

    return list;
  }, [machines, filter, search, sortBy, showStaleOffline]);

  const blocks = useMemo(() => {
    const result = [];
    for (let i = 0; i < visibleMachines.length; i += BLOCK_SIZE) {
      result.push(visibleMachines.slice(i, i + BLOCK_SIZE));
    }
    return result;
  }, [visibleMachines]);

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
    { key: 'all', label: 'All', count: machines.length - (showStaleOffline ? 0 : staleCount), color: 'var(--cyan)' },
    { key: 'online', label: 'Online', count: totalOnline, color: 'var(--green)' },
    { key: 'offline', label: 'Offline', count: totalOffline - (showStaleOffline ? 0 : staleCount), color: 'var(--red)' },
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
              {visibleMachines.length} machines · {totalGpus} GPUs
            </span>
          </h2>
          <div style={{ display: 'flex', gap: '1rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '0.82rem', fontWeight: 700, color: 'var(--green)' }}>
              {totalOnline} Online
            </span>
            {totalUnverified > 0 && (
              <span style={{ fontSize: '0.82rem', fontWeight: 700, color: 'var(--yellow)' }}>
                {totalUnverified} Unverified
              </span>
            )}
            <span style={{ fontSize: '0.82rem', fontWeight: 700, color: totalOffline > 0 ? 'var(--red)' : 'var(--text-muted)' }}>
              {totalOffline} Offline
            </span>
          </div>
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
          <option value="status">By status</option>
          <option value="name">By name</option>
          <option value="ip">By IP</option>
          <option value="gpus">By GPU count</option>
        </select>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {loading ? (
        <div className="spinner-container"><div className="spinner" /></div>
      ) : visibleMachines.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
          {machines.length === 0 ? 'No machines connected' : 'No machines match filters'}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {blocks.map((block, blockIdx) => (
            <div key={blockIdx} className="card" style={{ padding: '0.75rem 1rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontWeight: 600 }}>
                  {blockIdx * BLOCK_SIZE + 1}–{blockIdx * BLOCK_SIZE + block.length}
                </span>
                <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                  {block.filter(m => m.online).length}/{block.length} online
                </span>
              </div>
              <div
                className="fleet-grid"
                style={{ gridTemplateColumns: `repeat(auto-fill, ${DOT}px)` }}
                onMouseOver={handleDotOver}
                onMouseOut={handleDotOut}
                onClick={handleDotClick}
              >
                {block.map(m => (
                  <div
                    key={m.machine_id}
                    data-mid={m.machine_id}
                    className={`mdot ${m.online ? (m.verified ? 'on' : 'unv') : 'off'}`}
                    style={{ width: DOT, height: DOT }}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Show offline toggle */}
      {staleCount > 0 && (
        <div style={{ textAlign: 'center', marginTop: '1rem' }}>
          <button
            onClick={() => setShowStaleOffline(!showStaleOffline)}
            style={{
              padding: '0.4rem 1rem', borderRadius: '2rem', cursor: 'pointer',
              border: '1px solid var(--border)', fontSize: '0.78rem', fontWeight: 600,
              background: showStaleOffline ? 'rgba(239,68,68,0.1)' : 'transparent',
              color: showStaleOffline ? 'var(--red)' : 'var(--text-muted)',
              transition: 'all 150ms',
            }}
          >
            {showStaleOffline ? 'Hide' : 'Show'} offline ({staleCount})
          </button>
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
          <div className="text-mono" style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
            {tooltip.m.ip || '—'} · {tooltip.m.hostname || '—'}
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
