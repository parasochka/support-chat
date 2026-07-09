// Admin session storage (token + identity), with a "Remember me" choice.
//
// Remember me ON  -> localStorage: the login survives closing the browser.
// Remember me OFF -> sessionStorage: the login is dropped when the tab/browser
//                    closes (a shared-computer friendly default is opt-in).
//
// Readers (httpClient, authProvider) look in BOTH stores via `sessionGet`, so
// they don't need to know which mode is active. The `admin_remember` flag itself
// always lives in localStorage so the login form can pre-tick the box next time.

const SESSION_KEYS = ['admin_token', 'admin_role', 'admin_email'];
const REMEMBER_KEY = 'admin_remember';

/** Persist the logged-in session in the store chosen by `remember`. */
export const setSession = ({ token, role, email }, remember) => {
  const primary = remember ? localStorage : sessionStorage;
  const secondary = remember ? sessionStorage : localStorage;
  primary.setItem('admin_token', token || '');
  primary.setItem('admin_role', role || '');
  primary.setItem('admin_email', email || '');
  // Never leave a stale copy in the other store.
  SESSION_KEYS.forEach((k) => secondary.removeItem(k));
  localStorage.setItem(REMEMBER_KEY, remember ? '1' : '0');
};

/** Wipe the session from both stores (logout / expired token). */
export const clearSession = () => {
  SESSION_KEYS.forEach((k) => {
    localStorage.removeItem(k);
    sessionStorage.removeItem(k);
  });
};

/** Read a session value from whichever store holds it. */
export const sessionGet = (key) =>
  localStorage.getItem(key) || sessionStorage.getItem(key) || '';

/** Replace the stored token in place (sliding-session refresh from the server),
 *  writing to whichever store currently holds the session so the "Remember me"
 *  mode is preserved. No-op if there is no active token (e.g. env-token dev). */
export const updateToken = (token) => {
  if (!token) return;
  if (localStorage.getItem('admin_token') !== null) {
    localStorage.setItem('admin_token', token);
  } else if (sessionStorage.getItem('admin_token') !== null) {
    sessionStorage.setItem('admin_token', token);
  }
};

/** Whether the "Remember me" box should default to ticked. Default OFF, unless
 *  the operator explicitly ticked it last time (then it comes back ticked). */
export const rememberDefault = () => localStorage.getItem(REMEMBER_KEY) === '1';

/** True when a JWT's `exp` claim is in the past (so we can redirect to login
 *  cleanly instead of loading the dashboard and letting every call 401). */
export const isTokenExpired = (token) => {
  if (!token || token.split('.').length !== 3) return false;
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const payload = JSON.parse(atob(b64));
    if (!payload || typeof payload.exp !== 'number') return false;
    return Date.now() >= payload.exp * 1000;
  } catch {
    return false;
  }
};
