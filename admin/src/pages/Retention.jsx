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
import { API_URL, httpClient, getToken } from '../httpClient';
import { getProductId } from '../productScope';
import {
  FunnelBars,
  MiniBarChart,
  SeriesLineChart,
  TelegramCostCharts,
} from '../components/charts';
import RequireProduct from '../components/RequireProduct';
import SecretField from '../components/SecretField';

// ---------------------------------------------------------------------------
// Setup guide tab — the short "how to connect the bot" checklist that replaced
// the repo's RETENTION_SETUP.md. Everything product-level is configured right
// here in this section; only deploy env vars live outside.
// ---------------------------------------------------------------------------
const GUIDE_STEPS = [
  {
    title: '1 · Create the bot',
    body: (
      <>
        Open{' '}
        <Link href="https://t.me/BotFather" target="_blank" rel="noopener">
          @BotFather
        </Link>{' '}
        → <code>/newbot</code>, pick a name and a username, copy the <b>token</b>.
        Optionally set the description, about text and avatar there too. Menu
        commands are not needed — players enter only via a deeplink from the site.
      </>
    ),
  },
  {
    title: '2 · Create the channel (subscription gate)',
    body: (
      <>
        Create a Telegram <b>channel</b> and add the bot as a <b>channel
        administrator</b> — without admin rights the subscription check
        (<code>getChatMember</code>) fails and the gate never passes. Note the
        channel id (<code>@name</code> for public, <code>-100…</code> for private)
        and the channel URL (the gate&apos;s &quot;open channel&quot; button leads there).
      </>
    ),
  },
  {
    title: '3 · Deploy env (Railway)',
    body: (
      <>
        Set on the service (not per product): <code>PUBLIC_BASE_URL</code> (public
        address, used to build the webhook URL), <code>TELEGRAM_WEBHOOK_SECRET</code>{' '}
        (random string, verified in the webhook header),{' '}
        <code>RETENTION_MEDIA_DIR</code> (mount path of an attached <b>Volume</b>,
        so photos survive redeploys) and <code>SECRETS_MASTER_KEY</code> (encrypts
        product secrets). The full env table is in the repo&apos;s README.
      </>
    ),
  },
  {
    title: '4 · Connect this product',
    body: (
      <>
        On the <Link href="#/retention?tab=config">Telegram config</Link> tab:
        switch on <b>Retention bot enabled</b>, fill the bot username, channel id
        and channel URL → <b>Save config</b>. In <b>Secrets</b> paste the bot token
        (and the Player API key, if the casino exposes a profile endpoint) →{' '}
        <b>Save secrets</b>. Then press <b>Register Telegram webhook</b> — it must
        report the webhook URL back.
      </>
    ),
  },
  {
    title: '5 · Content and tuning',
    body: (
      <>
        Review the <Link href="#/retention?tab=kb">Retention KB</Link> (one text
        document — what Nika may offer and talk about; a generic English starter
        is pre-filled, replace it with the brand&apos;s own), tune the Telegram
        persona in <Link href="#/retention?tab=variables">Prompt variables</Link>{' '}
        (name/role/tone — empty fields inherit the support chat), upload photos
        in <Link href="#/retention?tab=photos">Media</Link> (bulk upload, then
        select them and press <b>Generate metadata</b> to have the AI fill the
        description, tags, <code>stage</code> = explicitness and{' '}
        <code>level_min</code> = VIP tier) and add live{' '}
        <Link href="#/retention?tab=managers">Managers</Link> (round-robin,
        sticky). Thresholds (daily photo cap, stage progression, VIP tiers,
        nonce TTL) are the <code>retention</code> group in{' '}
        <Link href="#/settings">Settings</Link>; bot texts are the{' '}
        <code>rtn_*</code> keys in <Link href="#/translations">Translations</Link>.
      </>
    ),
  },
  {
    title: '6 · Entry points',
    body: (
      <>
        Nothing extra to integrate for the main path: once the bot is enabled, the
        support widget&apos;s <b>escalation button</b> automatically routes the player
        into the bot (one-time deeplink, subscription gate on the way in, &quot;go to
        a manager&quot; in the menu). Optionally the site can mint its own per-player
        deeplink via <code>POST /api/retention/deeplink</code> — the full contract
        (handshake signing, profile pull/push) is documented at{' '}
        <Link href="/integration-telegram" target="_blank" rel="noopener">
          /integration-telegram
        </Link>
        .
      </>
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
      Quick test: open the deeplink → pass the channel gate → chat with Nika → ask
      for a photo → it arrives; write &quot;my account is blocked&quot; → she routes you
      out instead of answering support questions herself.
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
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
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
      notify('Telegram config saved', { type: 'success' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
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
      notify('Secrets saved', { type: 'success' });
      setSecrets({});
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    }
  };

  const clearSecret = async (field, label) => {
    if (!window.confirm(`Clear ${label}? It falls back to the deploy env value.`)) return;
    try {
      await httpClient(`${API_URL}/admin/products/${productId}/secrets`, {
        method: 'PUT',
        body: JSON.stringify({ [field]: '' }),
      });
      notify(`${label} cleared`, { type: 'success' });
      setSecrets({ ...secrets, [field]: '' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Clear failed', { type: 'error' });
    }
  };

  const registerWebhook = async () => {
    try {
      const { json } = await httpClient(
        `${API_URL}/admin/retention/webhook/${productId}`,
        { method: 'POST' }
      );
      notify(`Webhook registered: ${json.webhook_url || 'ok'}`, { type: 'success' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Webhook registration failed', {
        type: 'error',
      });
    }
  };

  if (!data) return <Box sx={{ p: 2 }}>Loading…</Box>;
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
          label="Retention bot enabled"
        />
        {[
          ['telegram_bot_username', 'Bot username (without @)'],
          ['telegram_channel_id', 'Channel id (@channel or -100…)'],
          ['telegram_channel_url', 'Channel URL (subscription gate)'],
          ['player_api_url', 'Player API URL (profile pull)'],
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
          Save config
        </Button>
        <Button variant="outlined" onClick={registerWebhook} sx={{ mt: 1 }}>
          Register Telegram webhook
        </Button>
        {data.webhook_url && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Webhook URL: <code>{data.webhook_url}</code>
          </Typography>
        )}
        <Typography variant="h6" sx={{ mt: 2 }}>
          Secrets
        </Typography>
        <SecretField
          label="Telegram bot token"
          set={Boolean(p.has_telegram_bot_token)}
          value={secrets.telegram_bot_token}
          onChange={(e) => setSecrets({ ...secrets, telegram_bot_token: e.target.value })}
          onClear={() => clearSecret('telegram_bot_token', 'Telegram bot token')}
        />
        <SecretField
          label="Player API key"
          set={Boolean(p.has_player_api_key)}
          value={secrets.player_api_key}
          onChange={(e) => setSecrets({ ...secrets, player_api_key: e.target.value })}
          onClear={() => clearSecret('player_api_key', 'Player API key')}
        />
        <Button variant="contained" size="small" onClick={saveSecrets} sx={{ mt: 1 }}>
          Save secrets
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
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [productId, notify]);

  const save = async () => {
    setSaving(true);
    try {
      const { json } = await httpClient(
        `${API_URL}/admin/retention/kb/text?product_id=${productId}`,
        { method: 'PUT', body: JSON.stringify({ text }) }
      );
      setText(json.text ?? '');
      notify('Retention KB saved', { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  if (text === null) return <Box sx={{ p: 2 }}>Loading…</Box>;

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          The whole retention knowledge base as one text (Layer 2 of the
          retention prompt — what Nika may offer and talk about in Telegram).
          Keep it in English: it is the most token-efficient language for the
          model, and Nika answers in the player&apos;s language regardless.{' '}
          <code>{'{placeholders}'}</code> are substituted from KB variables.
        </Typography>
        <TextField
          value={text}
          onChange={(e) => setText(e.target.value)}
          multiline
          minRows={20}
          fullWidth
        />
        <Button variant="contained" onClick={save} disabled={saving} sx={{ mt: 1.5 }}>
          Save
        </Button>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Prompt variables tab — the Telegram-persona values (name, role, brand,
// products, tone of voice). Every field except the tone INHERITS the support
// chat's value when left empty, so by default the bot mirrors the support
// persona and the operator overrides only what should differ (e.g. a bolder,
// more intimate Telegram girl with her own name).
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
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [productId, notify, apply]);

  const save = async () => {
    setSaving(true);
    try {
      const { json } = await httpClient(
        `${API_URL}/admin/retention/prompt-variables?product_id=${productId}`,
        { method: 'PUT', body: JSON.stringify({ value: values }) }
      );
      apply(json.variables);
      notify('Retention prompt variables saved', { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  if (vars === null) return <Box sx={{ p: 2 }}>Loading…</Box>;

  return (
    <Box sx={{ maxWidth: 900 }}>
      <Alert severity="info" sx={{ mb: 2 }}>
        These values uniquify the <b>Telegram retention persona</b> — a
        <b> separate prompt</b>, fully independent from the{' '}
        <Link href="#/prompt?tab=variables">support-chat prompt variables</Link>.
        An empty field <b>uses the built-in retention default</b> (shown as the
        placeholder); a support edit never leaks into the bot. Fill a field only
        where you want the Telegram persona to differ from that default.
      </Alert>
      <Card>
        <CardContent>
          {vars.map((v) => (
            <TextField
              key={v.key}
              label={v.key}
              helperText={v.description + ' Empty = the built-in retention default.'}
              value={values[v.key] ?? ''}
              onChange={(e) => setValues({ ...values, [v.key]: e.target.value })}
              placeholder={v.default}
              fullWidth
              multiline
              margin="normal"
            />
          ))}
          <Button variant="contained" onClick={save} disabled={saving} sx={{ mt: 1 }}>
            {saving ? 'Saving…' : 'Save variables'}
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
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [productId, notify]);

  return (
    <Box sx={{ maxWidth: 1000 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        The complete retention prompt as the model receives it in the Telegram
        chat (read-only; language: {preview?.example?.lang || '—'}). To change
        the wording, edit <code>prompts.py</code> and redeploy; the brand values
        are on the{' '}
        <Link href="#/retention?tab=variables">Prompt variables</Link> tab.
      </Typography>
      <PreviewBlock
        title="System message (retention Layer 1 core + Layer 2 retention KB)"
        text={preview?.system}
      />
      <PreviewBlock
        title="User message (Layer 3: profile, language, photo candidates, guardrails)"
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

// How many photos ride in one generate-metadata request; larger selections are
// chunked client-side so a slow vision batch can't hit the request timeout.
const META_CHUNK = 10;

// Photos shown per page. The whole library is loaded once (filtering is
// client-side); paginating the grid keeps only ~20 previews fetching at a
// time instead of every photo at once.
const PHOTOS_PER_PAGE = 20;

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
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
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
        notify(body.detail || 'Upload failed', { type: 'error' });
        return;
      }
      const body = await res.json().catch(() => ({}));
      const uploaded = (body.photos || []).length || 1;
      notify(`${uploaded} photo${uploaded === 1 ? '' : 's'} uploaded`, {
        type: 'success',
      });
      setFiles([]);
      load();
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
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    }
  };

  const remove = async (id) => {
    if (!window.confirm('Delete this photo?')) return;
    try {
      await httpClient(`${API_URL}/admin/retention/photos/${id}`, { method: 'DELETE' });
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Delete failed', { type: 'error' });
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
        `Generate metadata for ${ids.length} photo(s)? The AI fills the description, tags, stage and VIP level; current values are overwritten.`
      )
    ) {
      return;
    }
    setGenerating(true);
    let ok = 0;
    let failed = 0;
    const errors = [];
    try {
      for (let i = 0; i < ids.length; i += META_CHUNK) {
        const chunk = ids.slice(i, i + META_CHUNK);
        setGenProgress(`${Math.min(i + chunk.length, ids.length)} / ${ids.length}`);
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
      }
      if (failed) {
        notify(
          `Metadata: ${ok} generated, ${failed} failed (${errors.slice(0, 3).join('; ')}${errors.length > 3 ? '…' : ''})`,
          { type: 'warning' }
        );
      } else {
        notify(`Metadata generated for ${ok} photo${ok === 1 ? '' : 's'}`, {
          type: 'success',
        });
      }
      setSelected(new Set());
    } catch (e) {
      notify(e.body?.detail || e.message || 'Generation failed', { type: 'error' });
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
            Upload photos
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Pick any number of files at once. The fields below apply to every
            uploaded photo — leave them empty and use{' '}
            <b>Generate metadata</b> afterwards to have the AI fill the
            description, tags, explicitness stage and VIP level per photo.
          </Typography>
          <Grid container spacing={1.5} sx={{ mb: 1 }}>
            <Grid size={{ xs: 12 }}>
              <TextField
                size="small"
                label="Description (grounds the caption the model writes)"
                value={upload.description}
                onChange={(e) => setUpload({ ...upload, description: e.target.value })}
                fullWidth
                multiline
              />
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 4 }}>
              <TextField
                size="small"
                label="Tags (comma-separated)"
                value={upload.tags}
                onChange={(e) => setUpload({ ...upload, tags: e.target.value })}
                fullWidth
              />
            </Grid>
            <Grid size={{ xs: 6, sm: 3, md: 2 }}>
              <TextField
                select
                size="small"
                label="Level (min VIP tier)"
                value={upload.level_min}
                onChange={(e) => setUpload({ ...upload, level_min: Number(e.target.value) })}
                helperText="VIP tier to unlock"
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
                label="Stage (explicitness)"
                value={upload.stage}
                onChange={(e) => setUpload({ ...upload, stage: Number(e.target.value) })}
                helperText="1 = softest"
                fullWidth
              >
                {stageChoices.map((s) => (
                  <MenuItem key={s} value={s}>{`Stage ${s}`}</MenuItem>
                ))}
              </TextField>
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 4 }}>
              <TextField
                size="small"
                label="Category"
                value={upload.category}
                onChange={(e) => setUpload({ ...upload, category: e.target.value })}
                fullWidth
              />
            </Grid>
          </Grid>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Button variant="outlined" component="label">
              {files.length
                ? `${files.length} file${files.length === 1 ? '' : 's'} chosen`
                : 'Choose files'}
              <input
                hidden
                type="file"
                accept="image/*"
                multiple
                onChange={(e) => setFiles([...e.target.files])}
              />
            </Button>
            <Button variant="contained" onClick={doUpload} disabled={!files.length || uploading}>
              {uploading ? 'Uploading…' : 'Upload'}
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
              label="Search (description, tags, category)"
              value={filters.q}
              onChange={(e) => setFilter({ q: e.target.value })}
              sx={{ minWidth: 240 }}
            />
            <TextField
              select
              size="small"
              label="Stage"
              value={filters.stage}
              onChange={(e) => setFilter({ stage: e.target.value })}
              sx={{ minWidth: 110 }}
            >
              <MenuItem value="all">all</MenuItem>
              {stageOptions.map((s) => (
                <MenuItem key={s} value={String(s)}>
                  {s}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Level min"
              value={filters.level}
              onChange={(e) => setFilter({ level: e.target.value })}
              sx={{ minWidth: 110 }}
            >
              <MenuItem value="all">all</MenuItem>
              {levelOptions.map((l) => (
                <MenuItem key={l} value={String(l)}>
                  {l}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Status"
              value={filters.status}
              onChange={(e) => setFilter({ status: e.target.value })}
              sx={{ minWidth: 110 }}
            >
              <MenuItem value="all">all</MenuItem>
              <MenuItem value="active">active</MenuItem>
              <MenuItem value="inactive">inactive</MenuItem>
            </TextField>
            <Typography variant="body2" color="text.secondary">
              {visible.length} of {items.length} photos
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
              Select all shown
            </Button>
            <Button
              size="small"
              onClick={() => setSelected(new Set())}
              disabled={!selected.size}
            >
              Clear selection
            </Button>
            <Button
              variant="contained"
              size="small"
              onClick={generate}
              disabled={!selected.size || generating}
            >
              {generating
                ? `Generating… ${genProgress}`
                : `Generate metadata (${selected.size})`}
            </Button>
            <Typography variant="caption" color="text.secondary">
              AI (the product&apos;s own model + API key) fills the description,
              tags, stage and minimum VIP level for every selected photo.
            </Typography>
          </Stack>
        </CardContent>
      </Card>

      {items.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          No photos yet — upload the first ones above.
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
                  <Chip size="small" variant="outlined" label={`stage ${ph.stage}`} />
                  <Chip size="small" variant="outlined" label={`level ${ph.level_min}+`} />
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
                    label="Description"
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
                    label="Tags (comma-separated)"
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
                      label="Level (min VIP)"
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
                      label="Stage"
                      value={ph.stage}
                      onChange={(e) =>
                        Number(e.target.value) !== ph.stage &&
                        patch(ph.id, { stage: Number(e.target.value) })
                      }
                      fullWidth
                    >
                      {stageChoices.map((s) => (
                        <MenuItem key={s} value={s}>{`Stage ${s}`}</MenuItem>
                      ))}
                      {!stageChoices.includes(ph.stage) && (
                        <MenuItem value={ph.stage}>{`Stage ${ph.stage}`}</MenuItem>
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
                      label="Active"
                    />
                    {ph.telegram_file_id && <Chip size="small" label="cached in TG" />}
                    <Button size="small" color="error" onClick={() => remove(ph.id)}>
                      Delete
                    </Button>
                  </Stack>
                </Stack>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>
      {pageCount > 1 && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          justifyContent="center"
          sx={{ mt: 2 }}
        >
          <Button
            size="small"
            disabled={safePage <= 1}
            onClick={() => setPage(safePage - 1)}
          >
            Prev
          </Button>
          <Typography variant="body2">
            {safePage} / {pageCount} · {visible.length} photos
          </Typography>
          <Button
            size="small"
            disabled={safePage >= pageCount}
            onClick={() => setPage(safePage + 1)}
          >
            Next
          </Button>
        </Stack>
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
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
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
      notify(e.body?.detail || e.message || 'Create failed', { type: 'error' });
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
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    }
  };

  const remove = async (id) => {
    if (!window.confirm('Delete this manager?')) return;
    try {
      await httpClient(`${API_URL}/admin/retention/managers/${id}`, { method: 'DELETE' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Delete failed', { type: 'error' });
    }
  };

  return (
    <Box>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
        <TextField size="small" label="Display name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        <TextField size="small" label="Telegram username (without @)" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <Button variant="outlined" onClick={create} disabled={!form.display_name || !form.username}>
          Add manager
        </Button>
      </Stack>
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Username</TableCell>
              <TableCell>Active</TableCell>
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
                    Delete
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
// Pings tab — the proactive-ping rule matrix + the send ledger. Rules say WHO
// gets nudged and WHY (trigger + inactivity window + intent for the AI); the
// pacing (master switch, caps, gaps, quiet hours, batch size) lives in
// Settings → Retention bot so it is tuned in one place.
// ---------------------------------------------------------------------------
const TRIGGER_LABELS = {
  bot_inactivity: 'No messages in the bot',
  casino_inactivity: 'No casino login/play (needs partner data feed)',
  no_deposit: 'No deposit (needs partner data feed)',
};

// Dialog form state; vip_tiers is edited as a comma string, sent as a list.
const EMPTY_RULE = {
  name: '',
  enabled: true,
  trigger_kind: 'bot_inactivity',
  inactivity_days: 7,
  action: 'message',
  intent: '',
  vip_tiers: '',
  cooldown_days: 7,
  priority: 0,
};

const STATUS_COLORS = { sent: 'success', failed: 'error', skipped: 'default' };

const PingsTab = ({ productId }) => {
  const notify = useNotify();
  const { permissions } = usePermissions();
  const canWrite = permissions === 'admin';
  const [rules, setRules] = useState([]);
  const [ledger, setLedger] = useState({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState(null); // EMPTY_RULE-shaped, id when editing
  const [running, setRunning] = useState(false);
  const pageSize = 50;

  const loadRules = useCallback(() => {
    httpClient(`${API_URL}/admin/retention/pings/rules?product_id=${productId}`)
      .then(({ json }) => setRules(json.items || []))
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [productId, notify]);

  const loadLedger = useCallback(() => {
    httpClient(
      `${API_URL}/admin/retention/pings?product_id=${productId}&page=${page}&page_size=${pageSize}`
    )
      .then(({ json }) => setLedger(json))
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [productId, page, notify]);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  useEffect(() => {
    loadLedger();
  }, [loadLedger]);

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
          ? `${API_URL}/admin/retention/pings/rules/${editing.id}?product_id=${productId}`
          : `${API_URL}/admin/retention/pings/rules?product_id=${productId}`,
        { method: editing.id ? 'PUT' : 'POST', body: JSON.stringify(body) }
      );
      notify(editing.id ? 'Rule saved' : 'Rule created', { type: 'success' });
      setEditing(null);
      loadRules();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    }
  };

  const patchRule = async (id, fields) => {
    try {
      await httpClient(
        `${API_URL}/admin/retention/pings/rules/${id}?product_id=${productId}`,
        { method: 'PUT', body: JSON.stringify(fields) }
      );
      loadRules();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    }
  };

  const removeRule = async (id) => {
    if (!window.confirm('Delete this ping rule? The ledger history stays.')) return;
    try {
      await httpClient(
        `${API_URL}/admin/retention/pings/rules/${id}?product_id=${productId}`,
        { method: 'DELETE' }
      );
      loadRules();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Delete failed', { type: 'error' });
    }
  };

  const runNow = async () => {
    setRunning(true);
    try {
      const { json } = await httpClient(
        `${API_URL}/admin/retention/pings/run?product_id=${productId}`,
        { method: 'POST' }
      );
      const s = json.stats || {};
      if (s.skipped) {
        notify(`Sweep skipped: ${s.skipped}`, { type: 'warning' });
      } else {
        notify(
          `Sweep done — considered ${s.considered ?? 0}, sent ${s.sent ?? 0}, failed ${s.failed ?? 0}`,
          { type: 'success' }
        );
      }
      loadLedger();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Run failed', { type: 'error' });
    } finally {
      setRunning(false);
    }
  };

  const pages = Math.max(1, Math.ceil((ledger.total || 0) / pageSize));

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        The ping matrix re-engages quiet players: each rule picks WHO (a
        trigger + inactivity window, optionally narrowed to VIP tiers) and WHAT
        (a message or a photo, with an intent hint that grounds what Nika
        writes). The master switch, the per-player daily cap and minimum gap,
        the quiet hours and the batch size live in{' '}
        <Link href="#/settings">Settings → Retention bot</Link>. Players opt
        out any time by sending <code>/stop</code> to the bot (and back in
        with <code>/start</code>).
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
        <Typography variant="h6">Rules</Typography>
        {canWrite && (
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" size="small" onClick={() => openEditor(null)}>
              Add rule
            </Button>
            <Button
              variant="contained"
              size="small"
              onClick={runNow}
              disabled={running}
            >
              {running ? 'Running…' : 'Run now'}
            </Button>
          </Stack>
        )}
      </Stack>
      {canWrite && (
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
          Run now sweeps this product once, ignoring quiet hours (you are
          explicitly asking); every other guard — caps, gaps, cooldowns,
          opt-outs — still applies.
        </Typography>
      )}
      <Box sx={{ overflowX: 'auto', mb: 3 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>On</TableCell>
              <TableCell>Name</TableCell>
              <TableCell>Trigger</TableCell>
              <TableCell align="right">Days</TableCell>
              <TableCell>Action</TableCell>
              <TableCell>VIP tiers</TableCell>
              <TableCell align="right">Cooldown</TableCell>
              <TableCell align="right">Priority</TableCell>
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
                <TableCell>{TRIGGER_LABELS[r.trigger_kind] || r.trigger_kind}</TableCell>
                <TableCell align="right">{r.inactivity_days}</TableCell>
                <TableCell>{r.action}</TableCell>
                <TableCell>
                  {(r.vip_tiers || []).length ? r.vip_tiers.join(', ') : 'all'}
                </TableCell>
                <TableCell align="right">{r.cooldown_days}d</TableCell>
                <TableCell align="right">{r.priority}</TableCell>
                <TableCell>
                  {canWrite && (
                    <>
                      <Button size="small" onClick={() => openEditor(r)}>
                        Edit
                      </Button>
                      <Button size="small" color="error" onClick={() => removeRule(r.id)}>
                        Delete
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
                    No rules yet — nothing is pinged until a rule exists.
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Box>

      <Typography variant="h6" sx={{ mb: 1 }}>
        Ledger
      </Typography>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
        Every ping attempt: who was nudged, by which rule, and what it cost.
        Skipped rows explain why a candidate was passed over.
      </Typography>
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>When</TableCell>
              <TableCell>Player</TableCell>
              <TableCell>Rule</TableCell>
              <TableCell>Action</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Detail</TableCell>
              <TableCell align="right">Cost $</TableCell>
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
                    No pings sent yet.
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
            Prev
          </Button>
          <Typography variant="body2">
            {page} / {pages} · {ledger.total} pings
          </Typography>
          <Button size="small" disabled={page >= pages} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </Stack>
      )}

      <Dialog open={!!editing} onClose={() => setEditing(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing?.id ? 'Edit rule' : 'New rule'}</DialogTitle>
        <DialogContent dividers>
          {editing && (
            <Stack spacing={2} sx={{ mt: 0.5 }}>
              <TextField
                size="small"
                label="Name"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                helperText="Shown in the rules table and the ledger."
                fullWidth
              />
              <TextField
                select
                size="small"
                label="Trigger"
                value={editing.trigger_kind}
                onChange={(e) => setEditing({ ...editing, trigger_kind: e.target.value })}
                helperText="Casino triggers need the partner's Player API / push feed to see logins and deposits."
                fullWidth
              >
                {Object.entries(TRIGGER_LABELS).map(([value, label]) => (
                  <MenuItem key={value} value={value}>
                    {label}
                  </MenuItem>
                ))}
              </TextField>
              <TextField
                size="small"
                type="number"
                label="Inactivity days"
                value={editing.inactivity_days}
                onChange={(e) => setEditing({ ...editing, inactivity_days: e.target.value })}
                helperText="How many quiet days before the rule fires."
                fullWidth
                slotProps={{ htmlInput: { min: 1, max: 365 } }}
              />
              <TextField
                select
                size="small"
                label="Action"
                value={editing.action}
                onChange={(e) => setEditing({ ...editing, action: e.target.value })}
                helperText="Photo pings pick from the player's unlocked media (tier × stage gates apply)."
                fullWidth
              >
                <MenuItem value="message">message</MenuItem>
                <MenuItem value="photo">photo</MenuItem>
              </TextField>
              <TextField
                size="small"
                label="Intent (English hint for the AI)"
                value={editing.intent}
                onChange={(e) => setEditing({ ...editing, intent: e.target.value })}
                helperText="What the ping should achieve, e.g. “miss them warmly, tease what's new, invite them back — no pressure”."
                fullWidth
                multiline
                minRows={2}
              />
              <TextField
                size="small"
                label="VIP tiers (comma-separated, empty = all)"
                value={editing.vip_tiers}
                onChange={(e) => setEditing({ ...editing, vip_tiers: e.target.value })}
                helperText="Lowercase tier names from Settings → Retention bot → VIP tiers, e.g. gold, platinum."
                fullWidth
              />
              <Stack direction="row" spacing={2}>
                <TextField
                  size="small"
                  type="number"
                  label="Cooldown days"
                  value={editing.cooldown_days}
                  onChange={(e) => setEditing({ ...editing, cooldown_days: e.target.value })}
                  helperText="Days before the SAME rule may hit the same player again."
                  fullWidth
                  slotProps={{ htmlInput: { min: 0, max: 365 } }}
                />
                <TextField
                  size="small"
                  type="number"
                  label="Priority"
                  value={editing.priority}
                  onChange={(e) => setEditing({ ...editing, priority: e.target.value })}
                  helperText="Higher wins when several rules match one player."
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
                label="Enabled"
              />
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
          <Button variant="contained" onClick={saveRule} disabled={!editing?.name?.trim()}>
            Save
          </Button>
        </DialogActions>
      </Dialog>
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
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [productId, page, notify]);

  useEffect(() => {
    load();
  }, [load]);

  const openTranscript = (id) => {
    httpClient(`${API_URL}/admin/session/${id}`)
      .then(({ json }) => setDetail(json))
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
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
          ? `Delete ${ids.length} Telegram chats? This permanently removes their messages and logs AND purges each linked player (identity, seen photos, pings) from analytics.`
          : 'Delete this Telegram chat? This permanently removes its messages and logs AND purges the linked player (identity, seen photos, pings) from analytics.'
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
        many ? `${ids.length} chats deleted` : 'Chat deleted',
        { type: 'success' }
      );
      setSelected(new Set());
      setDetail(null);
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Delete failed', { type: 'error' });
    }
  };

  const pages = Math.max(1, Math.ceil((data.total || 0) / pageSize));
  const cols = isAdmin ? 10 : 8;

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        Telegram chats with Nika, kept apart from the support-widget
        conversations. An idle chat closes automatically (the “Session idle
        (min)” knob in Settings → Retention bot); when the player returns, a new
        chat starts and Nika is shown the tail of the previous one for
        continuity. Click a row for the transcript.
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
            Delete selected ({selected.size})
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
              <TableCell>Player</TableCell>
              <TableCell>TG user</TableCell>
              <TableCell>Lang</TableCell>
              <TableCell>Status</TableCell>
              <TableCell align="right">Msgs</TableCell>
              <TableCell align="right">Cost $</TableCell>
              <TableCell>Started</TableCell>
              <TableCell>Last activity</TableCell>
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
                      Delete
                    </Button>
                  </TableCell>
                )}
              </TableRow>
            ))}
            {data.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={cols}>
                  <Typography color="text.secondary" sx={{ py: 2 }}>
                    No Telegram chats yet.
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
            Prev
          </Button>
          <Typography variant="body2">
            {page} / {pages} · {data.total} chats
          </Typography>
          <Button size="small" disabled={page >= pages} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </Stack>
      )}
      <Dialog open={!!detail} onClose={() => setDetail(null)} maxWidth="md" fullWidth>
        <DialogTitle>
          Telegram chat · {detail?.session?.id}
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
                    sx={{ maxWidth: '80%', alignSelf: 'flex-end', width: 240 }}
                  >
                    <Typography variant="caption" color="text.secondary">
                      photo · {new Date(item.created_at).toLocaleString()}
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
                      </Typography>
                      <Typography sx={{ whiteSpace: 'pre-wrap' }}>{item.content}</Typography>
                    </CardContent>
                  </Card>
                )
              )}
            {(detail?.messages || []).length === 0 && (
              <Typography color="text.secondary">No messages.</Typography>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Typography variant="caption" color="text.secondary" sx={{ mr: 'auto', ml: 1 }}>
            Total cost: ${detail?.cost_usd_total ?? 0}
          </Typography>
          <Button onClick={() => setDetail(null)}>Close</Button>
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
  { key: 'messages', label: 'Messages' },
  { key: 'active_users', label: 'Active players' },
  { key: 'photos', label: 'Photos' },
  { key: 'pings', label: 'Pings' },
];

const FUNNEL_STEPS = [
  ['deeplinks_created', 'Deeplinks minted'],
  ['starts', '/start redemptions'],
  ['new_users', 'New linked players'],
  ['subscribed', 'Subscribed to channel'],
  ['engaged', 'Engaged (wrote a message)'],
  ['photo_receivers', 'Received a photo'],
  ['handoffs', 'Handed off'],
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
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
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
      ? `${(inRange.ping_reply_rate * 100).toFixed(1)}% reply rate`
      : 'no pings in range';

  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
        <TextField
          size="small"
          type="date"
          label="From"
          value={range.from}
          onChange={(e) => e.target.value && setRange({ ...range, from: e.target.value })}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <TextField
          size="small"
          type="date"
          label="To"
          value={range.to}
          onChange={(e) => e.target.value && setRange({ ...range, to: e.target.value })}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <Typography variant="caption" color="text.secondary">
          Both days inclusive. “Player base” below is lifetime; everything else
          counts this range.
        </Typography>
      </Stack>

      <Typography variant="h6" sx={{ mb: 1 }}>
        Player base
      </Typography>
      <Grid container spacing={2} alignItems="stretch" sx={{ mb: 2 }}>
        <KpiCard label="Linked players" value={base?.total} hint="lifetime deeplink entries" />
        <KpiCard label="Subscribed" value={base?.subscribed} hint="passed the channel gate" />
        <KpiCard label="Pings muted" value={base?.pings_muted} hint="opted out via /stop" />
        <KpiCard label="Unreachable" value={base?.unreachable} hint="blocked the bot / sends fail" />
      </Grid>

      <Typography variant="h6" sx={{ mb: 1 }}>
        In range
      </Typography>
      <Grid container spacing={2} alignItems="stretch" sx={{ mb: 2 }}>
        <KpiCard label="Active players" value={inRange?.active_users} hint="wrote in the range" />
        <KpiCard label="New players" value={inRange?.new_users} hint="first deeplink entry" />
        <KpiCard label="Player messages" value={inRange?.user_messages} />
        <KpiCard label="Photos sent" value={inRange?.photos_sent} />
        <KpiCard
          label="Pings sent"
          value={inRange?.pings_sent}
          hint={inRange?.pings_failed ? `${inRange.pings_failed} failed` : 'proactive nudges'}
        />
        <KpiCard label="Ping replies" value={inRange?.ping_replies} hint={replyRate} />
        <KpiCard label="Hand-offs" value={inRange?.handoffs} hint="to manager / site support" />
        <KpiCard
          label="Cost (USD)"
          value={inRange?.cost_usd != null ? `$${Number(inRange.cost_usd).toFixed(4)}` : undefined}
          hint="TG dialog + photo metadata"
        />
      </Grid>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
            Daily activity
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
                Entry funnel
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
                Stage distribution
              </Typography>
              <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
                Players per unlocked photo stage (lifetime).
              </Typography>
              <MiniBarChart
                data={overview?.stage_distribution || []}
                xKey="stage"
                yKey="users"
                label="Players"
              />
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Typography variant="h6" sx={{ mb: 1 }}>
        Linked players ({users.length})
      </Typography>
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Player</TableCell>
              <TableCell>TG user</TableCell>
              <TableCell>Entry</TableCell>
              <TableCell>VIP</TableCell>
              <TableCell align="right">Stage</TableCell>
              <TableCell align="right">Msgs</TableCell>
              <TableCell align="right">Photos</TableCell>
              <TableCell>Manager</TableCell>
              <TableCell>Last active</TableCell>
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
  pings: PingsTab,
  chats: ConversationsTab,
  analytics: AnalyticsTab,
};

const SUBTABS = {
  config: [
    ['config', 'Telegram config'],
    ['guide', 'Setup guide'],
  ],
  prompt: [
    ['prompt', 'Prompt preview'],
    ['variables', 'Prompt variables'],
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
    return <RequireProduct title="Retention · Telegram" />;
  }

  const Component = COMPONENTS[tab];
  const subtabs = groupFor(tab);

  return (
    <Box sx={{ p: 2, maxWidth: 1100 }}>
      <Title title="Retention · Telegram" />
      {subtabs && (
        <Tabs
          value={tab}
          onChange={(e, v) => setParams({ tab: v }, { replace: true })}
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
