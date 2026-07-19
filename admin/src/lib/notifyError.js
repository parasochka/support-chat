// One home for the error-toast idiom. FastAPI puts the useful message in
// `detail`; react-admin's HttpError surfaces it as e.body.detail — sites that
// fell back to e.message alone silently swallowed it.
export const notifyError = (notify, e, fallback) =>
  notify(e?.body?.detail || e?.message || fallback, { type: 'error' });
