// Model credentials and authentication cookies stay on the server.
const API_BASE = import.meta.env.VITE_API_URL || (import.meta.env.PROD ? import.meta.env.BASE_URL.replace(/\/$/, '') : '');
const pendingRequests = new Set();
let csrfToken = '';

export function setAdminSession(session) {
  csrfToken = session?.csrf_token || '';
}

export function clearAdminSession() {
  csrfToken = '';
  for (const controller of pendingRequests) controller.abort();
  pendingRequests.clear();
}

export async function localRequest(path, options = {}) {
  const requestUrl = new URL(`${API_BASE}${path}`, window.location.origin);
  if (import.meta.env.PROD && requestUrl.origin !== window.location.origin) throw new Error('服务地址必须与当前页面同源。');
  const { timeout = 180_000, notifyUnauthorized = true, ...requestOptions } = options;
  const controller = new AbortController();
  const headers = new Headers(requestOptions.headers);
  if (csrfToken && !['GET', 'HEAD'].includes((requestOptions.method || 'GET').toUpperCase())) headers.set('X-CSRF-Token', csrfToken);
  const timer = window.setTimeout(() => controller.abort(), timeout);
  pendingRequests.add(controller);
  try {
    const response = await fetch(requestUrl, {
      ...requestOptions,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = typeof body.detail === 'string' ? body.detail : body.message;
      const error = new Error(detail || `请求失败（HTTP ${response.status}），请稍后重试。`);
      error.status = response.status;
      if (response.status === 401 && notifyUnauthorized && import.meta.env.PROD) {
        clearAdminSession();
        window.dispatchEvent(new Event('cad-session-expired'));
      }
      throw error;
    }
    return response;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时。请稍后重试，或换一张较小的图纸。');
    if (error instanceof TypeError) throw new Error('无法连接服务，请检查网络后重试。');
    throw error;
  } finally {
    window.clearTimeout(timer);
    pendingRequests.delete(controller);
  }
}

export async function getAdminSession() {
  return (await localRequest('/api/auth/session', { timeout: 10_000, notifyUnauthorized: false })).json();
}

export async function loginAdmin(username, password) {
  return (await localRequest('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
    timeout: 20_000,
    notifyUnauthorized: false,
  })).json();
}

export async function logoutAdmin() {
  await localRequest('/api/auth/logout', { method: 'POST', timeout: 10_000 });
}

export async function uploadLocalDrawing(file) {
  const body = new FormData();
  body.append('file', file);
  return (await localRequest('/api/upload', { method: 'POST', body })).json();
}

export async function askLocalDrawing(sessionId, question, history) {
  return (await localRequest('/api/local-qa', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, question, history }),
  })).json();
}

export async function getLocalStatus() {
  return (await localRequest('/api/local-status', { timeout: 10_000 })).json();
}

export async function getLocalPreview(sessionId, kind) {
  const query = new URLSearchParams({ session_id: sessionId, type: 'original' });
  return (await localRequest(`/api/${kind}?${query}`)).blob();
}

export async function getLocalDemo() {
  const blob = await (await localRequest('/api/local-demo')).blob();
  return new File([blob], '建筑平面示例.dxf', { type: 'application/dxf' });
}

export async function getLocalFont() {
  return (await localRequest('/api/local-font', { timeout: 20_000 })).blob();
}
