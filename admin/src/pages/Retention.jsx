import { useCallback, useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import FormControlLabel from '@mui/material/FormControlLabel';
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
import { API_URL, httpClient, getToken } from '../httpClient';
import { getProductId } from '../productScope';

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
        <TextField
          label={`Telegram bot token ${p.has_telegram_bot_token ? '· set' : '· not set'}`}
          type="password"
          value={secrets.telegram_bot_token ?? ''}
          onChange={(e) => setSecrets({ ...secrets, telegram_bot_token: e.target.value })}
          fullWidth
          margin="dense"
          autoComplete="new-password"
        />
        <TextField
          label={`Player API key ${p.has_player_api_key ? '· set' : '· not set'}`}
          type="password"
          value={secrets.player_api_key ?? ''}
          onChange={(e) => setSecrets({ ...secrets, player_api_key: e.target.value })}
          fullWidth
          margin="dense"
          autoComplete="new-password"
        />
        <Button variant="contained" size="small" onClick={saveSecrets} sx={{ mt: 1 }}>
          Save secrets
        </Button>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Retention KB tab (flat scenario base — not the support kb_topics)
// ---------------------------------------------------------------------------
const EMPTY_KB = { title: '', trigger_when: '', body: '', links: '', sort_order: 0, active: true };

const KbEntryForm = ({ initial, onSave, onDelete }) => {
  const [form, setForm] = useState({
    ...initial,
    links: Array.isArray(initial.links) ? initial.links.join('\n') : initial.links || '',
  });
  return (
    <Card sx={{ mb: 1 }}>
      <CardContent>
        <Stack spacing={1}>
          <TextField label="Title" size="small" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          <TextField label="Trigger when (optional)" size="small" value={form.trigger_when || ''} onChange={(e) => setForm({ ...form, trigger_when: e.target.value })} />
          <TextField label="Body" multiline minRows={3} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
          <TextField label="Links (one per line)" multiline minRows={2} value={form.links} onChange={(e) => setForm({ ...form, links: e.target.value })} />
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField label="Sort order" size="small" type="number" value={form.sort_order} onChange={(e) => setForm({ ...form, sort_order: Number(e.target.value) })} sx={{ width: 120 }} />
            <FormControlLabel control={<Switch checked={Boolean(form.active)} onChange={(e) => setForm({ ...form, active: e.target.checked })} />} label="Active" />
            <Button
              variant="contained"
              size="small"
              disabled={!form.title || !form.body}
              onClick={() =>
                onSave({
                  ...form,
                  links: form.links.split('\n').map((s) => s.trim()).filter(Boolean),
                })
              }
            >
              Save
            </Button>
            {onDelete && (
              <Button size="small" color="error" onClick={onDelete}>
                Delete
              </Button>
            )}
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  );
};

const KbTab = ({ productId }) => {
  const notify = useNotify();
  const [items, setItems] = useState([]);

  const load = useCallback(() => {
    httpClient(`${API_URL}/admin/retention/kb?product_id=${productId}`)
      .then(({ json }) => setItems(json.items || []))
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [productId, notify]);

  useEffect(() => {
    load();
  }, [load]);

  const create = async (data) => {
    try {
      await httpClient(`${API_URL}/admin/retention/kb?product_id=${productId}`, {
        method: 'POST',
        body: JSON.stringify(data),
      });
      notify('Entry created', { type: 'success' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Create failed', { type: 'error' });
    }
  };

  const update = async (id, data) => {
    try {
      await httpClient(`${API_URL}/admin/retention/kb/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      });
      notify('Entry saved', { type: 'success' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    }
  };

  const remove = async (id) => {
    if (!window.confirm('Delete this entry?')) return;
    try {
      await httpClient(`${API_URL}/admin/retention/kb/${id}`, { method: 'DELETE' });
      notify('Entry deleted', { type: 'success' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Delete failed', { type: 'error' });
    }
  };

  return (
    <Box>
      <Typography variant="h6" sx={{ mb: 1 }}>
        New entry
      </Typography>
      <KbEntryForm initial={EMPTY_KB} onSave={create} />
      <Typography variant="h6" sx={{ my: 1 }}>
        Entries ({items.length})
      </Typography>
      {items.map((it) => (
        <KbEntryForm
          key={it.id}
          initial={it}
          onSave={(d) => update(it.id, d)}
          onDelete={() => remove(it.id)}
        />
      ))}
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
  if (!src) return <Box sx={{ width: 96, height: 96, bgcolor: 'action.hover' }} />;
  return <img src={src} alt="" style={{ width: 96, height: 96, objectFit: 'cover' }} />;
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
          <Typography variant="h6">Upload photo</Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ my: 1 }}>
            <Button variant="outlined" component="label">
              {file ? file.name : 'Choose file'}
              <input hidden type="file" accept="image/*" onChange={(e) => setFile(e.target.files[0])} />
            </Button>
            <TextField size="small" label="Description (grounds the caption)" value={upload.description} onChange={(e) => setUpload({ ...upload, description: e.target.value })} sx={{ minWidth: 280 }} />
            <TextField size="small" label="Tags (csv)" value={upload.tags} onChange={(e) => setUpload({ ...upload, tags: e.target.value })} />
            <TextField size="small" type="number" label="Level min (VIP tier)" value={upload.level_min} onChange={(e) => setUpload({ ...upload, level_min: Number(e.target.value) })} sx={{ width: 140 }} />
            <TextField size="small" type="number" label="Stage" value={upload.stage} onChange={(e) => setUpload({ ...upload, stage: Number(e.target.value) })} sx={{ width: 100 }} />
            <TextField size="small" label="Category" value={upload.category} onChange={(e) => setUpload({ ...upload, category: e.target.value })} />
            <Button variant="contained" onClick={doUpload} disabled={!file}>
              Upload
            </Button>
          </Stack>
        </CardContent>
      </Card>
      <Stack spacing={1}>
        {items.map((ph) => (
          <Card key={ph.id}>
            <CardContent>
              <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
                <PhotoPreview photoId={ph.id} />
                <TextField size="small" label="Description" defaultValue={ph.description || ''} onBlur={(e) => e.target.value !== (ph.description || '') && patch(ph.id, { description: e.target.value })} sx={{ minWidth: 260 }} />
                <TextField size="small" type="number" label="Level min" defaultValue={ph.level_min} onBlur={(e) => Number(e.target.value) !== ph.level_min && patch(ph.id, { level_min: Number(e.target.value) })} sx={{ width: 110 }} />
                <TextField size="small" type="number" label="Stage" defaultValue={ph.stage} onBlur={(e) => Number(e.target.value) !== ph.stage && patch(ph.id, { stage: Number(e.target.value) })} sx={{ width: 90 }} />
                <FormControlLabel control={<Switch checked={Boolean(ph.active)} onChange={(e) => patch(ph.id, { active: e.target.checked })} />} label="Active" />
                {ph.telegram_file_id && <Chip size="small" label="cached in TG" />}
                <Button size="small" color="error" onClick={() => remove(ph.id)}>
                  Delete
                </Button>
              </Stack>
            </CardContent>
          </Card>
        ))}
      </Stack>
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
      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <TextField size="small" label="Display name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        <TextField size="small" label="Telegram username (without @)" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <Button variant="outlined" onClick={create} disabled={!form.display_name || !form.username}>
          Add manager
        </Button>
      </Stack>
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

  return (
    <Box>
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6">Overview (30d)</Typography>
          <Typography component="pre" sx={{ fontFamily: 'monospace', fontSize: 13, whiteSpace: 'pre-wrap' }}>
            {overview ? JSON.stringify(overview, null, 2) : '…'}
          </Typography>
        </CardContent>
      </Card>
      <Typography variant="h6" sx={{ mb: 1 }}>
        Linked players ({users.length})
      </Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Player</TableCell>
            <TableCell>TG user</TableCell>
            <TableCell>Entry</TableCell>
            <TableCell>Stage</TableCell>
            <TableCell>Msgs</TableCell>
            <TableCell>Last seen</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {users.map((u, i) => (
            <TableRow key={i}>
              <TableCell>{u.player_id}</TableCell>
              <TableCell>{u.tg_user_id}</TableCell>
              <TableCell>{u.entry_type}</TableCell>
              <TableCell>{u.unlocked_stage}</TableCell>
              <TableCell>{u.message_count}</TableCell>
              <TableCell>{u.last_seen_at}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
};

// ---------------------------------------------------------------------------
// page shell — needs a concrete product (retention is strictly per-product)
// ---------------------------------------------------------------------------
const TABS = [
  ['config', 'Telegram config', ConfigTab],
  ['kb', 'Retention KB', KbTab],
  ['photos', 'Media', PhotosTab],
  ['managers', 'Managers', ManagersTab],
  ['analytics', 'Analytics', AnalyticsTab],
];

const Retention = () => {
  const [tab, setTab] = useState('config');
  const [productId, setProductId] = useState(getProductId());

  useEffect(() => {
    if (productId) return;
    // No product selected in the header switcher — fall back to the first
    // product the account can see (single-product deployments just work).
    httpClient(`${API_URL}/admin/structure`)
      .then(({ json }) => {
        const first = (json.partners || []).flatMap((pa) => pa.products || [])[0];
        if (first) setProductId(first.id);
      })
      .catch(() => {});
  }, [productId]);

  if (!productId) {
    return (
      <Box sx={{ p: 2 }}>
        <Title title="Retention · Telegram" />
        <Alert severity="info">
          Select a product in the header switcher to manage its retention bot.
        </Alert>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2, maxWidth: 1100 }}>
      <Title title="Retention · Telegram" />
      <Tabs value={tab} onChange={(e, v) => setTab(v)} sx={{ mb: 2 }} variant="scrollable">
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
