import { useEffect, useState } from 'react';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';

/**
 * Supported-language catalogue from GET /admin/meta, cached for the page
 * lifetime so filters and per-language forms stop hard-coding the shipped
 * five languages (admin-added languages appear everywhere automatically).
 */
let cached = null;
let pending = null;

export const fetchLanguages = () => {
  if (cached) return Promise.resolve(cached);
  if (!pending) {
    pending = httpClient(withProduct(`${API_URL}/admin/meta`))
      .then(({ json }) => {
        const names = Object.fromEntries(
          (json.languages || []).map((l) => [l.code, l.name])
        );
        cached = (json.supported || []).map((code) => ({
          code,
          name: names[code] || code.toUpperCase(),
        }));
        return cached;
      })
      .catch(() => {
        pending = null;
        // Shipped defaults keep the UI working if /meta is unreachable.
        return ['en', 'ru', 'es', 'tr', 'pt'].map((code) => ({
          code,
          name: code.toUpperCase(),
        }));
      });
  }
  return pending;
};

export const useSupportedLanguages = () => {
  const [langs, setLangs] = useState(cached || []);
  useEffect(() => {
    let alive = true;
    fetchLanguages().then((l) => alive && setLangs(l));
    return () => {
      alive = false;
    };
  }, []);
  return langs;
};
