import { useCallback, useEffect, useState } from 'react';
import { useNotify, usePermissions } from 'react-admin';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import FormControlLabel from '@mui/material/FormControlLabel';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import useIsMobile from '../lib/useIsMobile';
import rich from '../components/Rich';
import { t } from '../i18n';
import GridPagination from '../components/GridPagination';
import { notifyError } from '../lib/notifyError';
import { fmtDateTime } from '../lib/fmt';

// ---------------------------------------------------------------------------
// Idle pings — the agent's INACTIVITY ladder ("player quiet N days -> Nika
// writes first"): rules in retention_rules, swept by retention_idle.py from
// the same worker loop as the event agent (shared guards + dry-run). Rendered
// as the Idle pings tab of the Proactive agent page (the event triggers and
// the inactivity ladder are the same regime).
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
      notifyError(notify, e, t('Save failed'));
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
      notifyError(notify, e, t('Save failed'));
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
      notifyError(notify, e, t('Delete failed'));
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
          `${t('Sweep done')} — ${t('considered')} ${s.considered ?? 0}, ${t('sent')} ${s.sent ?? 0}, ${s.failed ?? 0} ${t('failed')}`,
          { type: 'success' }
        );
      }
      loadLedger();
    } catch (e) {
      notifyError(notify, e, t('Run failed'));
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
            'The proactive agent is DISABLED for this product, so idle pings do not fire either (they are part of the agent). Enable it in Retention → Settings → «Agent enabled».'
          )}
        </Alert>
      )}
      {agent && agent.v2_enabled && agent.v2_dry_run && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {t(
            'Dry-run (shadow mode) is ON: matched idle rules are logged to the agent’s Decisions ledger but nothing is sent. Turn it off in Retention → Settings when ready.'
          )}
        </Alert>
      )}
      <Alert severity="info" sx={{ mb: 2 }}>
        {rich(
          t(
            'Idle pings re-engage QUIET players — the inactivity side of the proactive agent (casino events are handled by the [Events](#/retention-agent?tab=events) tab). Each rule picks WHO (a trigger + inactivity window, optionally narrowed to VIP tiers) and WHAT (a message or a photo, with an English intent hint that grounds what Nika writes). Per-player caps, the minimum gap, quiet hours and the daily AI budget from Retention → Settings apply to every send; players opt out with `/stop`.'
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
        <Table size="small" sx={{ minWidth: 760 }}>
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
        <Table size="small" sx={{ minWidth: 680 }}>
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
                <TableCell>{fmtDateTime(p.created_at)}</TableCell>
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
        <GridPagination count={ledger.total || 0} page={page} perPage={pageSize}
                        onPage={setPage} />
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
                  "Media pings pick from the player's unlocked media (tier × stage gates apply). “photo” offers the mixed feed (photos + a couple of videos, the AI picks); “video” sends a video only."
                )}
                fullWidth
              >
                <MenuItem value="message">{t('message')}</MenuItem>
                <MenuItem value="photo">{t('photo')}</MenuItem>
                <MenuItem value="video">{t('video')}</MenuItem>
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
                  'Lowercase tier names from Retention → Settings → VIP tiers, e.g. gold, platinum.'
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

export default IdlePingsTab;
