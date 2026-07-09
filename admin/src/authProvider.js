import { API_URL, hasEnvToken } from './httpClient';
import { setSession, clearSession, sessionGet, isTokenExpired } from './session';

/**
 * Auth against the real backend: POST /admin/login with email + password
 * returns a JWT ({token, role, email}). The token is kept in local/session
 * storage (see ./session — chosen by the "Remember me" box) and sent as
 * `Authorization: Bearer` by httpClient.
 *
 * Alternatively, VITE_ADMIN_TOKEN (a pre-issued JWT) skips the login form
 * entirely — useful for local development.
 */
const authProvider = {
  login: async ({ username, password, remember = true }) => {
    const res = await fetch(`${API_URL}/admin/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: username, password }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail || 'Invalid email or password');
    }
    setSession(
      { token: body.token, role: body.role || '', email: body.email || '' },
      remember
    );
  },

  logout: () => {
    clearSession();
    return Promise.resolve();
  },

  checkAuth: () => {
    if (hasEnvToken()) return Promise.resolve();
    const token = sessionGet('admin_token');
    // A token that has already expired must not be treated as valid: without
    // this the SPA loads with a dead token, every /admin/* call 401s, and the
    // half-rendered dashboard only THEN bounces to login (the "lets me in, works
    // wrong, re-logs me in" bug). Catch it up front for a clean redirect.
    if (token && !isTokenExpired(token)) return Promise.resolve();
    if (token) clearSession();
    return Promise.reject();
  },

  checkError: (error) => {
    if (error && error.status === 401) {
      clearSession();
      return Promise.reject();
    }
    // 403 = no write access for this account/scope; stay logged in.
    return Promise.resolve();
  },

  getPermissions: () => Promise.resolve(sessionGet('admin_role') || 'manager'),

  getIdentity: () => {
    const email = sessionGet('admin_email') || 'admin';
    return Promise.resolve({ id: email, fullName: email });
  },
};

export default authProvider;
