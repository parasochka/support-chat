import { API_URL, hasEnvToken } from './httpClient';

/**
 * Auth against the real backend: POST /admin/login with email + password
 * returns a JWT ({token, role, email}). The token is kept in localStorage and
 * sent as `Authorization: Bearer` by httpClient.
 *
 * Alternatively, VITE_ADMIN_TOKEN (a pre-issued JWT) skips the login form
 * entirely — useful for local development.
 */
const authProvider = {
  login: async ({ username, password }) => {
    const res = await fetch(`${API_URL}/admin/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: username, password }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail || 'Invalid email or password');
    }
    localStorage.setItem('admin_token', body.token);
    localStorage.setItem('admin_role', body.role || '');
    localStorage.setItem('admin_email', body.email || '');
  },

  logout: () => {
    localStorage.removeItem('admin_token');
    localStorage.removeItem('admin_role');
    localStorage.removeItem('admin_email');
    return Promise.resolve();
  },

  checkAuth: () => {
    if (hasEnvToken() || localStorage.getItem('admin_token')) {
      return Promise.resolve();
    }
    return Promise.reject();
  },

  checkError: (error) => {
    if (error && error.status === 401) {
      localStorage.removeItem('admin_token');
      return Promise.reject();
    }
    // 403 = no write access for this account/scope; stay logged in.
    return Promise.resolve();
  },

  getPermissions: () => Promise.resolve(localStorage.getItem('admin_role') || 'manager'),

  getIdentity: () => {
    const email = localStorage.getItem('admin_email') || 'admin';
    return Promise.resolve({ id: email, fullName: email });
  },
};

export default authProvider;
