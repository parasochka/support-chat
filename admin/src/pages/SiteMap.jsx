import { useEffect, useState } from 'react';
import { Title, useNotify, usePermissions } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';
import RequireProduct from '../components/RequireProduct';

const emptyRow = () => ({ title: '', url: '', purpose: '' });

/**
 * Site map — one per-product list of official pages the AI may link to. It is
 * injected into the byte-stable Layer-1 core of BOTH bots (support + retention)
 * and named in each core's links policy, so the model links to real pages
 * instead of inventing URLs. Stored on the product (like prompt variables).
 */
const SiteMapEditor = () => {
  const notify = useNotify();
  const { permissions } = usePermissions();
  const readOnly = permissions !== 'admin';
  const [rows, setRows] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    httpClient(withProduct(`${API_URL}/admin/site-map`))
      .then(({ json }) => {
        const pages = json.pages || [];
        setRows(pages.length ? pages.map((p) => ({ ...emptyRow(), ...p })) : [emptyRow()]);
      })
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }))
      .finally(() => setLoaded(true));
  }, [notify]);

  const setRow = (i, patch) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((rs) => [...rs, emptyRow()]);
  const removeRow = (i) => setRows((rs) => rs.filter((_, j) => j !== i));

  const save = async () => {
    // Drop fully-blank rows; keep the rest for the server to validate the URLs.
    const value = rows
      .map((r) => ({
        title: (r.title || '').trim(),
        url: (r.url || '').trim(),
        purpose: (r.purpose || '').trim(),
      }))
      .filter((r) => r.url || r.title || r.purpose);
    setSaving(true);
    try {
      const { json } = await httpClient(withProduct(`${API_URL}/admin/site-map`), {
        method: 'PUT',
        body: JSON.stringify({ value }),
      });
      const pages = json.pages || [];
      setRows(pages.length ? pages.map((p) => ({ ...emptyRow(), ...p })) : [emptyRow()]);
      notify('Site map saved', { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box sx={{ p: 2, maxWidth: 900 }}>
      <Title title="Site map" />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Official pages of this product's website. They are added to the system
        prompt of <strong>both</strong> the support chat and the Telegram
        retention bot, and to their links policy, so the assistant links players
        to real pages instead of inventing URLs. One entry per page — the URL is
        required and must start with <code>http://</code> or <code>https://</code>.
      </Typography>

      <Card>
        <CardContent>
          {loaded &&
            rows.map((r, i) => (
              <Stack
                key={i}
                direction={{ xs: 'column', md: 'row' }}
                spacing={1}
                alignItems={{ md: 'flex-start' }}
                sx={{ mb: 1.5 }}
              >
                <TextField
                  label="Title"
                  value={r.title}
                  onChange={(e) => setRow(i, { title: e.target.value })}
                  disabled={readOnly}
                  size="small"
                  sx={{ flex: '0 0 200px' }}
                  placeholder="Cashier"
                />
                <TextField
                  label="URL"
                  value={r.url}
                  onChange={(e) => setRow(i, { url: e.target.value })}
                  disabled={readOnly}
                  size="small"
                  sx={{ flex: 1, minWidth: 220 }}
                  placeholder="https://example.com/cashier"
                />
                <TextField
                  label="Purpose (when to link here)"
                  value={r.purpose}
                  onChange={(e) => setRow(i, { purpose: e.target.value })}
                  disabled={readOnly}
                  size="small"
                  sx={{ flex: 1, minWidth: 220 }}
                  placeholder="where players top up their balance"
                />
                <IconButton
                  aria-label="remove page"
                  onClick={() => removeRow(i)}
                  disabled={readOnly}
                  sx={{ mt: { md: 0.5 } }}
                >
                  <DeleteIcon />
                </IconButton>
              </Stack>
            ))}

          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            <Button startIcon={<AddIcon />} onClick={addRow} disabled={readOnly}>
              Add page
            </Button>
            <Button
              variant="contained"
              onClick={save}
              disabled={saving || readOnly}
            >
              {saving ? 'Saving…' : 'Save site map'}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
};

const SiteMap = () => (
  <RequireProduct title="Site map">
    <SiteMapEditor />
  </RequireProduct>
);

export default SiteMap;
