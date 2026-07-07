import { useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';

// Content-tuning group edited from the prompt-variables area in the legacy
// SPA; kept out of the generic settings editor to avoid a duplicate editor.
const SKIP_GROUPS = ['escalation'];

/**
 * Hot-reloaded runtime settings groups (antispam / model / language / general
 * / retention). Each group is edited as JSON and written whole via
 * PUT /admin/settings/{key} — the backend validates hard and rejects bad
 * shapes with a 400.
 */
const Settings = () => {
  const [keys, setKeys] = useState([]);
  const [resolved, setResolved] = useState({});
  const [drafts, setDrafts] = useState({});
  const [saving, setSaving] = useState('');
  const notify = useNotify();

  const load = () =>
    httpClient(`${API_URL}/admin/settings`)
      .then(({ json }) => {
        const ks = (json.keys || []).filter((k) => !SKIP_GROUPS.includes(k));
        setKeys(ks);
        setResolved(json.resolved || {});
        const d = {};
        ks.forEach((k) => {
          d[k] = JSON.stringify(json.resolved?.[k] ?? {}, null, 2);
        });
        setDrafts(d);
      })
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async (key) => {
    let value;
    try {
      value = JSON.parse(drafts[key]);
    } catch {
      notify(`"${key}": invalid JSON`, { type: 'error' });
      return;
    }
    setSaving(key);
    try {
      await httpClient(`${API_URL}/admin/settings/${key}`, {
        method: 'PUT',
        body: JSON.stringify({ value }),
      });
      notify(`Setting "${key}" saved`, { type: 'success' });
      await load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    } finally {
      setSaving('');
    }
  };

  return (
    <Box sx={{ p: 2, maxWidth: 900 }}>
      <Title title="Settings" />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Hot-reloaded runtime settings (effective values shown; precedence
        DB&nbsp;→ env&nbsp;→ default). Each group is saved whole; the backend
        validates and rejects invalid shapes.
      </Typography>
      <Stack spacing={2}>
        {keys.map((key) => (
          <Card key={key}>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                {key}
              </Typography>
              <TextField
                value={drafts[key] ?? ''}
                onChange={(e) => setDrafts({ ...drafts, [key]: e.target.value })}
                fullWidth
                multiline
                minRows={6}
                InputProps={{ sx: { fontFamily: 'monospace', fontSize: 13 } }}
              />
              <Button
                variant="contained"
                onClick={() => save(key)}
                disabled={saving === key}
                sx={{ mt: 1 }}
              >
                {saving === key ? 'Saving…' : `Save ${key}`}
              </Button>
            </CardContent>
          </Card>
        ))}
      </Stack>
      {/* resolved kept in state for future diff-vs-override display */}
      {void resolved}
    </Box>
  );
};

export default Settings;
