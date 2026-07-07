import { useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';

/**
 * The user-facing copy registry (widget chrome + server-generated turns +
 * the per-language contact_url). GET /admin/translations returns the key
 * catalogue, resolved copy and stored overrides; the editor round-trips
 * overrides only — an empty field falls back to the shipped default.
 */
const Translations = () => {
  const [data, setData] = useState(null);
  const [overrides, setOverrides] = useState({});
  const [lang, setLang] = useState('');
  const [saving, setSaving] = useState(false);
  const notify = useNotify();

  useEffect(() => {
    httpClient(withProduct(`${API_URL}/admin/translations`))
      .then(({ json }) => {
        setData(json);
        setOverrides(json.overrides || {});
        setLang((json.languages || [])[0]?.code || '');
      })
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [notify]);

  if (!data) return <Box sx={{ p: 2 }}>Loading…</Box>;

  const setValue = (key, value) => {
    setOverrides((prev) => {
      const next = { ...prev, [lang]: { ...(prev[lang] || {}) } };
      if (value) next[lang][key] = value;
      else delete next[lang][key];
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      await httpClient(withProduct(`${API_URL}/admin/translations`), {
        method: 'PUT',
        body: JSON.stringify({ value: overrides }),
      });
      notify('Translations saved', { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const scopes = ['widget', 'server', 'retention'];

  return (
    <Box sx={{ p: 2, maxWidth: 1000 }}>
      <Title title="Translations" />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Player-facing copy per language. Empty fields fall back to the shipped
        default (shown as placeholder). <code>contact_url</code> is the
        per-language escalation contact button link.
      </Typography>
      <Tabs
        value={lang}
        onChange={(e, v) => setLang(v)}
        sx={{ mb: 2 }}
        variant="scrollable"
      >
        {(data.languages || []).map((l) => (
          <Tab key={l.code} value={l.code} label={`${l.name} (${l.code})`} />
        ))}
      </Tabs>
      {scopes.map((scope) => {
        const keys = (data.keys || []).filter((k) => k.scope === scope);
        if (!keys.length) return null;
        return (
          <Card key={scope} sx={{ mb: 2 }}>
            <CardContent>
              <Typography variant="h6" gutterBottom sx={{ textTransform: 'capitalize' }}>
                {scope} copy
              </Typography>
              {keys.map((k) => (
                <TextField
                  key={k.key}
                  label={k.key}
                  helperText={k.description}
                  value={overrides[lang]?.[k.key] ?? ''}
                  placeholder={
                    data.resolved?.[lang]?.[k.key] ?? data.defaults?.[lang]?.[k.key] ?? ''
                  }
                  onChange={(e) => setValue(k.key, e.target.value)}
                  fullWidth
                  multiline
                  margin="dense"
                />
              ))}
            </CardContent>
          </Card>
        );
      })}
      <Button variant="contained" onClick={save} disabled={saving}>
        {saving ? 'Saving…' : 'Save all languages'}
      </Button>
    </Box>
  );
};

export default Translations;
