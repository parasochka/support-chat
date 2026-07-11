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
let metaCached = null;
let metaPending = null;

/** The raw /admin/meta payload, cached for the page lifetime (null on failure). */
export const fetchMeta = () => {
  if (metaCached) return Promise.resolve(metaCached);
  if (!metaPending) {
    metaPending = httpClient(withProduct(`${API_URL}/admin/meta`))
      .then(({ json }) => {
        metaCached = json;
        return json;
      })
      .catch(() => {
        metaPending = null;
        return null;
      });
  }
  return metaPending;
};

/**
 * The current model id + USD-per-1M-token pricing for the token/cost counters
 * ({model, pricing: {input_per_1m, cached_input_per_1m, output_per_1m}|null}).
 */
export const useModelPricing = () => {
  const [mp, setMp] = useState(
    () => metaCached?.model_pricing || null
  );
  useEffect(() => {
    let alive = true;
    fetchMeta().then((m) => alive && m && setMp(m.model_pricing || null));
    return () => {
      alive = false;
    };
  }, []);
  return mp;
};

export const fetchLanguages = () => {
  if (cached) return Promise.resolve(cached);
  if (!pending) {
    pending = fetchMeta()
      .then((json) => {
        if (!json) throw new Error('meta unavailable');
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
