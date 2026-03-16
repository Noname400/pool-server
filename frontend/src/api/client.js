const BASE = '/api';

async function request(path, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers,
    credentials: 'include',
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const msg = body.detail || body.error || body.message || `Request failed: ${res.status}`;

    if (res.status === 401 && !path.startsWith('/auth/login')) {
      localStorage.removeItem('user');
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }

    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }

  if (res.status === 204) return null;
  return res.json();
}

function get(path) { return request(path); }
function post(path, body) { return request(path, { method: 'POST', body: JSON.stringify(body) }); }
function del(path) { return request(path, { method: 'DELETE' }); }

// Auth
export function login(apiKey) {
  return post('/auth/login', { api_key: apiKey });
}
export function logout() { return post('/auth/logout'); }
export function getMe() { return get('/auth/me'); }

// Admin: Stats
export function getStats() { return get('/admin/stats'); }

// Admin: Machines
export function getMachines() { return get('/admin/machines'); }
export function getMachineDetail(id) { return get(`/admin/machines/${encodeURIComponent(id)}`); }
export function deleteMachine(id) { return del(`/admin/machines/${encodeURIComponent(id)}`); }
export function machineCommand(id, command) {
  return post(`/admin/machines/${encodeURIComponent(id)}/command`, { command });
}
export function machineCommandAll(command, onlyOnline = true, tag = null) {
  return post('/admin/machines/command-all', { command, only_online: onlyOnline, tag });
}

// Admin: Settings
export function getSettings() { return get('/admin/settings'); }
export function updateSettings(data) { return post('/admin/settings', { settings: data }); }

// Admin: Found Keys
export function getFoundKeys(limit = 200) { return get(`/admin/found-keys?limit=${limit}`); }

// Admin: Stats History
export function getStatsHistory(hours = 24) { return get(`/admin/stats/history?hours=${hours}`); }

// Admin: Gate Monitor
export function getGateStats() { return get('/admin/gate-stats'); }

// Admin: Test Stand
export function getTestStatus() { return get('/admin/test/status'); }
export function startTest(xValues) { return post('/admin/test/start', { x_values: xValues }); }
export function stopTest() { return post('/admin/test/stop'); }
