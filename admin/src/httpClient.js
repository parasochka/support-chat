import { fetchUtils } from 'react-admin';
import { clearSession, sessionGet, updateToken } from './session';

export const API_URL = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');

const ENV_TOKEN = import.meta.env.VITE_ADMIN_TOKEN || '';

export const getToken = () => ENV_TOKEN || sessionGet('admin_token');

export const hasEnvToken = () => Boolean(ENV_TOKEN);

/** fetchJson wrapper that attaches the admin JWT as a Bearer header, and adopts
 *  a sliding-session refresh token when the server returns one (X-Refresh-Token).
 *  Env-token dev sessions never refresh (updateToken no-ops without an active
 *  stored token). */
/** Drop the dead session and bounce to login.
 *
 *  react-admin only routes errors through authProvider.checkError when they come
 *  from a dataProvider/authProvider call. Every custom page here (Retention,
 *  Settings, Logs, Quality, Structure, ApiKeys, Mcp, …) calls httpClient
 *  directly, so those 401s bypassed it entirely: a deactivated or revoked
 *  account still holds a structurally valid JWT, checkAuth (which only inspects
 *  `exp`) lets the SPA load, and the operator then collects endless "load
 *  failed" toasts with no redirect. Handling it at the transport covers every
 *  caller at once. */
const onUnauthorized = () => {
  if (ENV_TOKEN) return;          // dev env-token sessions have nowhere to go
  clearSession();
  if (typeof window !== 'undefined' && !window.location.hash.startsWith('#/login')) {
    window.location.hash = '#/login';
  }
};

export const httpClient = (url, options = {}) => {
  const headers = new Headers(options.headers || { Accept: 'application/json' });
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return fetchUtils.fetchJson(url, { ...options, headers }).then(
    (res) => {
      if (!ENV_TOKEN) {
        const refreshed = res.headers && res.headers.get('x-refresh-token');
        if (refreshed) updateToken(refreshed);
      }
      return res;
    },
    (err) => {
      if (err && err.status === 401) onUnauthorized();
      throw err;                  // callers still see the rejection
    },
  );
};
