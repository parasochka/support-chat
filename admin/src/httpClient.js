import { fetchUtils } from 'react-admin';
import { sessionGet, updateToken } from './session';

export const API_URL = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');

const ENV_TOKEN = import.meta.env.VITE_ADMIN_TOKEN || '';

export const getToken = () => ENV_TOKEN || sessionGet('admin_token');

export const hasEnvToken = () => Boolean(ENV_TOKEN);

/** fetchJson wrapper that attaches the admin JWT as a Bearer header, and adopts
 *  a sliding-session refresh token when the server returns one (X-Refresh-Token).
 *  Env-token dev sessions never refresh (updateToken no-ops without an active
 *  stored token). */
export const httpClient = (url, options = {}) => {
  const headers = new Headers(options.headers || { Accept: 'application/json' });
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return fetchUtils.fetchJson(url, { ...options, headers }).then((res) => {
    if (!ENV_TOKEN) {
      const refreshed = res.headers && res.headers.get('x-refresh-token');
      if (refreshed) updateToken(refreshed);
    }
    return res;
  });
};
