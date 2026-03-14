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

  const curlExample = `curl -s "${poolUrl}/get_number?count=1" \\
  -H "Authorization: ${token}" \\
  -H "X-Machine-Id: my-worker-01" \\
  -H "X-Hostname: worker-01" \\
  -H "X-GPU-Name: RTX 4090" \\
  -H "X-GPU-Count: 4"`;

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>Connect Machines</h2>

      <div style={{ background: 'var(--bg-secondary)', padding: '1.5rem', borderRadius: '0.75rem', marginBottom: '1.5rem' }}>
        <h3 style={{ marginBottom: '1rem' }}>How it works</h3>
        <ol style={{ lineHeight: '1.8', paddingLeft: '1.5rem' }}>
          <li>Trainer connects to pool via <strong>HTTPS</strong></li>
          <li>Authentication via <code>Authorization</code> header with trainer token</li>
          <li>First request auto-registers the machine</li>
          <li>New machines get test seeds first for verification</li>
          <li>After verification — real X ranges are distributed</li>
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
              ['GET /get_number?count=N', 'Get next X numbers to process'],
              ['POST /mark_done', 'Report completed X numbers'],
              ['POST /set_found', 'Report found key (x, y)'],
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

      <div style={{ background: 'var(--bg-secondary)', padding: '1.5rem', borderRadius: '0.75rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <h3>Example (curl)</h3>
          <CopyButton text={curlExample} />
        </div>
        <pre style={{ background: 'var(--bg-primary)', padding: '1rem', borderRadius: '0.5rem', fontSize: '0.8rem', overflow: 'auto', whiteSpace: 'pre-wrap' }}>{curlExample}</pre>
      </div>

      <div style={{ background: '#f59e0b15', border: '1px solid #f59e0b40', padding: '1rem', borderRadius: '0.75rem', marginTop: '1.5rem' }}>
        <strong style={{ color: '#f59e0b' }}>Security:</strong> All connections use HTTPS (SSL/TLS via Nginx). The trainer token authenticates each request. No additional agent or VPN needed.
      </div>
    </div>
  );
}
