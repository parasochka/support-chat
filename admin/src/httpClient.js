import { fetchUtils } from 'react-admin';

export const API_URL = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');

const ENV_TOKEN = import.meta.env.VITE_ADMIN_TOKEN || '';

export const getToken = () => ENV_TOKEN || localStorage.getItem('admin_token') || '';

export const hasEnvToken = () => Boolean(ENV_TOKEN);

/** fetchJson wrapper that attaches the admin JWT as a Bearer header. */
export const httpClient = (url, options = {}) => {
  const headers = new Headers(options.headers || { Accept: 'application/json' });
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return fetchUtils.fetchJson(url, { ...options, headers });
};
