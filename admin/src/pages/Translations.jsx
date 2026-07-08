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
import RequireProduct from '../components/RequireProduct';

// Keys that are service/error notices rather than the bot's own voice — split
// into their own block so the "what the player hears from the bot" sections
// stay clean and easy to tune.
const SERVICE_KEYS = new Set([
  'startError',
  'sendError',
  'switchStuck',
  'low_content_reply',
  'model_error_reply',
  'rtn_need_deeplink',
  'rtn_not_subscribed',
  'rtn_rate_limited',
  'rtn_low_content_reply',
  'rtn_injection_reply',
]);

const SECTIONS = [
  {
    id: 'widget',
    title: 'General — widget interface',
    help: 'Chrome strings rendered by the widget itself: header, topic picker, buttons, input placeholder.',
    match: (k) => k.scope === 'widget' && !SERVICE_KEYS.has(k.key),
  },
  {
    id: 'support',
    title: 'Support bot — messages to the player',
    help: 'What the support bot itself says to the player: the escalation card and its button (incl. the per-language contact_url link) and the closing option.',
    match: (k) => k.scope === 'server' && !SERVICE_KEYS.has(k.key),
  },
  {
    id: 'retention',
    title: 'Retention bot (Telegram) — messages to the player',
    help: 'What the Telegram retention bot says: the entry menu and its buttons, the subscription gate, the manager hand-off, the proactive-ping header and the /stop-/resume confirmations.',
    match: (k) => k.scope === 'retention' && !SERVICE_KEYS.has(k.key),
  },
  {
    id: 'service',
    title: 'Service and error notices',
    help: 'Technical fallbacks shown on failures and guards (errors, rate limit, low-content and injection nudges) — rarely need brand tuning.',
    match: (k) => SERVICE_KEYS.has(k.key),
  },
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
const TranslationsInner = () => {
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
        Everything the player sees, editable per language and split into
        blocks: the general widget interface, the support bot's messages to
        the player (incl. the per-language <code>contact_url</code> escalation
        button link), the Telegram retention bot's messages, and the service /
        error notices — plus the topic names. Clearing a field falls back to
        the shipped default (shown as placeholder).
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

      {SECTIONS.map(({ id, title, help, match }) => {
        const keys = (data.keys || []).filter(match);
        if (!keys.length) return null;
        return (
          <Card key={id} sx={{ mb: 2 }}>
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

const Translations = () => (
  <RequireProduct title="Translations">
    <TranslationsInner />
  </RequireProduct>
);

export default Translations;
