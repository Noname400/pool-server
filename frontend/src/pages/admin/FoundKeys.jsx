import React, { useState, useEffect } from 'react';
import { getFoundKeys } from '../../api/client';

export default function FoundKeys() {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      const all = await getFoundKeys(500);
      const seen = new Set();
      const unique = all.filter(k => {
        const key = `${k.x_value}_${k.y_value}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      setKeys(unique);
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <h2>Found Keys ({keys.length})</h2>
        <button className="btn btn-secondary" onClick={load}>Refresh</button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {loading ? <p>Loading...</p> : keys.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
          No keys found yet
        </div>
      ) : (
        <div style={{ overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '2px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '0.75rem 0.5rem' }}>#</th>
                <th style={{ textAlign: 'left', padding: '0.75rem 0.5rem' }}>X Value</th>
                <th style={{ textAlign: 'left', padding: '0.75rem 0.5rem' }}>Y Value</th>
                <th style={{ textAlign: 'left', padding: '0.75rem 0.5rem' }}>Machine</th>
                <th style={{ textAlign: 'left', padding: '0.75rem 0.5rem' }}>Found At</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((fk, i) => (
                <tr key={fk.id || i} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '0.5rem', color: 'var(--text-muted)', fontSize: '0.85rem' }}>{i + 1}</td>
                  <td style={{ padding: '0.5rem', fontFamily: 'monospace', fontSize: '0.85rem', fontWeight: 600 }}>{fk.x_value}</td>
                  <td style={{ padding: '0.5rem', fontFamily: 'monospace', fontSize: '0.8rem', maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{fk.y_value}</td>
                  <td style={{ padding: '0.5rem', fontSize: '0.85rem' }}>{fk.machine_id}</td>
                  <td style={{ padding: '0.5rem', fontSize: '0.85rem' }}>{fk.found_at ? new Date(fk.found_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
