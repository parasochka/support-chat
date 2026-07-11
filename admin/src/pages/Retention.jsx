import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Title, useNotify, usePermissions } from 'react-admin';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Checkbox from '@mui/material/Checkbox';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import FormControlLabel from '@mui/material/FormControlLabel';
import Grid from '@mui/material/Grid';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Tab from '@mui/material/Tab';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import Link from '@mui/material/Link';
import MuiPagination from '@mui/material/Pagination';
import { API_URL, httpClient, getToken } from '../httpClient';
import { getProductId } from '../productScope';
import {
  FunnelBars,
  MiniBarChart,
  SeriesLineChart,
  TelegramCostCharts,
} from '../components/charts';
import RequireProduct from '../components/RequireProduct';
import useIsMobile from '../lib/useIsMobile';
import SecretField from '../components/SecretField';
import TextStats from '../components/TextStats';
import rich from '../components/Rich';
import { t } from '../i18n';

// ---------------------------------------------------------------------------
// Setup guide tab — the short "how to connect the bot" checklist that replaced
// the repo's RETENTION_SETUP.md. Everything product-level is configured right
// here in this section; only deploy env vars live outside.
// ---------------------------------------------------------------------------
const GUIDE_STEPS = [
  {
    title: t('1 · Create the bot'),
    body: rich(
      t(
        'Open [@BotFather](https://t.me/BotFather) → `/newbot`, pick a name and a username, copy the **token**. Optionally set the description, about text and avatar there too. Menu commands are not needed — players enter only via a deeplink from the site.'
      )
    ),
  },
  {
    title: t('2 · Create the channel (subscription gate)'),
    body: rich(
      t(
        'Create a Telegram **channel** and add the bot as a **channel administrator** — without admin rights the subscription check (`getChatMember`) fails and the gate never passes. Note the channel id (`@name` for public, `-100…` for private) and the channel URL (the gate\'s "open channel" button leads there).'
      )
    ),
  },
  {
    title: t('3 · Deploy env (Railway)'),
    body: rich(
      t(
        'Set on the service (not per product): `PUBLIC_BASE_URL` (public address, used to build the webhook URL), `TELEGRAM_WEBHOOK_SECRET` (random string, verified in the webhook header), `RETENTION_MEDIA_DIR` (mount path of an attached **Volume**, so photos survive redeploys) and `SECRETS_MASTER_KEY` (encrypts product secrets). The full env table is in the repo\'s README.'
      )
    ),
  },
  {
    title: t('4 · Connect this product'),
    body: rich(
      t(
        'On the [Telegram config](#/retention?tab=config) tab: switch on **Retention bot enabled**, fill the bot username, channel id and channel URL → **Save config**. In **Secrets** paste the bot token (and the Player API key, if the casino exposes a profile endpoint) → **Save secrets**. Then press **Register Telegram webhook** — it must report the webhook URL back.'
      )
    ),
  },
  {
    title: t('5 · Content and tuning'),
    body: rich(
      t(
        'Review the [Retention KB](#/retention?tab=kb) (one text document — what Nika may offer and talk about; a generic English starter is pre-filled, replace it with the brand\'s own), tune the Telegram persona in [Prompt variables](#/retention?tab=variables) (name/role/tone — empty fields use the built-in retention defaults), upload photos in [Media](#/retention?tab=photos) (bulk upload, then select them and press **Generate metadata** to have the AI fill the description, tags, `stage` = explicitness and `level_min` = VIP tier) and add live [Managers](#/retention?tab=managers) (round-robin, sticky). Thresholds (daily photo cap, stage progression, VIP tiers, nonce TTL) are the `retention` group in [Settings](#/settings); bot texts are the `rtn_*` keys in [Translations](#/translations).'
      )
    ),
  },
  {
    title: t('6 · Entry points'),
    body: rich(
      t(
        'Nothing extra to integrate for the main path: once the bot is enabled, the support widget\'s **escalation button** automatically routes the player into the bot (one-time deeplink, subscription gate on the way in, "go to a manager" in the menu). Optionally the site can mint its own per-player deeplink via `POST /api/retention/deeplink` — the full contract (handshake signing, profile pull/push) is documented at [/integration-telegram](/integration-telegram).'
      )
    ),
  },
];

const GuideTab = () => (
  <Box>
    {GUIDE_STEPS.map((s) => (
      <Card key={s.title} sx={{ mb: 1.5 }}>
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>
            {s.title}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {s.body}
          </Typography>
        </CardContent>
      </Card>
    ))}
    <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
      {t(
        'Quick test: open the deeplink → pass the channel gate → chat with Nika → ask for a photo → it arrives; write "my account is blocked" → she routes you out instead of answering support questions herself.'
      )}
    </Typography>
  </Box>
);

// ---------------------------------------------------------------------------
// Telegram config tab
// ---------------------------------------------------------------------------
const ConfigTab = ({ productId }) => {
  const notify = useNotify();
  const [data, setData] = useState(null);
  const [form, setForm] = useState({});
  const [secrets, setSecrets] = useState({});

  const load = useCallback(() => {
    httpClient(`${API_URL}/admin/retention/telegram/${productId}`)
      .then(({ json }) => {
        setData(json);
        const p = json.product || {};
        setForm({
          telegram_bot_username: p.telegram_bot_username || '',
          telegram_channel_id: p.telegram_channel_id || '',
          telegram_channel_url: p.telegram_channel_url || '',
          player_api_url: p.player_api_url || '',
          retention_enabled: Boolean(p.retention_enabled),
        });
      })
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    try {
      await httpClient(`${API_URL}/admin/retention/telegram/${productId}`, {
        method: 'PUT',
        body: JSON.stringify(form),
      });
      notify(t('Telegram config saved'), { type: 'success' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    }
  };

  const saveSecrets = async () => {
    // Only send fields the operator actually typed into (a non-empty string).
    // Clearing is an explicit action (clearSecret), not "leave the box empty".
    const fields = Object.fromEntries(
      Object.entries(secrets).filter(([, v]) => v !== undefined && v !== '')
    );
    if (!Object.keys(fields).length) return;
    try {
      await httpClient(`${API_URL}/admin/products/${productId}/secrets`, {
        method: 'PUT',
        body: JSON.stringify(fields),
      });
      notify(t('Secrets saved'), { type: 'success' });
      setSecrets({});
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    }
  };

  const clearSecret = async (field, label) => {
    if (
      !window.confirm(
        t('Clear {label}? It falls back to the deploy env value.').replace('{label}', label)
      )
    ) {
      return;
    }
    try {
      await httpClient(`${API_URL}/admin/products/${productId}/secrets`, {
        method: 'PUT',
        body: JSON.stringify({ [field]: '' }),
      });
      notify(t('{label} cleared').replace('{label}', label), { type: 'success' });
      setSecrets({ ...secrets, [field]: '' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Clear failed'), { type: 'error' });
    }
  };

  const registerWebhook = async () => {
    try {
      const { json } = await httpClient(
        `${API_URL}/admin/retention/webhook/${productId}`,
        { method: 'POST' }
      );
      notify(`${t('Webhook registered:')} ${json.webhook_url || 'ok'}`, { type: 'success' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Webhook registration failed'), {
        type: 'error',
      });
    }
  };

  if (!data) return <Box sx={{ p: 2 }}>{t('Loading…')}</Box>;
  const p = data.product || {};

  return (
    <Card>
      <CardContent>
        <FormControlLabel
          control={
            <Switch
              checked={Boolean(form.retention_enabled)}
              onChange={(e) => setForm({ ...form, retention_enabled: e.target.checked })}
            />
          }
          label={t('Retention bot enabled')}
        />
        {[
          ['telegram_bot_username', t('Bot username (without @)')],
          ['telegram_channel_id', t('Channel id (@channel or -100…)')],
          ['telegram_channel_url', t('Channel URL (subscription gate)')],
          ['player_api_url', t('Player API URL (profile pull)')],
        ].map(([f, label]) => (
          <TextField
            key={f}
            label={label}
            value={form[f] ?? ''}
            onChange={(e) => setForm({ ...form, [f]: e.target.value })}
            fullWidth
            margin="dense"
          />
        ))}
        <Button variant="contained" onClick={save} sx={{ mt: 1, mr: 1 }}>
          {t('Save config')}
        </Button>
        <Button variant="outlined" onClick={registerWebhook} sx={{ mt: 1 }}>
          {t('Register Telegram webhook')}
        </Button>
        {data.webhook_url && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {t('Webhook URL:')} <code>{data.webhook_url}</code>
          </Typography>
        )}
        <Typography variant="h6" sx={{ mt: 2 }}>
          {t('Secrets')}
        </Typography>
        <SecretField
          label={t('Telegram bot token')}
          set={Boolean(p.has_telegram_bot_token)}
          value={secrets.telegram_bot_token}
          onChange={(e) => setSecrets({ ...secrets, telegram_bot_token: e.target.value })}
          onClear={() => clearSecret('telegram_bot_token', t('Telegram bot token'))}
        />
        <SecretField
          label={t('Player API key')}
          set={Boolean(p.has_player_api_key)}
          value={secrets.player_api_key}
          onChange={(e) => setSecrets({ ...secrets, player_api_key: e.target.value })}
          onClear={() => clearSecret('player_api_key', t('Player API key'))}
        />
        <Button variant="contained" size="small" onClick={saveSecrets} sx={{ mt: 1 }}>
          {t('Save secrets')}
        </Button>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Retention KB tab — ONE free-text document per product, edited exactly like a
// support topic's KB text: paste, change, save. New products arrive with the
// generic English starter document already seeded.
// ---------------------------------------------------------------------------
const KbTab = ({ productId }) => {
  const notify = useNotify();
  const [text, setText] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    httpClient(`${API_URL}/admin/retention/kb/text?product_id=${productId}`)
      .then(({ json }) => setText(json.text ?? ''))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify]);

  const save = async () => {
    setSaving(true);
    try {
      const { json } = await httpClient(
        `${API_URL}/admin/retention/kb/text?product_id=${productId}`,
        { method: 'PUT', body: JSON.stringify({ text }) }
      );
      setText(json.text ?? '');
      notify(t('Retention KB saved'), { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  if (text === null) return <Box sx={{ p: 2 }}>{t('Loading…')}</Box>;

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          {rich(
            t(
              'The whole retention knowledge base as one text (Layer 2 of the retention prompt — what Nika may offer and talk about in Telegram). Keep it in English: it is the most token-efficient language for the model, and Nika answers in the player\'s language regardless. `{placeholders}` are substituted from KB variables.'
            )
          )}
        </Typography>
        <Alert severity="info" sx={{ mb: 1 }}>
          <b>{t('English only')}.</b>{' '}
          {t(
            'Model-facing content must be in English — the backend rejects other scripts. Player-facing copy belongs in Translations.'
          )}
        </Alert>
        <TextStats text={text} />
        <TextField
          value={text}
          onChange={(e) => setText(e.target.value)}
          multiline
          minRows={20}
          fullWidth
        />
        <Button variant="contained" onClick={save} disabled={saving} sx={{ mt: 1.5 }}>
          {t('Save')}
        </Button>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Prompt variables tab — the Telegram-persona values (name, role, brand,
// products, tone of voice). A SEPARATE prompt with its own defaults: an empty
// field falls back to the built-in retention default, never to the support
// chat's value, so a support edit can never leak into the bot.
// ---------------------------------------------------------------------------
const VariablesTab = ({ productId }) => {
  const notify = useNotify();
  const [vars, setVars] = useState(null);
  const [values, setValues] = useState({});
  const [saving, setSaving] = useState(false);

  const apply = useCallback((variables) => {
    setVars(variables || []);
    const v = {};
    (variables || []).forEach((x) => {
      v[x.key] = x.value ?? '';
    });
    setValues(v);
  }, []);

  useEffect(() => {
    httpClient(
      `${API_URL}/admin/retention/prompt-variables?product_id=${productId}`
    )
      .then(({ json }) => apply(json.variables))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify, apply]);

  const save = async () => {
    setSaving(true);
    try {
      const { json } = await httpClient(
        `${API_URL}/admin/retention/prompt-variables?product_id=${productId}`,
        { method: 'PUT', body: JSON.stringify({ value: values }) }
      );
      apply(json.variables);
      notify(t('Retention prompt variables saved'), { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  if (vars === null) return <Box sx={{ p: 2 }}>{t('Loading…')}</Box>;

  return (
    <Box sx={{ maxWidth: 900 }}>
      <Alert severity="info" sx={{ mb: 2 }}>
        {rich(
          t(
            'These values uniquify the **Telegram retention persona** — a **separate prompt**, fully independent from the [support-chat prompt variables](#/prompt?tab=variables). An empty field **uses the built-in retention default** (shown as the placeholder); a support edit never leaks into the bot. Fill a field only where you want the Telegram persona to differ from that default.'
          )
        )}
      </Alert>
      <Card>
        <CardContent>
          <Alert severity="info" sx={{ mb: 1 }}>
            <b>{t('English only')}.</b>{' '}
            {t(
              'Model-facing content must be in English — the backend rejects other scripts. Player-facing copy belongs in Translations.'
            )}
          </Alert>
          <TextStats
            label={t('Total')}
            text={vars.map((v) => values[v.key] || v.default || '')}
          />
          {vars.map((v) => (
            <TextField
              key={v.key}
              label={v.key}
              helperText={`${v.description} ${t('Empty = the built-in retention default.')}`}
              value={values[v.key] ?? ''}
              onChange={(e) => setValues({ ...values, [v.key]: e.target.value })}
              placeholder={v.default}
              fullWidth
              multiline
              margin="normal"
            />
          ))}
          <Button variant="contained" onClick={save} disabled={saving} sx={{ mt: 1 }}>
            {saving ? t('Saving…') : t('Save variables')}
          </Button>
        </CardContent>
      </Card>
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Prompt preview tab — read-only view of the assembled RETENTION prompt.
// Mirrors the support Prompt → Preview page exactly: it shows ONLY the
// assembled prompt (no variables table — those already-resolved values just
// took up space here). The variable VALUES are edited on the Prompt variables
// tab, same as the support prompt.
// ---------------------------------------------------------------------------
const PreviewBlock = ({ title, text }) => (
  <Card sx={{ mb: 2 }}>
    <CardContent>
      <Typography variant="h6" gutterBottom>
        {title}
      </Typography>
      <TextStats text={text || ''} sx={{ mb: 1 }} />
      <Typography
        component="pre"
        sx={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 13, m: 0 }}
      >
        {text || '—'}
      </Typography>
    </CardContent>
  </Card>
);

const PromptTab = ({ productId }) => {
  const notify = useNotify();
  const [preview, setPreview] = useState(null);

  useEffect(() => {
    httpClient(`${API_URL}/admin/retention/effective-prompt?product_id=${productId}`)
      .then(({ json }) => setPreview(json.effective_preview))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify]);

  return (
    <Box sx={{ maxWidth: 1000 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {rich(
          t(
            'The complete retention prompt as the model receives it in the Telegram chat (read-only; language: {lang}). To change the wording, edit `prompts.py` and redeploy; the brand values are on the [Prompt variables](#/retention?tab=variables) tab.'
          ).replace('{lang}', preview?.example?.lang || '—')
        )}
      </Typography>
      <TextStats
        label={t('Total')}
        text={[preview?.system, preview?.user]}
        sx={{ mb: 1.5 }}
      />
      <PreviewBlock
        title={t('System message (retention Layer 1 core + Layer 2 retention KB)')}
        text={preview?.system}
      />
      <PreviewBlock
        title={t('User message (Layer 3: profile, language, photo candidates, guardrails)')}
        text={preview?.user}
      />
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Photos tab (media library; binary preview needs the auth header -> blob)
// ---------------------------------------------------------------------------
// Session-lived cache of the fetched preview object URLs, keyed by photo id.
// The binary is immutable per id, so once fetched a preview is reused across
// re-renders, pagination and filter changes instead of re-downloading — the
// slow-loading complaint. The URLs are intentionally never revoked: they live
// for the tab's lifetime (bounded by how many distinct photos exist).
const photoUrlCache = new Map();

const PhotoPreview = ({ photoId }) => {
  const [src, setSrc] = useState(() => photoUrlCache.get(photoId) || null);
  useEffect(() => {
    const cached = photoUrlCache.get(photoId);
    if (cached) {
      setSrc(cached);
      return undefined;
    }
    let cancelled = false;
    fetch(`${API_URL}/admin/retention/photos/${photoId}/file`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => {
        if (blob && !cancelled) {
          const url = URL.createObjectURL(blob);
          photoUrlCache.set(photoId, url);
          setSrc(url);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [photoId]);
  const frame = {
    width: '100%',
    height: 180,
    bgcolor: 'action.hover',
    borderRadius: 1,
    overflow: 'hidden',
  };
  if (!src) return <Box sx={frame} />;
  return (
    <Box sx={frame}>
      <img
        src={src}
        alt=""
        style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
      />
    </Box>
  );
};

// A pagination bar that mirrors react-admin's default <Pagination> look (the
// Conversations list): a "1–N of M" range on the left and numbered page buttons
// on the right. Used by the client-paginated grids/tables in this page so they
// match the rest of the admin. `count` is the total row count, `page` is
// 1-based, `perPage` the page size.
const GridPagination = ({ count, page, perPage, onPage, unit = t('items') }) => {
  const pageCount = Math.max(1, Math.ceil(count / perPage));
  const from = count === 0 ? 0 : (page - 1) * perPage + 1;
  const to = Math.min(page * perPage, count);
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      flexWrap="wrap"
      useFlexGap
      spacing={1}
      sx={{ mt: 2, px: 1 }}
    >
      <Typography variant="body2" color="text.secondary">
        {from}–{to} {t('of')} {count} {unit}
      </Typography>
      <MuiPagination
        color="primary"
        size="small"
        count={pageCount}
        page={Math.min(page, pageCount)}
        onChange={(_e, value) => onPage(value)}
        showFirstButton
        showLastButton
      />
    </Stack>
  );
};

// How many photos ride in one generate-metadata request; larger selections are
// chunked client-side so a slow vision batch can't hit the request timeout.
const META_CHUNK = 10;

// Photos shown per page. The whole library is loaded once (filtering is
// client-side); paginating the grid keeps only ~21 previews fetching at a
// time instead of every photo at once.
const PHOTOS_PER_PAGE = 21;

const PhotosTab = ({ productId }) => {
  const notify = useNotify();
  const [items, setItems] = useState([]);
  const [upload, setUpload] = useState({ description: '', tags: '', level_min: 0, stage: 1, category: '' });
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [generating, setGenerating] = useState(false);
  const [genProgress, setGenProgress] = useState('');
  const [filters, setFilters] = useState({ q: '', stage: 'all', level: 'all', status: 'all' });
  const [page, setPage] = useState(1);
  // The product's real gate ranges — Stage 1..maxStage, Level 0..tiers-1 — so
  // the pickers below can only offer values the delivery gate can actually serve
  // (no stage 0 or 6, no VIP tier past the last one).
  const [gate, setGate] = useState({ tiers: ['none'], maxStage: 5 });

  const load = useCallback(() => {
    httpClient(`${API_URL}/admin/retention/photos?product_id=${productId}`)
      .then(({ json }) => setItems(json.items || []))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    httpClient(`${API_URL}/admin/settings?product_id=${productId}`)
      .then(({ json }) => {
        const rt = json?.resolved?.retention || {};
        setGate({
          tiers: (rt.vip_tiers || ['none']).map((t) => String(t)),
          maxStage: Math.max(1, Number(rt.max_stage) || 5),
        });
      })
      .catch(() => {});
  }, [productId]);

  const stageChoices = Array.from({ length: gate.maxStage }, (_, i) => i + 1);
  const levelChoices = gate.tiers.map((t, i) => ({ value: i, label: `${i} · ${t}` }));

  const doUpload = async () => {
    if (!files.length) return;
    setUploading(true);
    const fd = new FormData();
    fd.append('product_id', String(productId));
    fd.append('description', upload.description);
    fd.append('tags', upload.tags);
    fd.append('level_min', String(upload.level_min));
    fd.append('stage', String(upload.stage));
    fd.append('category', upload.category);
    files.forEach((f) => fd.append('files', f));
    try {
      const res = await fetch(`${API_URL}/admin/retention/photos`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${getToken()}` },
        body: fd,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        notify(body.detail || t('Upload failed'), { type: 'error' });
        return;
      }
      const body = await res.json().catch(() => ({}));
      const uploaded = (body.photos || []).length || 1;
      notify(t('{n} photo(s) uploaded').replace('{n}', uploaded), {
        type: 'success',
      });
      setFiles([]);
      load();
    } catch (e) {
      // Network failure: without this the rejection escapes the click handler
      // and the operator gets no feedback at all.
      notify(e.message || t('Upload failed'), { type: 'error' });
    } finally {
      setUploading(false);
    }
  };

  const patch = async (id, fields) => {
    try {
      await httpClient(`${API_URL}/admin/retention/photos/${id}`, {
        method: 'PUT',
        body: JSON.stringify(fields),
      });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    }
  };

  const remove = async (id) => {
    if (!window.confirm(t('Delete this photo?'))) return;
    try {
      await httpClient(`${API_URL}/admin/retention/photos/${id}`, { method: 'DELETE' });
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Delete failed'), { type: 'error' });
    }
  };

  // --- filters (client-side over the loaded library) ---
  const visible = items.filter((ph) => {
    if (filters.status !== 'all' && Boolean(ph.active) !== (filters.status === 'active')) {
      return false;
    }
    if (filters.stage !== 'all' && Number(ph.stage) !== Number(filters.stage)) return false;
    if (filters.level !== 'all' && Number(ph.level_min) !== Number(filters.level)) return false;
    if (filters.q) {
      const hay = `${ph.description || ''} ${(ph.tags || []).join(' ')} ${ph.category || ''}`.toLowerCase();
      if (!hay.includes(filters.q.toLowerCase())) return false;
    }
    return true;
  });
  const stageOptions = [...new Set(items.map((ph) => Number(ph.stage)))].sort((a, b) => a - b);
  const levelOptions = [...new Set(items.map((ph) => Number(ph.level_min)))].sort((a, b) => a - b);

  // --- client-side pagination over the filtered set ---
  const pageCount = Math.max(1, Math.ceil(visible.length / PHOTOS_PER_PAGE));
  const safePage = Math.min(page, pageCount);
  const pageItems = visible.slice(
    (safePage - 1) * PHOTOS_PER_PAGE,
    safePage * PHOTOS_PER_PAGE
  );
  // A filter change can shrink the list below the current page; snap back.
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);
  const setFilter = (patch) => {
    setFilters((f) => ({ ...f, ...patch }));
    setPage(1);
  };

  // --- selection + AI metadata generation ---
  const toggleSelect = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const selectAllVisible = () => setSelected(new Set(visible.map((ph) => ph.id)));

  const generate = async () => {
    const ids = [...selected];
    if (!ids.length || generating) return;
    if (
      !window.confirm(
        t(
          'Generate metadata for {n} photo(s)? The AI fills the description, tags, stage and VIP level; current values are overwritten.'
        ).replace('{n}', ids.length)
      )
    ) {
      return;
    }
    setGenerating(true);
    let ok = 0;
    let failed = 0;
    const errors = [];
    // Each chunk fails independently — a mid-batch 500/network error must not
    // discard the counts of chunks that already succeeded server-side.
    for (let i = 0; i < ids.length; i += META_CHUNK) {
      const chunk = ids.slice(i, i + META_CHUNK);
      setGenProgress(`${Math.min(i + chunk.length, ids.length)} / ${ids.length}`);
      try {
        const { json } = await httpClient(
          `${API_URL}/admin/retention/photos/generate-metadata?product_id=${productId}`,
          { method: 'POST', body: JSON.stringify({ ids: chunk }) }
        );
        (json.results || []).forEach((r) => {
          if (r.ok) ok += 1;
          else {
            failed += 1;
            errors.push(`#${r.id}: ${r.error}`);
          }
        });
      } catch (e) {
        failed += chunk.length;
        errors.push(e.body?.detail || e.message || t('request failed'));
      }
    }
    try {
      if (failed) {
        notify(
          `${t('Metadata: {ok} generated, {failed} failed')
            .replace('{ok}', ok)
            .replace('{failed}', failed)} (${errors.slice(0, 3).join('; ')}${errors.length > 3 ? '…' : ''})`,
          { type: 'warning' }
        );
      } else {
        notify(t('Metadata generated for {n} photo(s)').replace('{n}', ok), {
          type: 'success',
        });
      }
      setSelected(new Set());
    } finally {
      setGenerating(false);
      setGenProgress('');
      load();
    }
  };

  return (
    <Box>
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('Upload photos')}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {rich(
              t(
                'Pick any number of files at once. The fields below apply to every uploaded photo — leave them empty and use **Generate metadata** afterwards to have the AI fill the description, tags, explicitness stage and VIP level per photo.'
              )
            )}
          </Typography>
          <Grid container spacing={1.5} sx={{ mb: 1 }}>
            <Grid size={{ xs: 12 }}>
              <TextField
                size="small"
                label={t('Description (grounds the caption the model writes)')}
                value={upload.description}
                onChange={(e) => setUpload({ ...upload, description: e.target.value })}
                fullWidth
                multiline
              />
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 4 }}>
              <TextField
                size="small"
                label={t('Tags (comma-separated)')}
                value={upload.tags}
                onChange={(e) => setUpload({ ...upload, tags: e.target.value })}
                fullWidth
              />
            </Grid>
            <Grid size={{ xs: 6, sm: 3, md: 2 }}>
              <TextField
                select
                size="small"
                label={t('Level (min VIP tier)')}
                value={upload.level_min}
                onChange={(e) => setUpload({ ...upload, level_min: Number(e.target.value) })}
                helperText={t('VIP tier to unlock')}
                fullWidth
              >
                {levelChoices.map((o) => (
                  <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>
                ))}
              </TextField>
            </Grid>
            <Grid size={{ xs: 6, sm: 3, md: 2 }}>
              <TextField
                select
                size="small"
                label={t('Stage (explicitness)')}
                value={upload.stage}
                onChange={(e) => setUpload({ ...upload, stage: Number(e.target.value) })}
                helperText={t('1 = softest')}
                fullWidth
              >
                {stageChoices.map((s) => (
                  <MenuItem key={s} value={s}>{`${t('Stage')} ${s}`}</MenuItem>
                ))}
              </TextField>
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 4 }}>
              <TextField
                size="small"
                label={t('Category')}
                value={upload.category}
                onChange={(e) => setUpload({ ...upload, category: e.target.value })}
                fullWidth
              />
            </Grid>
          </Grid>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Button variant="outlined" component="label">
              {files.length
                ? t('{n} files chosen').replace('{n}', files.length)
                : t('Choose files')}
              <input
                hidden
                type="file"
                accept="image/*"
                multiple
                onChange={(e) => setFiles([...e.target.files])}
              />
            </Button>
            <Button variant="contained" onClick={doUpload} disabled={!files.length || uploading}>
              {uploading ? t('Uploading…') : t('Upload')}
            </Button>
          </Stack>
        </CardContent>
      </Card>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
          >
            <TextField
              size="small"
              label={t('Search (description, tags, category)')}
              value={filters.q}
              onChange={(e) => setFilter({ q: e.target.value })}
              sx={{ minWidth: 240 }}
            />
            <TextField
              select
              size="small"
              label={t('Stage')}
              value={filters.stage}
              onChange={(e) => setFilter({ stage: e.target.value })}
              sx={{ minWidth: 110 }}
            >
              <MenuItem value="all">{t('all')}</MenuItem>
              {stageOptions.map((s) => (
                <MenuItem key={s} value={String(s)}>
                  {s}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label={t('Level min')}
              value={filters.level}
              onChange={(e) => setFilter({ level: e.target.value })}
              sx={{ minWidth: 110 }}
            >
              <MenuItem value="all">{t('all')}</MenuItem>
              {levelOptions.map((l) => (
                <MenuItem key={l} value={String(l)}>
                  {l}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label={t('Status')}
              value={filters.status}
              onChange={(e) => setFilter({ status: e.target.value })}
              sx={{ minWidth: 110 }}
            >
              <MenuItem value="all">{t('all')}</MenuItem>
              <MenuItem value="active">{t('active')}</MenuItem>
              <MenuItem value="inactive">{t('inactive')}</MenuItem>
            </TextField>
            <Typography variant="body2" color="text.secondary">
              {t('{shown} of {total} photos')
                .replace('{shown}', visible.length)
                .replace('{total}', items.length)}
            </Typography>
          </Stack>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
            sx={{ mt: 1.5 }}
          >
            <Button size="small" onClick={selectAllVisible} disabled={!visible.length}>
              {t('Select all shown')}
            </Button>
            <Button
              size="small"
              onClick={() => setSelected(new Set())}
              disabled={!selected.size}
            >
              {t('Clear selection')}
            </Button>
            <Button
              variant="contained"
              size="small"
              onClick={generate}
              disabled={!selected.size || generating}
            >
              {generating
                ? `${t('Generating…')} ${genProgress}`
                : `${t('Generate metadata')} (${selected.size})`}
            </Button>
            <Typography variant="caption" color="text.secondary">
              {t(
                "AI (the product's own model + API key) fills the description, tags, stage and minimum VIP level for every selected photo."
              )}
            </Typography>
          </Stack>
        </CardContent>
      </Card>

      {items.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          {t('No photos yet — upload the first ones above.')}
        </Typography>
      )}
      <Grid container spacing={2} alignItems="stretch">
        {pageItems.map((ph) => (
          <Grid size={{ xs: 12, sm: 6, md: 4 }} key={ph.id}>
            <Card
              sx={{
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
                outline: selected.has(ph.id) ? '2px solid' : 'none',
                outlineColor: 'primary.main',
              }}
            >
              <CardContent sx={{ flexGrow: 1 }}>
                <Box sx={{ position: 'relative' }}>
                  <PhotoPreview photoId={ph.id} />
                  <Checkbox
                    checked={selected.has(ph.id)}
                    onChange={() => toggleSelect(ph.id)}
                    sx={{
                      position: 'absolute',
                      top: 4,
                      left: 4,
                      bgcolor: 'background.paper',
                      borderRadius: 1,
                      p: 0.25,
                      '&:hover': { bgcolor: 'background.paper' },
                    }}
                  />
                </Box>
                <Stack
                  direction="row"
                  spacing={0.5}
                  flexWrap="wrap"
                  useFlexGap
                  sx={{ mt: 1 }}
                >
                  <Chip size="small" variant="outlined" label={`${t('stage')} ${ph.stage}`} />
                  <Chip size="small" variant="outlined" label={`${t('level')} ${ph.level_min}+`} />
                  {(ph.tags || []).slice(0, 4).map((t) => (
                    <Chip key={t} size="small" label={t} />
                  ))}
                  {(ph.tags || []).length > 4 && (
                    <Chip size="small" label={`+${ph.tags.length - 4}`} />
                  )}
                </Stack>
                <Stack spacing={1.5} sx={{ mt: 1.5 }}>
                  <TextField
                    size="small"
                    label={t('Description')}
                    defaultValue={ph.description || ''}
                    onBlur={(e) =>
                      e.target.value !== (ph.description || '') &&
                      patch(ph.id, { description: e.target.value })
                    }
                    fullWidth
                    multiline
                  />
                  <TextField
                    size="small"
                    label={t('Tags (comma-separated)')}
                    defaultValue={(ph.tags || []).join(', ')}
                    onBlur={(e) => {
                      const tags = e.target.value
                        .split(',')
                        .map((t) => t.trim().toLowerCase())
                        .filter(Boolean);
                      if (tags.join(',') !== (ph.tags || []).join(',')) {
                        patch(ph.id, { tags });
                      }
                    }}
                    fullWidth
                  />
                  <Stack direction="row" spacing={1}>
                    <TextField
                      select
                      size="small"
                      label={t('Level (min VIP)')}
                      value={ph.level_min}
                      onChange={(e) =>
                        Number(e.target.value) !== ph.level_min &&
                        patch(ph.id, { level_min: Number(e.target.value) })
                      }
                      fullWidth
                    >
                      {levelChoices.map((o) => (
                        <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>
                      ))}
                      {!levelChoices.some((o) => o.value === ph.level_min) && (
                        <MenuItem value={ph.level_min}>{`${ph.level_min} · (?)`}</MenuItem>
                      )}
                    </TextField>
                    <TextField
                      select
                      size="small"
                      label={t('Stage')}
                      value={ph.stage}
                      onChange={(e) =>
                        Number(e.target.value) !== ph.stage &&
                        patch(ph.id, { stage: Number(e.target.value) })
                      }
                      fullWidth
                    >
                      {stageChoices.map((s) => (
                        <MenuItem key={s} value={s}>{`${t('Stage')} ${s}`}</MenuItem>
                      ))}
                      {!stageChoices.includes(ph.stage) && (
                        <MenuItem value={ph.stage}>{`${t('Stage')} ${ph.stage}`}</MenuItem>
                      )}
                    </TextField>
                  </Stack>
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    justifyContent="space-between"
                    flexWrap="wrap"
                    useFlexGap
                  >
                    <FormControlLabel
                      control={
                        <Switch
                          size="small"
                          checked={Boolean(ph.active)}
                          onChange={(e) => patch(ph.id, { active: e.target.checked })}
                        />
                      }
                      label={t('Active')}
                    />
                    {ph.telegram_file_id && <Chip size="small" label={t('cached in TG')} />}
                    <Button size="small" color="error" onClick={() => remove(ph.id)}>
                      {t('Delete')}
                    </Button>
                  </Stack>
                </Stack>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>
      {visible.length > 0 && (
        <GridPagination
          count={visible.length}
          page={safePage}
          perPage={PHOTOS_PER_PAGE}
          onPage={setPage}
          unit={t('photos')}
        />
      )}
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Managers tab (round-robin hand-off targets)
// ---------------------------------------------------------------------------
const ManagersTab = ({ productId }) => {
  const notify = useNotify();
  const [items, setItems] = useState([]);
  const [form, setForm] = useState({ display_name: '', username: '' });

  const load = useCallback(() => {
    httpClient(`${API_URL}/admin/retention/managers?product_id=${productId}`)
      .then(({ json }) => setItems(json.items || []))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify]);

  useEffect(() => {
    load();
  }, [load]);

  const create = async () => {
    try {
      await httpClient(`${API_URL}/admin/retention/managers?product_id=${productId}`, {
        method: 'POST',
        body: JSON.stringify(form),
      });
      setForm({ display_name: '', username: '' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Create failed'), { type: 'error' });
    }
  };

  const patch = async (id, fields) => {
    try {
      await httpClient(`${API_URL}/admin/retention/managers/${id}`, {
        method: 'PUT',
        body: JSON.stringify(fields),
      });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    }
  };

  const remove = async (id) => {
    if (!window.confirm(t('Delete this manager?'))) return;
    try {
      await httpClient(`${API_URL}/admin/retention/managers/${id}`, { method: 'DELETE' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Delete failed'), { type: 'error' });
    }
  };

  return (
    <Box>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
        <TextField size="small" label={t('Display name')} value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        <TextField size="small" label={t('Telegram username (without @)')} value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <Button variant="outlined" onClick={create} disabled={!form.display_name || !form.username}>
          {t('Add manager')}
        </Button>
      </Stack>
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t('Name')}</TableCell>
              <TableCell>{t('Username')}</TableCell>
              <TableCell>{t('Active')}</TableCell>
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {items.map((m) => (
              <TableRow key={m.id}>
                <TableCell>{m.display_name}</TableCell>
                <TableCell>@{m.username}</TableCell>
                <TableCell>
                  <Switch size="small" checked={Boolean(m.active)} onChange={(e) => patch(m.id, { active: e.target.checked })} />
                </TableCell>
                <TableCell>
                  <Button size="small" color="error" onClick={() => remove(m.id)}>
                    {t('Delete')}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Conversations tab — the Telegram chats, logged apart from support. A chat
// "closes" when it sits idle past the `session_idle_minutes` retention knob
// (status becomes resolved); the player's next message starts a fresh chat
// that carries a short continuity tail from the previous one.
// ---------------------------------------------------------------------------
const ConversationsTab = ({ productId }) => {
  const isMobile = useIsMobile();
  const notify = useNotify();
  const { permissions } = usePermissions();
  const isAdmin = permissions === 'admin';
  const [data, setData] = useState({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState(null); // {session, messages, ...}
  const [selected, setSelected] = useState(() => new Set());
  const pageSize = 25;

  const load = useCallback(() => {
    httpClient(
      `${API_URL}/admin/retention/sessions?product_id=${productId}&page=${page}&page_size=${pageSize}`
    )
      .then(({ json }) => setData(json))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, page, notify]);

  useEffect(() => {
    load();
  }, [load]);

  const openTranscript = (id) => {
    httpClient(`${API_URL}/admin/session/${id}`)
      .then(({ json }) => setDetail(json))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  };

  const toggleSelect = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allSelected =
    data.items.length > 0 && data.items.every((s) => selected.has(s.id));
  const toggleSelectAll = () =>
    setSelected((prev) =>
      allSelected ? new Set() : new Set(data.items.map((s) => s.id))
    );

  const deleteIds = async (ids) => {
    if (!ids.length) return;
    const many = ids.length > 1;
    if (
      !window.confirm(
        many
          ? t(
              'Delete {n} Telegram chats? This permanently removes their messages and logs AND purges each linked player (identity, seen photos, pings) from analytics.'
            ).replace('{n}', ids.length)
          : t(
              'Delete this Telegram chat? This permanently removes its messages and logs AND purges the linked player (identity, seen photos, pings) from analytics.'
            )
      )
    ) {
      return;
    }
    try {
      await Promise.all(
        ids.map((id) =>
          httpClient(`${API_URL}/admin/session/${id}`, { method: 'DELETE' })
        )
      );
      notify(
        many ? t('{n} chats deleted').replace('{n}', ids.length) : t('Chat deleted'),
        { type: 'success' }
      );
      setSelected(new Set());
      setDetail(null);
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Delete failed'), { type: 'error' });
    }
  };

  const pages = Math.max(1, Math.ceil((data.total || 0) / pageSize));
  const cols = isAdmin ? 10 : 8;

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        {t(
          'Telegram chats with Nika, kept apart from the support-widget conversations. An idle chat closes automatically (the “Session idle (min)” knob in Settings → Retention bot); when the player returns, a new chat starts and Nika is shown the tail of the previous one for continuity. Click a row for the transcript.'
        )}
      </Alert>
      {isAdmin && (
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
          <Button
            size="small"
            color="error"
            variant="outlined"
            disabled={!selected.size}
            onClick={() => deleteIds([...selected])}
          >
            {t('Delete selected')} ({selected.size})
          </Button>
        </Stack>
      )}
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              {isAdmin && (
                <TableCell padding="checkbox">
                  <Checkbox
                    size="small"
                    checked={allSelected}
                    indeterminate={selected.size > 0 && !allSelected}
                    onChange={toggleSelectAll}
                  />
                </TableCell>
              )}
              <TableCell>{t('Player')}</TableCell>
              <TableCell>{t('TG user')}</TableCell>
              <TableCell>{t('Lang')}</TableCell>
              <TableCell>{t('Status')}</TableCell>
              <TableCell align="right">{t('Msgs')}</TableCell>
              <TableCell align="right">{t('Cost $')}</TableCell>
              <TableCell>{t('Started')}</TableCell>
              <TableCell>{t('Last activity')}</TableCell>
              {isAdmin && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {data.items.map((s) => (
              <TableRow
                key={s.id}
                hover
                sx={{ cursor: 'pointer' }}
                onClick={() => openTranscript(s.id)}
              >
                {isAdmin && (
                  <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                    <Checkbox
                      size="small"
                      checked={selected.has(s.id)}
                      onChange={() => toggleSelect(s.id)}
                    />
                  </TableCell>
                )}
                <TableCell>{s.full_name || s.player_id || '—'}</TableCell>
                <TableCell>
                  {s.tg_username ? `@${s.tg_username}` : s.tg_user_id || '—'}
                </TableCell>
                <TableCell>{s.lang || '—'}</TableCell>
                <TableCell>
                  <Chip
                    size="small"
                    label={s.status}
                    color={s.status === 'open' ? 'success' : 'default'}
                    variant="outlined"
                  />
                </TableCell>
                <TableCell align="right">{s.message_count}</TableCell>
                <TableCell align="right">
                  {s.cost_usd_total ? s.cost_usd_total.toFixed(4) : '0'}
                </TableCell>
                <TableCell>{new Date(s.created_at).toLocaleString()}</TableCell>
                <TableCell>{new Date(s.updated_at).toLocaleString()}</TableCell>
                {isAdmin && (
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Button
                      size="small"
                      color="error"
                      onClick={() => deleteIds([s.id])}
                    >
                      {t('Delete')}
                    </Button>
                  </TableCell>
                )}
              </TableRow>
            ))}
            {data.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={cols}>
                  <Typography color="text.secondary" sx={{ py: 2 }}>
                    {t('No Telegram chats yet.')}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Box>
      {pages > 1 && (
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
          <Button size="small" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            {t('Prev')}
          </Button>
          <Typography variant="body2">
            {page} / {pages} · {data.total} {t('chats')}
          </Typography>
          <Button size="small" disabled={page >= pages} onClick={() => setPage(page + 1)}>
            {t('Next')}
          </Button>
        </Stack>
      )}
      <Dialog open={!!detail} onClose={() => setDetail(null)} maxWidth="md" fullWidth fullScreen={isMobile}>
        <DialogTitle>
          {t('Telegram chat')} · {detail?.session?.id}
          {detail?.session?.status ? ` · ${detail.session.status}` : ''}
        </DialogTitle>
        <DialogContent dividers>
          <Stack spacing={1}>
            {[
              ...(detail?.messages || []).map((m) => ({ ...m, _kind: 'message' })),
              ...(detail?.photos || []).map((p) => ({ ...p, _kind: 'photo' })),
            ]
              .sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
              .map((item, i) =>
                item._kind === 'photo' ? (
                  <Box
                    key={`p${i}`}
                    sx={{ maxWidth: '80%', alignSelf: 'flex-end', width: { xs: 180, sm: 240 } }}
                  >
                    <Typography variant="caption" color="text.secondary">
                      {t('photo')} · {new Date(item.created_at).toLocaleString()}
                    </Typography>
                    <PhotoPreview photoId={item.photo_id} />
                    {item.description && (
                      <Typography variant="caption" color="text.secondary" display="block">
                        {item.description}
                      </Typography>
                    )}
                  </Box>
                ) : (
                  <Card
                    key={`m${i}`}
                    variant="outlined"
                    sx={{
                      maxWidth: '80%',
                      alignSelf: item.role === 'user' ? 'flex-start' : 'flex-end',
                      bgcolor: item.role === 'user' ? 'transparent' : 'action.hover',
                    }}
                  >
                    <CardContent sx={{ py: 1, '&:last-child': { pb: 1 } }}>
                      <Typography variant="caption" color="text.secondary">
                        {item.role} · {new Date(item.created_at).toLocaleString()}
                        {item.cost_usd ? ` · $${item.cost_usd.toFixed(5)}` : ''}
                        {item.ping_context ? ` · ⚡ ${t('proactive:')} ${item.ping_context}` : ''}
                      </Typography>
                      <Typography sx={{ whiteSpace: 'pre-wrap' }}>{item.content}</Typography>
                    </CardContent>
                  </Card>
                )
              )}
            {(detail?.messages || []).length === 0 && (
              <Typography color="text.secondary">{t('No messages.')}</Typography>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Typography variant="caption" color="text.secondary" sx={{ mr: 'auto', ml: 1 }}>
            {t('Total cost:')} ${detail?.cost_usd_total ?? 0}
          </Typography>
          <Button onClick={() => setDetail(null)}>{t('Close')}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Analytics tab — a date range over the retention KPIs, split into the
// lifetime "Player base" and the "In range" activity (incl. pings + cost),
// plus the daily activity chart, the entry funnel and the stage distribution.
// ---------------------------------------------------------------------------
const isoDay = (d) => d.toISOString().slice(0, 10);
const defaultRange = () => ({
  from: isoDay(new Date(Date.now() - 30 * 86400000)),
  to: isoDay(new Date()),
});

const KpiCard = ({ label, value, hint }) => (
  <Grid size={{ xs: 6, sm: 4, md: 3 }}>
    <Card sx={{ height: '100%' }}>
      <CardContent
        sx={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 0.5 }}
      >
        <Typography variant="overline" color="text.secondary" sx={{ lineHeight: 1.4 }}>
          {label}
        </Typography>
        <Typography variant="h5" sx={{ fontWeight: 600 }}>
          {value ?? '—'}
        </Typography>
        {hint && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 'auto' }}>
            {hint}
          </Typography>
        )}
      </CardContent>
    </Card>
  </Grid>
);

const TIMESERIES_SERIES = [
  { key: 'messages', label: t('Messages') },
  { key: 'active_users', label: t('Active players') },
  { key: 'photos', label: t('Photos') },
  { key: 'pings', label: t('Pings') },
];

const FUNNEL_STEPS = [
  ['deeplinks_created', t('Deeplinks minted')],
  ['starts', t('/start redemptions')],
  ['new_users', t('New linked players')],
  ['subscribed', t('Subscribed to channel')],
  ['engaged', t('Engaged (wrote a message)')],
  ['photo_receivers', t('Received a photo')],
  ['handoffs', t('Handed off')],
];

const AnalyticsTab = ({ productId }) => {
  const notify = useNotify();
  const [range, setRange] = useState(defaultRange);
  const [overview, setOverview] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [series, setSeries] = useState([]);
  const [users, setUsers] = useState([]);

  useEffect(() => {
    const qs = `product_id=${productId}&from=${range.from}&to=${range.to}`;
    httpClient(`${API_URL}/admin/retention/overview?${qs}`)
      .then(({ json }) => setOverview(json))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
    httpClient(`${API_URL}/admin/retention/funnel?${qs}`)
      .then(({ json }) => setFunnel(json))
      .catch(() => setFunnel(null));
    httpClient(`${API_URL}/admin/retention/timeseries?${qs}`)
      .then(({ json }) => setSeries(json.series || []))
      .catch(() => setSeries([]));
  }, [productId, range, notify]);

  useEffect(() => {
    httpClient(`${API_URL}/admin/retention/users?product_id=${productId}`)
      .then(({ json }) => setUsers(json.items || []))
      .catch(() => {});
  }, [productId]);

  const base = overview?.users;
  const inRange = overview?.range;
  const replyRate =
    inRange?.ping_reply_rate != null
      ? t('{pct}% reply rate').replace('{pct}', (inRange.ping_reply_rate * 100).toFixed(1))
      : t('no pings in range');

  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
        <TextField
          size="small"
          type="date"
          label={t('From')}
          value={range.from}
          onChange={(e) => e.target.value && setRange({ ...range, from: e.target.value })}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <TextField
          size="small"
          type="date"
          label={t('To')}
          value={range.to}
          onChange={(e) => e.target.value && setRange({ ...range, to: e.target.value })}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <Typography variant="caption" color="text.secondary">
          {t('Both days inclusive. “Player base” below is lifetime; everything else counts this range.')}
        </Typography>
      </Stack>

      <Typography variant="h6" sx={{ mb: 1 }}>
        {t('Player base')}
      </Typography>
      <Grid container spacing={2} alignItems="stretch" sx={{ mb: 2 }}>
        <KpiCard label={t('Linked players')} value={base?.total} hint={t('lifetime deeplink entries')} />
        <KpiCard label={t('Subscribed')} value={base?.subscribed} hint={t('passed the channel gate')} />
        <KpiCard label={t('Pings muted')} value={base?.pings_muted} hint={t('opted out via /stop')} />
        <KpiCard label={t('Unreachable')} value={base?.unreachable} hint={t('blocked the bot / sends fail')} />
      </Grid>

      <Typography variant="h6" sx={{ mb: 1 }}>
        {t('In range')}
      </Typography>
      <Grid container spacing={2} alignItems="stretch" sx={{ mb: 2 }}>
        <KpiCard label={t('Active players')} value={inRange?.active_users} hint={t('wrote in the range')} />
        <KpiCard label={t('New players')} value={inRange?.new_users} hint={t('first deeplink entry')} />
        <KpiCard label={t('Player messages')} value={inRange?.user_messages} />
        <KpiCard label={t('Photos sent')} value={inRange?.photos_sent} />
        <KpiCard
          label={t('Pings sent')}
          value={inRange?.pings_sent}
          hint={inRange?.pings_failed ? `${inRange.pings_failed} ${t('failed')}` : t('proactive nudges')}
        />
        <KpiCard label={t('Ping replies')} value={inRange?.ping_replies} hint={replyRate} />
        <KpiCard label={t('Hand-offs')} value={inRange?.handoffs} hint={t('to manager / site support')} />
        <KpiCard
          label={t('Cost (USD)')}
          value={inRange?.cost_usd != null ? `$${Number(inRange.cost_usd).toFixed(4)}` : undefined}
          hint={t('TG dialog + photo metadata')}
        />
      </Grid>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
            {t('Daily activity')}
          </Typography>
          <SeriesLineChart data={series} series={TIMESERIES_SERIES} />
        </CardContent>
      </Card>

      <Box sx={{ mb: 2 }}>
        <TelegramCostCharts data={series} height={220} />
      </Box>

      <Grid container spacing={2} alignItems="stretch" sx={{ mb: 2 }}>
        <Grid size={{ xs: 12, md: 7 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                {t('Entry funnel')}
              </Typography>
              <FunnelBars
                steps={FUNNEL_STEPS.map(([key, label]) => ({
                  label,
                  value: funnel ? funnel[key] ?? 0 : null,
                }))}
              />
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, md: 5 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                {t('Stage distribution')}
              </Typography>
              <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
                {t('Players per unlocked photo stage (lifetime).')}
              </Typography>
              <MiniBarChart
                data={overview?.stage_distribution || []}
                xKey="stage"
                yKey="users"
                label={t('Players')}
              />
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Typography variant="h6" sx={{ mb: 1 }}>
        {t('Linked players')} ({users.length})
      </Typography>
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t('Player')}</TableCell>
              <TableCell>{t('TG user')}</TableCell>
              <TableCell>{t('Entry')}</TableCell>
              <TableCell>{t('VIP')}</TableCell>
              <TableCell align="right">{t('Stage')}</TableCell>
              <TableCell align="right">{t('Msgs')}</TableCell>
              <TableCell align="right">{t('Photos')}</TableCell>
              <TableCell>{t('Manager')}</TableCell>
              <TableCell>{t('Last active')}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {users.map((u, i) => (
              <TableRow key={u.id ?? i}>
                <TableCell>{u.player_id}</TableCell>
                <TableCell>
                  {u.tg_username ? `@${u.tg_username}` : u.tg_user_id}
                </TableCell>
                <TableCell>{u.entry_type}</TableCell>
                <TableCell>{u.vip_level || '—'}</TableCell>
                <TableCell align="right">{u.unlocked_stage}</TableCell>
                <TableCell align="right">{u.meaningful_msgs}</TableCell>
                <TableCell align="right">{u.photos_total}</TableCell>
                <TableCell>{u.manager_name || '—'}</TableCell>
                <TableCell>
                  {u.last_active_at
                    ? new Date(u.last_active_at).toLocaleString()
                    : '—'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Idle pings tab — the agent's INACTIVITY ladder ("player quiet N days ->
// Nika writes first"): rules in retention_rules, swept by retention_idle.py
// from the same worker loop as the event agent (shared guards + dry-run).
// ---------------------------------------------------------------------------
const TRIGGER_LABELS = {
  bot_inactivity: 'Quiet in the bot',
  casino_inactivity: 'Not playing on the site',
  no_deposit: 'No deposit',
};

const STATUS_COLORS = { sent: 'success', failed: 'error', skipped: 'default' };

const EMPTY_RULE = {
  name: '',
  enabled: true,
  trigger_kind: 'bot_inactivity',
  inactivity_days: 7,
  action: 'message',
  intent: '',
  vip_tiers: '',
  cooldown_days: 14,
  priority: 0,
};

const IdlePingsTab = ({ productId }) => {
  const isMobile = useIsMobile();
  const notify = useNotify();
  const { permissions } = usePermissions();
  const canWrite = permissions === 'admin';
  const [rules, setRules] = useState([]);
  const [ledger, setLedger] = useState({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState(null); // EMPTY_RULE-shaped, id when editing
  const [running, setRunning] = useState(false);
  const [agent, setAgent] = useState(null); // /v2/status snapshot
  const pageSize = 50;

  const loadRules = useCallback(() => {
    httpClient(`${API_URL}/admin/retention/idle/rules?product_id=${productId}`)
      .then(({ json }) => setRules(json.items || []))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify]);

  const loadLedger = useCallback(() => {
    httpClient(
      `${API_URL}/admin/retention/idle/ledger?product_id=${productId}&page=${page}&page_size=${pageSize}`
    )
      .then(({ json }) => setLedger(json))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, page, notify]);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  useEffect(() => {
    loadLedger();
  }, [loadLedger]);

  useEffect(() => {
    httpClient(`${API_URL}/admin/retention/v2/status?product_id=${productId}`)
      .then(({ json }) => setAgent(json))
      .catch(() => {});
  }, [productId]);

  const openEditor = (rule) =>
    setEditing(
      rule
        ? { ...rule, vip_tiers: (rule.vip_tiers || []).join(', ') }
        : { ...EMPTY_RULE }
    );

  const saveRule = async () => {
    const body = {
      name: editing.name,
      enabled: Boolean(editing.enabled),
      trigger_kind: editing.trigger_kind,
      inactivity_days: Number(editing.inactivity_days) || 1,
      action: editing.action,
      intent: editing.intent,
      vip_tiers: editing.vip_tiers
        .split(',')
        .map((s) => s.trim().toLowerCase())
        .filter(Boolean),
      cooldown_days: Number(editing.cooldown_days) || 0,
      priority: Number(editing.priority) || 0,
    };
    try {
      await httpClient(
        editing.id
          ? `${API_URL}/admin/retention/idle/rules/${editing.id}?product_id=${productId}`
          : `${API_URL}/admin/retention/idle/rules?product_id=${productId}`,
        { method: editing.id ? 'PUT' : 'POST', body: JSON.stringify(body) }
      );
      notify(editing.id ? t('Rule saved') : t('Rule created'), { type: 'success' });
      setEditing(null);
      loadRules();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    }
  };

  const patchRule = async (id, fields) => {
    try {
      await httpClient(
        `${API_URL}/admin/retention/idle/rules/${id}?product_id=${productId}`,
        { method: 'PUT', body: JSON.stringify(fields) }
      );
      loadRules();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    }
  };

  const removeRule = async (id) => {
    if (!window.confirm(t('Delete this ping rule? The ledger history stays.'))) return;
    try {
      await httpClient(
        `${API_URL}/admin/retention/idle/rules/${id}?product_id=${productId}`,
        { method: 'DELETE' }
      );
      loadRules();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Delete failed'), { type: 'error' });
    }
  };

  const runNow = async () => {
    setRunning(true);
    try {
      const { json } = await httpClient(
        `${API_URL}/admin/retention/idle/run?product_id=${productId}`,
        { method: 'POST' }
      );
      const s = json.stats || {};
      if (s.skipped) {
        notify(`${t('Sweep skipped:')} ${s.skipped}`, { type: 'warning' });
      } else {
        notify(
          `${t('Sweep done')} — ${t('considered')} ${s.considered ?? 0}, ${t('sent')} ${s.sent ?? 0}, ${t('failed')} ${s.failed ?? 0}`,
          { type: 'success' }
        );
      }
      loadLedger();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Run failed'), { type: 'error' });
    } finally {
      setRunning(false);
    }
  };

  const pages = Math.max(1, Math.ceil((ledger.total || 0) / pageSize));

  return (
    <Box>
      {agent && !agent.v2_enabled && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {t(
            'The proactive agent is DISABLED for this product, so idle pings do not fire either (they are part of the agent). Enable it in Settings → Retention bot → «Agent enabled».'
          )}
        </Alert>
      )}
      {agent && agent.v2_enabled && agent.v2_dry_run && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {t(
            'Dry-run (shadow mode) is ON: matched idle rules are logged to the agent’s Decisions ledger but nothing is sent. Turn it off in Settings → Retention bot when ready.'
          )}
        </Alert>
      )}
      <Alert severity="info" sx={{ mb: 2 }}>
        {rich(
          t(
            'Idle pings re-engage QUIET players — the inactivity side of the proactive agent (casino events are handled by the [Proactive agent](#/retention-agent) page). Each rule picks WHO (a trigger + inactivity window, optionally narrowed to VIP tiers) and WHAT (a message or a photo, with an English intent hint that grounds what Nika writes). Per-player caps, the minimum gap, quiet hours and the daily AI budget from Settings → Retention bot apply to every send; players opt out with `/stop`.'
          )
        )}
      </Alert>

      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        justifyContent="space-between"
        flexWrap="wrap"
        useFlexGap
        sx={{ mb: 1 }}
      >
        <Typography variant="h6">{t('Rules')}</Typography>
        {canWrite && (
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" size="small" onClick={() => openEditor(null)}>
              {t('Add rule')}
            </Button>
            <Button
              variant="contained"
              size="small"
              onClick={runNow}
              disabled={running}
            >
              {running ? t('Running…') : t('Run now')}
            </Button>
          </Stack>
        )}
      </Stack>
      {canWrite && (
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
          {t(
            'Run now sweeps this product once, ignoring quiet hours (you are explicitly asking); every other guard — caps, gaps, cooldowns, opt-outs, dry-run — still applies.'
          )}
        </Typography>
      )}
      <Box sx={{ overflowX: 'auto', mb: 3 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t('On')}</TableCell>
              <TableCell>{t('Name')}</TableCell>
              <TableCell>{t('Trigger')}</TableCell>
              <TableCell align="right">{t('Days')}</TableCell>
              <TableCell>{t('Action')}</TableCell>
              <TableCell>{t('VIP tiers')}</TableCell>
              <TableCell align="right">{t('Cooldown')}</TableCell>
              <TableCell align="right">{t('Priority')}</TableCell>
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {rules.map((r) => (
              <TableRow key={r.id} hover>
                <TableCell>
                  <Switch
                    size="small"
                    checked={Boolean(r.enabled)}
                    disabled={!canWrite}
                    onChange={(e) => patchRule(r.id, { enabled: e.target.checked })}
                  />
                </TableCell>
                <TableCell>{r.name}</TableCell>
                <TableCell>{t(TRIGGER_LABELS[r.trigger_kind]) || r.trigger_kind}</TableCell>
                <TableCell align="right">{r.inactivity_days}</TableCell>
                <TableCell>{r.action}</TableCell>
                <TableCell>
                  {(r.vip_tiers || []).length ? r.vip_tiers.join(', ') : t('all')}
                </TableCell>
                <TableCell align="right">{r.cooldown_days}d</TableCell>
                <TableCell align="right">{r.priority}</TableCell>
                <TableCell>
                  {canWrite && (
                    <>
                      <Button size="small" onClick={() => openEditor(r)}>
                        {t('Edit')}
                      </Button>
                      <Button size="small" color="error" onClick={() => removeRule(r.id)}>
                        {t('Delete')}
                      </Button>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {rules.length === 0 && (
              <TableRow>
                <TableCell colSpan={9}>
                  <Typography color="text.secondary" sx={{ py: 2 }}>
                    {t('No rules yet — quiet players are not re-engaged until a rule exists.')}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Box>

      <Typography variant="h6" sx={{ mb: 1 }}>
        {t('Ledger')}
      </Typography>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
        {t(
          'Every proactive-send attempt (idle rules AND event reactions): who was nudged, by which rule, and what it cost. Skipped rows explain why a candidate was passed over.'
        )}
      </Typography>
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t('When')}</TableCell>
              <TableCell>{t('Player')}</TableCell>
              <TableCell>{t('Rule')}</TableCell>
              <TableCell>{t('Action')}</TableCell>
              <TableCell>{t('Status')}</TableCell>
              <TableCell>{t('Detail')}</TableCell>
              <TableCell align="right">{t('Cost $')}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {ledger.items.map((p) => (
              <TableRow key={p.id} hover>
                <TableCell>{new Date(p.created_at).toLocaleString()}</TableCell>
                <TableCell>
                  {p.full_name ||
                    (p.tg_username ? `@${p.tg_username}` : p.player_id) ||
                    '—'}
                </TableCell>
                <TableCell>{p.rule_name || '—'}</TableCell>
                <TableCell>{p.action}</TableCell>
                <TableCell>
                  <Chip
                    size="small"
                    label={p.status}
                    color={STATUS_COLORS[p.status] || 'default'}
                    variant="outlined"
                  />
                </TableCell>
                <TableCell>{p.detail || ''}</TableCell>
                <TableCell align="right">
                  {p.cost_usd ? Number(p.cost_usd).toFixed(5) : '—'}
                </TableCell>
              </TableRow>
            ))}
            {ledger.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={7}>
                  <Typography color="text.secondary" sx={{ py: 2 }}>
                    {t('No proactive sends yet.')}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Box>
      {pages > 1 && (
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
          <Button size="small" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            {t('Prev')}
          </Button>
          <Typography variant="body2">
            {page} / {pages} · {ledger.total}
          </Typography>
          <Button size="small" disabled={page >= pages} onClick={() => setPage(page + 1)}>
            {t('Next')}
          </Button>
        </Stack>
      )}

      <Dialog open={!!editing} onClose={() => setEditing(null)} maxWidth="sm" fullWidth fullScreen={isMobile}>
        <DialogTitle>{editing?.id ? t('Edit rule') : t('New rule')}</DialogTitle>
        <DialogContent dividers>
          {editing && (
            <Stack spacing={2} sx={{ mt: 0.5 }}>
              <TextField
                size="small"
                label={t('Name')}
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                helperText={t('Shown in the rules table and the ledger.')}
                fullWidth
              />
              <TextField
                select
                size="small"
                label={t('Trigger')}
                value={editing.trigger_kind}
                onChange={(e) => setEditing({ ...editing, trigger_kind: e.target.value })}
                helperText={t(
                  "Casino triggers need the partner's Player API / event feed to see logins and deposits."
                )}
                fullWidth
              >
                {Object.entries(TRIGGER_LABELS).map(([value, label]) => (
                  <MenuItem key={value} value={value}>
                    {t(label)}
                  </MenuItem>
                ))}
              </TextField>
              <TextField
                size="small"
                type="number"
                label={t('Inactivity days')}
                value={editing.inactivity_days}
                onChange={(e) => setEditing({ ...editing, inactivity_days: e.target.value })}
                helperText={t('How many quiet days before the rule fires.')}
                fullWidth
                slotProps={{ htmlInput: { min: 1, max: 365 } }}
              />
              <TextField
                select
                size="small"
                label={t('Action')}
                value={editing.action}
                onChange={(e) => setEditing({ ...editing, action: e.target.value })}
                helperText={t(
                  "Photo pings pick from the player's unlocked media (tier × stage gates apply)."
                )}
                fullWidth
              >
                <MenuItem value="message">{t('message')}</MenuItem>
                <MenuItem value="photo">{t('photo')}</MenuItem>
              </TextField>
              <TextField
                size="small"
                label={t('Intent (English hint for the AI)')}
                value={editing.intent}
                onChange={(e) => setEditing({ ...editing, intent: e.target.value })}
                helperText={t(
                  'What the ping should achieve, e.g. “miss them warmly, tease what’s new, invite them back — no pressure”. English only (it feeds the model prompt).'
                )}
                fullWidth
                multiline
                minRows={2}
              />
              <TextField
                size="small"
                label={t('VIP tiers (comma-separated, empty = all)')}
                value={editing.vip_tiers}
                onChange={(e) => setEditing({ ...editing, vip_tiers: e.target.value })}
                helperText={t(
                  'Lowercase tier names from Settings → Retention bot → VIP tiers, e.g. gold, platinum.'
                )}
                fullWidth
              />
              <Stack direction="row" spacing={2}>
                <TextField
                  size="small"
                  type="number"
                  label={t('Cooldown days')}
                  value={editing.cooldown_days}
                  onChange={(e) => setEditing({ ...editing, cooldown_days: e.target.value })}
                  helperText={t('Days before the SAME rule may hit the same player again.')}
                  fullWidth
                  slotProps={{ htmlInput: { min: 0, max: 365 } }}
                />
                <TextField
                  size="small"
                  type="number"
                  label={t('Priority')}
                  value={editing.priority}
                  onChange={(e) => setEditing({ ...editing, priority: e.target.value })}
                  helperText={t('Higher wins when several rules match one player.')}
                  fullWidth
                  slotProps={{ htmlInput: { min: -1000, max: 1000 } }}
                />
              </Stack>
              <FormControlLabel
                control={
                  <Switch
                    checked={Boolean(editing.enabled)}
                    onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })}
                  />
                }
                label={t('Enabled')}
              />
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>{t('Cancel')}</Button>
          <Button variant="contained" onClick={saveRule} disabled={!editing?.name?.trim()}>
            {t('Save')}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

// ---------------------------------------------------------------------------
// page shell — needs a concrete product (retention is strictly per-product)
// ---------------------------------------------------------------------------
// Navigation between sections is the sidebar's job now (each section is its own
// entry, like Support), so there is no page-wide tab strip. Two sections bundle
// a secondary sub-tab under one sidebar entry (mirrors the Support "Prompt"
// page): the Setup guide under Telegram config, and the Prompt variables under
// Prompt. Those groups render a small internal 2-tab strip; every other section
// renders its component directly.
const COMPONENTS = {
  config: ConfigTab,
  guide: GuideTab,
  kb: KbTab,
  prompt: PromptTab,
  variables: VariablesTab,
  photos: PhotosTab,
  managers: ManagersTab,
  idle: IdlePingsTab,
  chats: ConversationsTab,
  analytics: AnalyticsTab,
};

const SUBTABS = {
  config: [
    ['config', t('Telegram config')],
    ['guide', t('Setup guide')],
  ],
  prompt: [
    ['prompt', t('Prompt preview')],
    ['variables', t('Prompt variables')],
  ],
};

// The group (and its sub-tab strip) a tab belongs to, if any; the group's first
// entry is the sidebar's landing tab.
const groupFor = (tab) =>
  Object.values(SUBTABS).find((entries) => entries.some(([v]) => v === tab));

const Retention = () => {
  const [params, setParams] = useSearchParams();
  const productId = getProductId();
  const requested = params.get('tab');
  const tab = COMPONENTS[requested] ? requested : 'config';

  // Retention data is strictly per-product; refuse to render without one so the
  // operator can't edit the default product by accident (same gate as KB /
  // Prompt / Translations).
  if (!productId) {
    return <RequireProduct title={t('Retention · Telegram')} />;
  }

  const Component = COMPONENTS[tab];
  const subtabs = groupFor(tab);

  return (
    <Box sx={{ p: 2, maxWidth: 1100 }}>
      <Title title={t('Retention · Telegram')} />
      {subtabs && (
        <Tabs
          value={tab}
          onChange={(e, v) => setParams({ tab: v }, { replace: true })}
          variant="scrollable"
          allowScrollButtonsMobile
          sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}
        >
          {subtabs.map(([value, label]) => (
            <Tab key={value} value={value} label={label} />
          ))}
        </Tabs>
      )}
      <Component productId={productId} />
    </Box>
  );
};

export default Retention;
