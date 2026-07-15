import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Title, useNotify, usePermissions } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import FormControlLabel from '@mui/material/FormControlLabel';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { getProductId } from '../productScope';
import RequireProduct from '../components/RequireProduct';
import SecretField from '../components/SecretField';
import { SettingsModule } from './Settings';
import { t } from '../i18n';

/**
 * The one home for everything that CONFIGURES the retention bot: the Telegram
 * wiring (bot token, channel, webhook), the manager pool for hand-offs, and
 * the `retention` settings group (photos, progression, the proactive agent's
 * switches and guards). Content (KB, prompt, media) lives in its own sidebar
 * entries; this page is setup + tuning only.
 */

// ---------------------------------------------------------------------------
// Telegram config tab
// ---------------------------------------------------------------------------
const ConfigTab = ({ productId }) => {
  const notify = useNotify();
  // Managers are read-only server-side (403 on write) — pre-disable saves.
  const { permissions } = usePermissions();
  const readOnly = permissions !== 'admin';
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
        <Button variant="contained" onClick={save} disabled={readOnly} sx={{ mt: 1, mr: 1 }}>
          {t('Save config')}
        </Button>
        <Button variant="outlined" onClick={registerWebhook} disabled={readOnly} sx={{ mt: 1 }}>
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
        <Button variant="contained" size="small" onClick={saveSecrets} disabled={readOnly} sx={{ mt: 1 }}>
          {t('Save secrets')}
        </Button>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Managers tab (round-robin hand-off targets)
// ---------------------------------------------------------------------------
const ManagersTab = ({ productId }) => {
  const notify = useNotify();
  // Managers are read-only server-side (403 on write) — pre-disable writes.
  const { permissions } = usePermissions();
  const readOnly = permissions !== 'admin';
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
        <Button variant="outlined" onClick={create} disabled={!form.display_name || !form.username || readOnly}>
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
// page shell — three tabs; the ?tab= param survives reloads. The WHOLE page
// (tab strip included) sits behind the product gate: every retention surface
// is per-product, and GLOBAL retention defaults are edited from System →
// Settings territory by design — rendering the tabs at the All-products scope
// only invited edits against the wrong scope.
// ---------------------------------------------------------------------------
const TABS = [
  ['config', 'Telegram config'],
  ['managers', 'Managers'],
  ['params', 'Parameters'],
];

const RetentionSettingsInner = () => {
  const [params, setParams] = useSearchParams();
  const productId = getProductId();
  const requested = params.get('tab');
  const tab = TABS.some(([v]) => v === requested) ? requested : 'config';

  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('Retention settings')} />
      <Tabs
        value={tab}
        onChange={(e, v) => setParams({ tab: v }, { replace: true })}
        variant="scrollable"
        allowScrollButtonsMobile
        sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}
      >
        {TABS.map(([value, label]) => (
          <Tab key={value} value={value} label={t(label)} />
        ))}
      </Tabs>
      {tab === 'params' ? (
        <SettingsModule module="retention" />
      ) : tab === 'managers' ? (
        <ManagersTab productId={productId} />
      ) : (
        <ConfigTab productId={productId} />
      )}
    </Box>
  );
};

const RetentionSettings = () => (
  <RequireProduct title={t('Retention settings')}>
    <RetentionSettingsInner />
  </RequireProduct>
);

export default RetentionSettings;
