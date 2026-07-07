import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Title, useNotify } from 'react-admin';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import FormControlLabel from '@mui/material/FormControlLabel';
import Grid from '@mui/material/Grid';
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
        is pre-filled, replace it with the brand&apos;s own), upload photos in{' '}
        <Link href="#/retention?tab=photos">Media</Link> (description grounds the
        caption; <code>level_min</code> = VIP tier, <code>stage</code> =
        explicitness) and add live <Link href="#/retention?tab=managers">Managers</Link>{' '}
        (round-robin, sticky). Thresholds (daily photo cap, stage progression, VIP
        tiers, nonce TTL) are the <code>retention</code> group in{' '}
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
// Prompt preview tab — read-only view of the assembled RETENTION prompt
// (mirrors the support Prompt → Preview page) + the prompt variables the
// retention templates use. The values are edited in ONE place — the support
// Prompt → Prompt variables sub-tab — so this tab only shows them and links there.
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
  const [variables, setVariables] = useState([]);

  useEffect(() => {
    httpClient(`${API_URL}/admin/retention/effective-prompt?product_id=${productId}`)
      .then(({ json }) => {
        setPreview(json.effective_preview);
        setVariables(json.variables || []);
      })
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [productId, notify]);

  return (
    <Box sx={{ maxWidth: 1000 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        The complete retention prompt as the model receives it in the Telegram
        chat (read-only; language: {preview?.example?.lang || '—'}). To change
        the wording, edit <code>prompts.py</code> and redeploy.
      </Typography>
      <Alert severity="info" sx={{ mb: 2 }}>
        The prompt variables below are shared with the support chat and are
        edited in one place: <Link href="#/prompt?tab=variables">Support chat →
        Prompt → Prompt variables</Link>.
      </Alert>
      {variables.length > 0 && (
        <Card sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Prompt variables used by the retention prompt
            </Typography>
            <Box sx={{ overflowX: 'auto' }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Variable</TableCell>
                    <TableCell>Description</TableCell>
                    <TableCell>Current value</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {variables.map((v) => (
                    <TableRow key={v.key}>
                      <TableCell><code>{`{${v.key}}`}</code></TableCell>
                      <TableCell>{v.description}</TableCell>
                      <TableCell sx={{ whiteSpace: 'pre-wrap' }}>{v.value}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          </CardContent>
        </Card>
      )}
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
const PhotoPreview = ({ photoId }) => {
  const [src, setSrc] = useState(null);
  useEffect(() => {
    let url;
    fetch(`${API_URL}/admin/retention/photos/${photoId}/file`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => {
        if (blob) {
          url = URL.createObjectURL(blob);
          setSrc(url);
        }
      })
      .catch(() => {});
    return () => url && URL.revokeObjectURL(url);
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

const PhotosTab = ({ productId }) => {
  const notify = useNotify();
  const [items, setItems] = useState([]);
  const [upload, setUpload] = useState({ description: '', tags: '', level_min: 0, stage: 1, category: '' });
  const [file, setFile] = useState(null);

  const load = useCallback(() => {
    httpClient(`${API_URL}/admin/retention/photos?product_id=${productId}`)
      .then(({ json }) => setItems(json.items || []))
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [productId, notify]);

  useEffect(() => {
    load();
  }, [load]);

  const doUpload = async () => {
    if (!file) return;
    const fd = new FormData();
    fd.append('product_id', String(productId));
    fd.append('description', upload.description);
    fd.append('tags', upload.tags);
    fd.append('level_min', String(upload.level_min));
    fd.append('stage', String(upload.stage));
    fd.append('category', upload.category);
    fd.append('file', file);
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
    notify('Photo uploaded', { type: 'success' });
    setFile(null);
    load();
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
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Delete failed', { type: 'error' });
    }
  };

  return (
    <Box>
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Upload photo
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
                size="small"
                type="number"
                label="Level min (VIP tier)"
                value={upload.level_min}
                onChange={(e) => setUpload({ ...upload, level_min: Number(e.target.value) })}
                fullWidth
              />
            </Grid>
            <Grid size={{ xs: 6, sm: 3, md: 2 }}>
              <TextField
                size="small"
                type="number"
                label="Stage (explicitness)"
                value={upload.stage}
                onChange={(e) => setUpload({ ...upload, stage: Number(e.target.value) })}
                fullWidth
              />
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
              {file ? file.name : 'Choose file'}
              <input hidden type="file" accept="image/*" onChange={(e) => setFile(e.target.files[0])} />
            </Button>
            <Button variant="contained" onClick={doUpload} disabled={!file}>
              Upload
            </Button>
          </Stack>
        </CardContent>
      </Card>

      {items.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          No photos yet — upload the first one above.
        </Typography>
      )}
      <Grid container spacing={2} alignItems="stretch">
        {items.map((ph) => (
          <Grid size={{ xs: 12, sm: 6, md: 4 }} key={ph.id}>
            <Card sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
              <CardContent sx={{ flexGrow: 1 }}>
                <PhotoPreview photoId={ph.id} />
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
                  <Stack direction="row" spacing={1}>
                    <TextField
                      size="small"
                      type="number"
                      label="Level min"
                      defaultValue={ph.level_min}
                      onBlur={(e) =>
                        Number(e.target.value) !== ph.level_min &&
                        patch(ph.id, { level_min: Number(e.target.value) })
                      }
                      fullWidth
                    />
                    <TextField
                      size="small"
                      type="number"
                      label="Stage"
                      defaultValue={ph.stage}
                      onBlur={(e) =>
                        Number(e.target.value) !== ph.stage &&
                        patch(ph.id, { stage: Number(e.target.value) })
                      }
                      fullWidth
                    />
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
// Analytics tab
// ---------------------------------------------------------------------------
const AnalyticsTab = ({ productId }) => {
  const notify = useNotify();
  const [overview, setOverview] = useState(null);
  const [users, setUsers] = useState([]);

  useEffect(() => {
    httpClient(`${API_URL}/admin/retention/overview?product_id=${productId}`)
      .then(({ json }) => setOverview(json))
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
    httpClient(`${API_URL}/admin/retention/users?product_id=${productId}`)
      .then(({ json }) => setUsers(json.items || []))
      .catch(() => {});
  }, [productId, notify]);

  const kpis = [
    ['Linked players', overview?.users_total, 'total deeplink entries'],
    ['Subscribed', overview?.users_subscribed, 'passed the channel gate'],
    ['Active (30d)', overview?.users_active, 'wrote in the period'],
    ['Avg stage', overview?.avg_stage, 'photo explicitness unlocked'],
    ['Photos sent (30d)', overview?.photos_sent],
    ['Hand-offs (30d)', overview?.handoffs, 'to manager / site support'],
  ];

  return (
    <Box>
      <Grid container spacing={2} alignItems="stretch" sx={{ mb: 2 }}>
        {kpis.map(([label, value, hint]) => (
          <Grid size={{ xs: 6, sm: 4, md: 2 }} key={label}>
            <Card sx={{ height: '100%' }}>
              <CardContent
                sx={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 0.5 }}
              >
                <Typography
                  variant="overline"
                  color="text.secondary"
                  sx={{ lineHeight: 1.4 }}
                >
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
        ))}
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
const TABS = [
  ['guide', 'Setup guide', GuideTab],
  ['config', 'Telegram config', ConfigTab],
  ['kb', 'Retention KB', KbTab],
  ['prompt', 'Prompt preview', PromptTab],
  ['photos', 'Media', PhotosTab],
  ['managers', 'Managers', ManagersTab],
  ['analytics', 'Analytics', AnalyticsTab],
];

const Retention = () => {
  const [params, setParams] = useSearchParams();
  const productId = getProductId();
  const requested = params.get('tab');
  const tab = TABS.some(([v]) => v === requested) ? requested : 'config';

  // Retention data is strictly per-product; refuse to render without one so the
  // operator can't edit the default product by accident (same gate as KB /
  // Prompt / Translations).
  if (!productId) {
    return <RequireProduct title="Retention · Telegram" />;
  }

  return (
    <Box sx={{ p: 2, maxWidth: 1100 }}>
      <Title title="Retention · Telegram" />
      <Tabs
        value={tab}
        onChange={(e, v) => setParams({ tab: v }, { replace: true })}
        sx={{ mb: 2 }}
        variant="scrollable"
      >
        {TABS.map(([value, label]) => (
          <Tab key={value} value={value} label={label} />
        ))}
      </Tabs>
      {TABS.map(([value, , Component]) =>
        value === tab ? <Component key={value} productId={productId} /> : null
      )}
    </Box>
  );
};

export default Retention;
