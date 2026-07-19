// Timestamps render in the viewer's local timezone (the API returns tz-aware
// ISO strings) — one formatter so every table reads the same.
export const fmtDateTime = (v) => (v ? new Date(v).toLocaleString() : '');
