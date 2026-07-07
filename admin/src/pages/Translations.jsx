import { useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Stack from '@mui/material/Stack';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { getProductId, withProduct } from '../productScope';

const SCOPES = [
  ['widget', 'Widget texts', 'Chrome strings rendered by the widget itself.'],
  [
    'server',
    'Assistant service replies',
    'Model-free texts the server sends as part of a turn (escalation card, closing option, nudges, the per-language contact_url).',
  ],
  [
    'retention',
    'Retention bot copy',
    'Telegram retention bot strings (menu, subscription gate, hand-off).',
  ],
];

/**
 * The user-facing copy registry (widget chrome + server-generated turns +
 * retention copy + the per-language contact_url), plus the per-language topic
 * names (stored on the topics themselves).
 *
 * Fields show the RESOLVED copy — what the player currently sees, overrides
 * and shipped defaults included — exactly like the legacy SPA, so existing
 * translations are visible and editable, not hidden behind placeholders. On
 * save only values that differ from the shipped default are stored as
 * overrides; clearing a field falls back to the default.
 */
const Translations = () => {
  const [data, setData] = useState(null);
  const [topics, setTopics] = useState([]);
  const [texts, setTexts] = useState({}); // lang -> {key: value}, seeded from resolved
  const [titles, setTitles] = useState({}); // topicId -> {lang: value}
  const [lang, setLang] = useState('');
  const [saving, setSaving] = useState(false);
  const notify = useNotify();

  const load = () =>
    Promise.all([
      httpClient(withProduct(`${API_URL}/admin/translations`)),
      httpClient(withProduct(`${API_URL}/admin/kb/topics`)),
    ])
      .then(([t, k]) => {
        setData(t.json);
        setTopics(k.json.topics || []);
        setTexts({});
        setTitles({});
        setLang((prev) => prev || (t.json.languages || [])[0]?.code || '');
      })
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Seed the current language's fields from the resolved copy on first visit.
  useEffect(() => {
    if (!data || !lang) return;
    setTexts((prev) => {
      if (prev[lang]) return prev;
      const seeded = {};
      (data.keys || []).forEach((k) => {
        seeded[k.key] = data.resolved?.[lang]?.[k.key] ?? '';
      });
      return { ...prev, [lang]: seeded };
    });
  }, [data, lang]);

  if (!data) return <Box sx={{ p: 2 }}>Loading…</Box>;

  const defaults = data.defaults?.[lang] || {};
  const current = texts[lang] || {};

  const setValue = (key, value) =>
    setTexts((prev) => ({
      ...prev,
      [lang]: { ...(prev[lang] || {}), [key]: value },
    }));

  const setTitle = (topicId, value) =>
    setTitles((prev) => ({
      ...prev,
      [topicId]: { ...(prev[topicId] || {}), [lang]: value },
    }));

  const save = async () => {
    setSaving(true);
    try {
      // Copy overrides: store only values that differ from the shipped default
      // for every language visited in this editing session; untouched
      // languages keep their stored overrides as-is.
      const value = JSON.parse(JSON.stringify(data.overrides || {}));
      Object.entries(texts).forEach(([lg, entries]) => {
        const langDefaults = data.defaults?.[lg] || {};
        const edited = {};
        (data.keys || []).forEach((k) => {
          const v = entries[k.key];
          if (typeof v === 'string' && v.trim() && v !== (langDefaults[k.key] || '')) {
            edited[k.key] = v;
          }
        });
        if (Object.keys(edited).length) value[lg] = edited;
        else delete value[lg];
      });
      await httpClient(withProduct(`${API_URL}/admin/translations`), {
        method: 'PUT',
        body: JSON.stringify({ value }),
      });

      // Topic names: upsert each topic whose title was edited.
      const changed = Object.entries(titles).filter(([, perLang]) =>
        Object.keys(perLang).length
      );
      for (const [topicId, perLang] of changed) {
        const topic = topics.find((t) => String(t.id) === String(topicId));
        if (!topic) continue;
        const title = { ...topic.title };
        Object.entries(perLang).forEach(([lg, v]) => {
          if (v && v.trim()) title[lg] = v;
          else delete title[lg];
        });
        await httpClient(`${API_URL}/admin/kb/topics`, {
          method: 'POST',
          body: JSON.stringify({
            slug: topic.slug,
            title,
            order: topic.display_order ?? 0,
            active: topic.active ?? true,
            product_id: getProductId() ?? null,
          }),
        });
      }

      notify('Translations saved — live', { type: 'success' });
      await load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box sx={{ p: 2, maxWidth: 1000 }}>
      <Title title="Translations" />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Everything the player sees, editable per language: the widget texts,
        the assistant's service replies (incl. the per-language{' '}
        <code>contact_url</code> escalation button link), the retention bot
        copy and the topic names. Clearing a field falls back to the shipped
        default (shown as placeholder).
      </Typography>
      <Tabs
        value={lang}
        onChange={(e, v) => setLang(v)}
        sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
        variant="scrollable"
        allowScrollButtonsMobile
      >
        {(data.languages || []).map((l) => (
          <Tab key={l.code} value={l.code} label={`${l.name} (${l.code})`} />
        ))}
      </Tabs>

      {SCOPES.map(([scope, title, help]) => {
        const keys = (data.keys || []).filter((k) => k.scope === scope);
        if (!keys.length) return null;
        return (
          <Card key={scope} sx={{ mb: 2 }}>
            <CardContent>
              <Typography variant="h6">{title}</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                {help}
              </Typography>
              {keys.map((k) => (
                <TextField
                  key={k.key}
                  label={k.key}
                  helperText={k.description}
                  value={current[k.key] ?? ''}
                  placeholder={defaults[k.key] ?? ''}
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

      {topics.length > 0 && (
        <Card sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="h6">Topic names</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              The topic picker buttons, per language. Stored on the topic
              itself; a missing translation falls back to English.
            </Typography>
            {topics.map((t) => (
              <TextField
                key={t.id}
                label={`${t.slug} (en: ${t.title?.en || '—'})`}
                value={titles[t.id]?.[lang] ?? t.title?.[lang] ?? ''}
                placeholder={t.title?.en || ''}
                onChange={(e) => setTitle(t.id, e.target.value)}
                fullWidth
                margin="dense"
              />
            ))}
          </CardContent>
        </Card>
      )}

      <Stack direction="row" spacing={1}>
        <Button variant="contained" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save translations'}
        </Button>
      </Stack>
    </Box>
  );
};

export default Translations;
