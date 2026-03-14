import React, { useState, useEffect } from 'react';
import { getSettings } from '../../api/client';
import CopyButton from '../../components/CopyButton';

export default function Connect() {
  const [settings, setSettings] = useState(null);

  useEffect(() => {
    getSettings().then(setSettings).catch(() => {});
  }, []);

  const poolUrl = window.location.origin;
  const token = settings?.trainer_auth_token || '<TRAINER_AUTH_TOKEN>';

  const exampleHeaders = `Authorization: ${token}
X-Machine-Id: <unique-machine-id>
X-Hostname: <hostname>
X-GPU-Name: <gpu-model>
X-GPU-Count: <count>
X-Version: <trainer-version>`;

  const curlGet = `curl -s "${poolUrl}/get_number?count=2" \\
  -H "Authorization: ${token}" \\
  -H "X-Machine-Id: my-worker-01" \\
  -H "X-Hostname: worker-01" \\
  -H "X-GPU-Name: RTX 4090" \\
  -H "X-GPU-Count: 4"

# Response:
# {
#   "numbers": [100, 101],
#   "leases": {"100": "42", "101": "43"},
#   "lease_ttl": 60,
#   "command": "work"
# }`;

  const curlDone = `curl -s -X POST "${poolUrl}/mark_done" \\
  -H "Authorization: ${token}" \\
  -H "X-Machine-Id: my-worker-01" \\
  -H "Content-Type: application/json" \\
  -d '{"nums": [100, 101], "leases": {"100": "42", "101": "43"}}'

# Response:
# {"ok": true, "count": 2, "rejected": 0, "already_done": 0}`;

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>Connect Machines</h2>

      <div style={{ background: 'var(--bg-secondary)', padding: '1.5rem', borderRadius: '0.75rem', marginBottom: '1.5rem' }}>
        <h3 style={{ marginBottom: '1rem' }}>How it works (v3 — lease model)</h3>
        <ol style={{ lineHeight: '1.8', paddingLeft: '1.5rem' }}>
          <li>Trainer connects to pool via <strong>HTTPS</strong></li>
          <li>Authentication via <code>Authorization</code> header with trainer token</li>
          <li>First request auto-registers the machine</li>
          <li>New machines get test seeds first for verification</li>
          <li><code>GET /get_number</code> returns X values with <strong>lease IDs</strong> and a TTL</li>
          <li>Trainer must send <code>lease_id</code> back in <code>POST /mark_done</code> within the TTL</li>
          <li>If not confirmed within TTL, X is <strong>automatically re-queued</strong> to another machine</li>
          <li>Duplicate / late <code>mark_done</code> with wrong lease is safely rejected</li>
        </ol>
      </div>

      <div style={{ background: 'var(--bg-secondary)', padding: '1.5rem', borderRadius: '0.75rem', marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <h3>Pool URL</h3>
          <CopyButton text={poolUrl} />
        </div>
        <code style={{ fontSize: '1.1rem', fontWeight: 600 }}>{poolUrl}</code>
      </div>

      <div style={{ background: 'var(--bg-secondary)', padding: '1.5rem', borderRadius: '0.75rem', marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <h3>Required Headers</h3>
        </div>
        <pre style={{ background: 'var(--bg-primary)', padding: '1rem', borderRadius: '0.5rem', fontSize: '0.85rem', overflow: 'auto' }}>{exampleHeaders}</pre>
      </div>

      <div style={{ background: 'var(--bg-secondary)', padding: '1.5rem', borderRadius: '0.75rem', marginBottom: '1.5rem' }}>
        <h3 style={{ marginBottom: '0.75rem' }}>API Endpoints</h3>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {[
              ['GET /get_number?count=N', 'Get X numbers with lease IDs (max 1000)'],
              ['POST /mark_done', 'Confirm completed X with leases: {nums, leases}'],
              ['POST /set_found', 'Report found key: {x, y}'],
              ['GET /status', 'Pool health check'],
            ].map(([endpoint, desc]) => (
              <tr key={endpoint} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '0.5rem', fontFamily: 'monospace', fontSize: '0.85rem', fontWeight: 600 }}>{endpoint}</td>
                <td style={{ padding: '0.5rem', fontSize: '0.85rem' }}>{desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ background: 'var(--bg-secondary)', padding: '1.5rem', borderRadius: '0.75rem', marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <h3>1. Get numbers (with leases)</h3>
          <CopyButton text={curlGet} />
        </div>
        <pre style={{ background: 'var(--bg-primary)', padding: '1rem', borderRadius: '0.5rem', fontSize: '0.8rem', overflow: 'auto', whiteSpace: 'pre-wrap' }}>{curlGet}</pre>
      </div>

      <div style={{ background: 'var(--bg-secondary)', padding: '1.5rem', borderRadius: '0.75rem', marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <h3>2. Mark done (with leases)</h3>
          <CopyButton text={curlDone} />
        </div>
        <pre style={{ background: 'var(--bg-primary)', padding: '1rem', borderRadius: '0.5rem', fontSize: '0.8rem', overflow: 'auto', whiteSpace: 'pre-wrap' }}>{curlDone}</pre>
      </div>

      <div style={{ background: '#f59e0b15', border: '1px solid #f59e0b40', padding: '1rem', borderRadius: '0.75rem', marginTop: '1.5rem' }}>
        <strong style={{ color: '#f59e0b' }}>Lease guarantee:</strong> Each X is leased for <strong>{settings?.lease_ttl || 60}s</strong>.
        If <code>mark_done</code> is not received within the TTL, the X is automatically re-queued and given to another machine. No X is ever lost.
      </div>

      <div style={{ background: '#ef444415', border: '1px solid #ef444440', padding: '1rem', borderRadius: '0.75rem', marginTop: '0.75rem' }}>
        <strong style={{ color: '#ef4444' }}>Important:</strong> Always send the <code>leases</code> dict from <code>get_number</code> response back in <code>mark_done</code>.
        Without it, lease validation is skipped (legacy mode) and re-queue protection does not apply.
      </div>
    </div>
  );
}
