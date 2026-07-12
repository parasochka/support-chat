import { useCallback, useEffect, useState } from 'react';
import { Title, useNotify, usePermissions } from 'react-admin';
import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import IconButton from '@mui/material/IconButton';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import FormControlLabel from '@mui/material/FormControlLabel';
import Switch from '@mui/material/Switch';
import RefreshIcon from '@mui/icons-material/Refresh';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import DeleteIcon from '@mui/icons-material/Delete';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import { useLocation, useNavigate } from 'react-router-dom';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';
import RequireProduct from '../components/RequireProduct';
import { t } from '../i18n';
import rich from '../components/Rich';

/**
 * The proactive agent (event-driven) — the one regime that writes to players
 * first. This page is its home: the status header (switches and knobs live in
 * Settings → Retention bot), the canonical-event log with a simulator
 * (exercise the pipeline before the casino integration exists), the agent
 * decision ledger (state snapshot + guard verdict + decision + cost per row —
 * the full audit trail, dry-run rows included), and the "How it works &
 * testing" guide. Events and decisions are deletable (one row or the whole
 * log) so live testing never leaves duplicated rows behind.
 */

// Status chips carry long text (the worker-off explainer, ISO-ish timestamps,
// the "state food" hints). By default a Chip label is `white-space: nowrap`,
// so a long one can neither shrink nor wrap and it shoots off the right edge on
// a phone. Applied to a wrapping Stack, this lets each label wrap to as many
// lines as it needs and caps the chip at the container width, so the status
// block stacks tidily instead of stretching horizontally.
const WRAP_CHIPS_SX = {
  '& .MuiChip-root': { height: 'auto', maxWidth: '100%' },
  '& .MuiChip-label': {
    whiteSpace: 'normal',
    overflow: 'visible',
    textOverflow: 'clip',
    py: 0.4,
    lineHeight: 1.35,
  },
};

const fmtDT = (iso) => (iso ? new Date(iso).toLocaleString() : '—');
const fmtCost = (v) =>
  v == null ? '—' : `$${Number(v).toFixed(4)}`;

const ACTION_COLORS = {
  message: 'success',
  photo: 'success',
  silence: 'default',
  blocked: 'warning',
  skipped: 'default',
};

// ---------------------------------------------------------------------------
// Per-event sample payloads for the simulator. Field names mirror what the
// pipeline actually reads: bet_settled's amount/win_amount/bonus_money feed
// the 24h loss window; profile-ish fields (vip_level, balance, …) ride into
// the player snapshot via the payload bridge. Several variants per event so
// the different branches (win/loss, comfort, profile refresh) are one click.
// ---------------------------------------------------------------------------
const PAYLOAD_SAMPLES = {
  deposit_confirmed: [
    { label: 'regular deposit', payload: { amount: 100, currency: 'USDT', method: 'crypto' } },
    { label: 'first deposit', payload: { amount: 25, currency: 'USDT', method: 'card', first_deposit: true } },
    { label: 'big + profile refresh', payload: { amount: 500, currency: 'EUR', method: 'card', vip_level: 'Gold', balance: 750 } },
  ],
  deposit_initiated: [
    { label: 'card deposit started', payload: { amount: 100, currency: 'EUR', method: 'card' } },
  ],
  deposit_failed: [
    { label: 'card declined', payload: { amount: 100, currency: 'EUR', method: 'card', reason: 'card_declined' } },
    { label: '3-D Secure failed', payload: { amount: 50, currency: 'EUR', method: 'card', reason: '3ds_failed' } },
  ],
  withdrawal_settled: [
    { label: 'payout received', payload: { amount: 250, currency: 'USDT', method: 'crypto' } },
    { label: 'big win payout', payload: { amount: 2000, currency: 'USDT', method: 'crypto' } },
  ],
  bet_settled: [
    { label: 'losing bet', payload: { amount: 50, win_amount: 0, currency: 'USDT', game: 'Book of Ra' } },
    { label: 'winning bet', payload: { amount: 20, win_amount: 75, currency: 'USDT', game: 'Gates of Olympus' } },
    { label: 'big loss (crosses threshold)', payload: { amount: 300, win_amount: 0, currency: 'USDT', game: 'Blackjack VIP' } },
    { label: 'bonus-money round (excluded)', payload: { amount: 40, win_amount: 0, currency: 'USDT', bonus_money: true } },
  ],
  session_started: [
    { label: 'mobile login', payload: { platform: 'mobile' } },
    { label: 'desktop login', payload: { platform: 'desktop' } },
  ],
  session_ended: [
    { label: 'session over', payload: { duration_min: 42 } },
  ],
  bonus_granted: [
    { label: 'deposit match granted', payload: { bonus_id: 'welcome100', type: 'deposit_match', amount: 100, currency: 'USDT' } },
    { label: 'free spins granted', payload: { bonus_id: 'freespins50', type: 'free_spins', spins: 50 } },
  ],
  bonus_claimed: [
    { label: 'bonus activated', payload: { bonus_id: 'welcome100', type: 'deposit_match' } },
  ],
  bonus_completed: [
    { label: 'wagering done, payout', payload: { bonus_id: 'welcome100', payout: 80, currency: 'USDT' } },
  ],
  bonus_expired: [
    { label: 'free spins expired unused', payload: { bonus_id: 'freespins50', type: 'free_spins' } },
    { label: 'match bonus expired', payload: { bonus_id: 'reload50', type: 'deposit_match', amount: 50 } },
  ],
  kyc_started: [
    { label: 'verification started', payload: {} },
  ],
  kyc_approved: [
    { label: 'verification passed', payload: { level: 'full' } },
  ],
  kyc_rejected: [
    { label: 'document unreadable', payload: { reason: 'document_unreadable' } },
  ],
  xp_granted: [
    { label: 'mission XP', payload: { amount: 150, source: 'mission' } },
  ],
  level_up: [
    { label: 'new level', payload: { level: 7, previous: 6 } },
    { label: 'level + fresh VIP tier', payload: { level: 12, previous: 11, vip_level: 'Silver' } },
  ],
  class_up: [
    { label: 'new loyalty class', payload: { class: 'Gold', previous: 'Silver', vip_level: 'Gold' } },
  ],
  downgrade: [
    { label: 'class downgraded', payload: { class: 'Silver', previous: 'Gold', vip_level: 'Silver' } },
  ],
  highlights_pack_opened: [
    { label: 'pack opened', payload: { pack_id: 'weekly_wins' } },
  ],
  highlights_pack_completed: [
    { label: 'pack completed', payload: { pack_id: 'weekly_wins', reward: 'free_spins' } },
  ],
  check_in_done: [
    { label: 'daily check-in', payload: { streak_days: 5 } },
  ],
  mission_completed: [
    { label: 'mission done', payload: { mission_id: 'daily_spin', reward_xp: 50 } },
  ],
};

const samplesFor = (eventName) =>
  PAYLOAD_SAMPLES[eventName] || [{ label: 'empty', payload: {} }];

const StatusHeader = ({ status, onRefresh, onRun, canWrite, running }) => {
  if (!status) return null;
  const act = status.activity || {};
  const todayMix = Object.entries(act.decisions_today || {})
    .map(([k, v]) => `${k}: ${v}`)
    .join(' · ');
  return (
    <Card sx={{ mb: 2 }}>
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={WRAP_CHIPS_SX}>
          <Chip
            label={status.v2_enabled ? t('Agent ENABLED') : t('Agent DISABLED (no proactive messages)')}
            color={status.v2_enabled ? 'success' : 'default'}
          />
          <Chip
            label={status.v2_dry_run ? t('DRY-RUN (decides, never sends)') : t('LIVE sending')}
            color={status.v2_dry_run ? 'info' : 'warning'}
          />
          <Chip
            label={`${t('today')}: ${fmtCost(status.cost_today_usd)} / ${t('budget')} ${
              status.daily_budget_usd ? `$${status.daily_budget_usd}` : t('none')
            }`}
          />
          <Chip label={`${t('queued events')}: ${status.queued_events}`} />
          <Box sx={{ flex: 1 }} />
          <Button size="small" startIcon={<RefreshIcon />} onClick={onRefresh}>
            {t('Refresh')}
          </Button>
          <Tooltip title={t('Drain the event queue through the pipeline now (the worker does the same on its timer).')}>
            <span>
              <Button
                size="small"
                variant="contained"
                startIcon={<PlayArrowIcon />}
                onClick={onRun}
                disabled={!canWrite || running || !status.v2_enabled}
              >
                {running ? t('Running…') : t('Process queue now')}
              </Button>
            </span>
          </Tooltip>
        </Stack>
        {/* Agent / worker liveness — derived from the durable tables, so it
            answers "is the agent actually running and what did it just do?" */}
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mt: 1, ...WRAP_CHIPS_SX }}>
          <Chip
            size="small"
            variant="outlined"
            color={status.scheduler_enabled ? 'success' : 'error'}
            label={
              status.scheduler_enabled
                ? `${t('worker: running every')} ${status.sweep_interval_sec}s`
                : t('worker: OFF (RETENTION_SCHEDULER_ENABLED=0 in deploy env — only «Process queue now» works)')
            }
          />
          <Chip size="small" variant="outlined" label={`${t('last event')}: ${fmtDT(act.last_event_at)}`} />
          <Chip size="small" variant="outlined" label={`${t('last processed')}: ${fmtDT(act.last_processed_at)}`} />
          <Chip size="small" variant="outlined" label={`${t('last decision')}: ${fmtDT(act.last_decision_at)}`} />
          <Chip
            size="small"
            variant="outlined"
            label={`${t('today')}: ${todayMix || t('no decisions')}${
              act.delivered_today ? ` · ${t('delivered')} ${act.delivered_today}` : ''
            }`}
          />
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          {t('Switches and knobs live in Settings → Retention bot («Proactive agent» + «Send-frequency guards»). The worker interval is a live setting too — 5s means near-realtime reactions. Dry-run ships ON: the agent decides and logs to the ledger below without sending — review its decisions, then turn dry-run off. New here? Read the «How it works & testing» tab.')}
        </Typography>
      </CardContent>
    </Card>
  );
};

const Simulator = ({ status, onDone, canWrite }) => {
  const notify = useNotify();
  const [eventName, setEventName] = useState('deposit_confirmed');
  const [playerId, setPlayerId] = useState('');
  const [payload, setPayload] = useState(
    JSON.stringify(samplesFor('deposit_confirmed')[0].payload),
  );
  const [busy, setBusy] = useState(false);
  // Linked Telegram accounts — the explicit-recipient picker. With one test
  // player linked to several Telegram accounts, "auto" sends to whichever
  // link was updated last; picking an account pins the recipient.
  const [linked, setLinked] = useState([]);
  const [tgUserId, setTgUserId] = useState('');

  useEffect(() => {
    httpClient(withProduct(`${API_URL}/admin/retention/users?limit=200`))
      .then(({ json }) => setLinked(json.items || []))
      .catch(() => {});
  }, []);

  const pickTgUser = (value) => {
    setTgUserId(value);
    // Convenience: picking an account also fills its player_id, so the event
    // is consistent without retyping.
    const u = linked.find((x) => String(x.tg_user_id) === String(value));
    if (u?.player_id) setPlayerId(u.player_id);
  };

  const decisionEvents = status?.decision_events || [];
  const samples = samplesFor(eventName);
  const wakesAgent = decisionEvents.includes(eventName);
  const isLossFeed = eventName === 'bet_settled';

  const pickEvent = (name) => {
    setEventName(name);
    // Auto-fill with the event's first sample so the payload always matches
    // the selected event (stale JSON from the previous event is the #1 trap).
    setPayload(JSON.stringify(samplesFor(name)[0].payload));
  };

  const send = async () => {
    let parsed = {};
    if (payload.trim()) {
      try {
        parsed = JSON.parse(payload);
      } catch {
        notify(t('Payload is not valid JSON'), { type: 'error' });
        return;
      }
    }
    setBusy(true);
    try {
      await httpClient(withProduct(`${API_URL}/admin/retention/v2/simulate-event`), {
        method: 'POST',
        body: JSON.stringify({
          event_name: eventName,
          player_id: playerId,
          payload: parsed,
          tg_user_id: tgUserId ? Number(tgUserId) : null,
        }),
      });
      notify(t('Event injected'), { type: 'success' });
      onDone();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Simulation failed'), { type: 'error' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card sx={{ mb: 2 }}>
      <CardContent>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          {t('Event simulator — inject a canonical event as if the casino sent it')}
        </Typography>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1}>
          <TextField
            select
            label={t('Event')}
            size="small"
            value={eventName}
            onChange={(e) => pickEvent(e.target.value)}
            sx={{ flex: { xs: '1 1 auto', md: '0 0 240px' } }}
          >
            {(status?.canonical_events || []).map((n) => (
              <MenuItem key={n} value={n}>
                {n}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label={t('Telegram recipient')}
            size="small"
            value={tgUserId}
            onChange={(e) => pickTgUser(e.target.value)}
            sx={{ flex: { xs: '1 1 auto', md: '0 0 240px' } }}
            helperText={
              tgUserId
                ? undefined
                : t('auto = the player’s most recently active link')
            }
          >
            <MenuItem value="">{t('auto (by player id)')}</MenuItem>
            {linked.map((u) => (
              <MenuItem key={u.tg_user_id} value={String(u.tg_user_id)}>
                {u.tg_username ? `@${u.tg_username}` : u.tg_user_id}
                {u.player_id ? ` · ${u.player_id}` : ''}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label={t('Player id')}
            size="small"
            value={playerId}
            onChange={(e) => setPlayerId(e.target.value)}
            sx={{ flex: { xs: '1 1 auto', md: '0 0 200px' } }}
            placeholder={t('the casino player_id')}
          />
          <TextField
            label={t('Payload (JSON)')}
            size="small"
            value={payload}
            onChange={(e) => setPayload(e.target.value)}
            sx={{ flex: 1 }}
          />
          <Button variant="contained" onClick={send} disabled={busy || !canWrite || !playerId.trim()}>
            {busy ? t('Sending…') : t('Inject event')}
          </Button>
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mt: 1, ...WRAP_CHIPS_SX }}>
          <Typography variant="caption" color="text.secondary">
            {t('Sample payloads:')}
          </Typography>
          {samples.map((s) => (
            <Chip
              key={s.label}
              size="small"
              variant="outlined"
              label={t(s.label)}
              onClick={() => setPayload(JSON.stringify(s.payload))}
            />
          ))}
          <Box sx={{ flex: 1 }} />
          {isLossFeed ? (
            <Chip size="small" color="info" variant="outlined"
              label={t('state food — wakes the agent only when the 24h net loss crosses the high-loss threshold')} />
          ) : (
            <Chip
              size="small"
              color={wakesAgent ? 'success' : 'default'}
              variant="outlined"
              label={wakesAgent ? t('wakes the agent (a decision will be ledgered)') : t('state food only (no decision, feeds player state)')}
            />
          )}
        </Stack>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Events / Decisions tables (deletable — live-testing cleanup)
// ---------------------------------------------------------------------------
const ClearAllButton = ({ label, onClear, canWrite }) => (
  <Tooltip title={label}>
    <span>
      <Button
        size="small"
        color="error"
        startIcon={<DeleteSweepIcon />}
        onClick={onClear}
        disabled={!canWrite}
      >
        {t('Clear all')}
      </Button>
    </span>
  </Tooltip>
);

const EventsTab = ({ events, canWrite, onDelete, onClear }) => (
  <Card>
    <CardContent>
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
          {t('The event log is also the state resolver’s memory (loss window, recent activity) — deleting rows rewrites that derived state. Meant for wiping simulator/test rows.')}
        </Typography>
        <ClearAllButton
          label={t("Delete ALL of this product's events (decisions stay, minus the event link).")}
          onClear={onClear}
          canWrite={canWrite}
        />
      </Stack>
      <Box sx={{ overflowX: 'auto' }}>
      <Table size="small" sx={{ minWidth: 720 }}>
        <TableHead>
          <TableRow>
            <TableCell>{t('When (casino time)')}</TableCell>
            <TableCell>{t('Event')}</TableCell>
            <TableCell>{t('Player')}</TableCell>
            <TableCell>{t('Source')}</TableCell>
            <TableCell>{t('Payload')}</TableCell>
            <TableCell>{t('Processed')}</TableCell>
            <TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {(events?.items || []).map((e) => (
            <TableRow key={e.id}>
              <TableCell>{fmtDT(e.ts)}</TableCell>
              <TableCell>
                <code>{e.event_name}</code>
              </TableCell>
              <TableCell>{e.player_id}</TableCell>
              <TableCell>{e.source}</TableCell>
              <TableCell sx={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                <code>{JSON.stringify(e.payload)}</code>
              </TableCell>
              <TableCell>{e.processed_at ? fmtDT(e.processed_at) : t('queued')}</TableCell>
              <TableCell align="right">
                <Tooltip title={t('Delete this event')}>
                  <span>
                    <IconButton size="small" onClick={() => onDelete(e.id)} disabled={!canWrite}>
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
              </TableCell>
            </TableRow>
          ))}
          {!(events?.items || []).length && (
            <TableRow>
              <TableCell colSpan={7}>
                <Typography variant="body2" color="text.secondary">
                  {rich(t('No events yet. The casino posts them to `POST /partner/{product_id}/event`, or inject one with the simulator above.'))}
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      </Box>
    </CardContent>
  </Card>
);

const DecisionsTab = ({ decisions, canWrite, onDelete, onClear }) => (
  <Card>
    <CardContent>
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
          {t('Deleting a decision “refunds” its cost from today’s budget and re-arms the same-event cooldown for that event type — so a wiped test decision can be re-run immediately.')}
        </Typography>
        <ClearAllButton
          label={t("Delete ALL of this product's decisions (resets today's budget counter and all same-event cooldowns).")}
          onClear={onClear}
          canWrite={canWrite}
        />
      </Stack>
      <Box sx={{ overflowX: 'auto' }}>
      <Table size="small" sx={{ minWidth: 900 }}>
        <TableHead>
          <TableRow>
            <TableCell>{t('When')}</TableCell>
            <TableCell>{t('Player')}</TableCell>
            <TableCell>{t('Event')}</TableCell>
            <TableCell>{t('Decision')}</TableCell>
            <TableCell>{t('Tone')}</TableCell>
            <TableCell>{t('Why / brief')}</TableCell>
            <TableCell>{t('Guards')}</TableCell>
            <TableCell>{t('Delivered')}</TableCell>
            <TableCell align="right">{t('Cost')}</TableCell>
            <TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {(decisions?.items || []).map((d) => (
            <TableRow key={d.id}>
              <TableCell>{fmtDT(d.created_at)}</TableCell>
              <TableCell>
                {/* One player_id can be linked to several Telegram accounts
                    (test setups) — the @username names the actual recipient. */}
                <Typography variant="body2">
                  {d.full_name || d.player_id || '—'}
                </Typography>
                {d.tg_username && (
                  <Typography variant="caption" color="text.secondary">
                    @{d.tg_username}
                  </Typography>
                )}
              </TableCell>
              <TableCell>
                <code>{d.event_name}</code>
              </TableCell>
              <TableCell>
                <Stack direction="row" spacing={0.5}>
                  <Chip size="small" label={d.action} color={ACTION_COLORS[d.action] || 'default'} />
                  {d.dry_run && <Chip size="small" label="dry-run" variant="outlined" />}
                </Stack>
              </TableCell>
              <TableCell>{d.tone || '—'}</TableCell>
              <TableCell sx={{ maxWidth: 340 }}>
                <Typography variant="body2">{d.reason || '—'}</Typography>
                {d.intent && (
                  <Typography variant="caption" color="text.secondary">
                    {t('brief:')} {d.intent}
                  </Typography>
                )}
              </TableCell>
              <TableCell sx={{ maxWidth: 220 }}>
                {(d.guard?.reasons || []).join(', ') ||
                  (d.guard?.comfort ? t('comfort window') : t('clear'))}
              </TableCell>
              <TableCell>{d.delivered ? t('yes') : d.detail || t('no')}</TableCell>
              <TableCell align="right">{fmtCost(d.cost_usd)}</TableCell>
              <TableCell align="right">
                <Tooltip title={t('Delete this decision')}>
                  <span>
                    <IconButton size="small" onClick={() => onDelete(d.id)} disabled={!canWrite}>
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
              </TableCell>
            </TableRow>
          ))}
          {!(decisions?.items || []).length && (
            <TableRow>
              <TableCell colSpan={10}>
                <Typography variant="body2" color="text.secondary">
                  {t('No decisions yet — inject an event and press «Process queue now».')}
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      </Box>
    </CardContent>
  </Card>
);

// ---------------------------------------------------------------------------
// System log — the durable retention_v2_* admin events (the admin-readable
// mirror of the Railway `retention_v2_*` log lines)
// ---------------------------------------------------------------------------
const LOG_TYPE_COLORS = {
  retention_v2_decision: 'success',
  retention_v2_simulated_event: 'info',
  retention_v2_run_manual: 'info',
};

const LogsTab = ({ logs }) => (
  <Card>
    <CardContent>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {rich(t('Every agent action leaves a durable trace here: decisions, simulator injections, manual queue runs, deletes. The same facts stream to the deploy (Railway) logs as `retention_v2_*` lines — decisions, guard blocks and failed sends included — so this view and the deploy logs always tell one story.'))}
      </Typography>
      <Box sx={{ overflowX: 'auto' }}>
      <Table size="small" sx={{ minWidth: 560 }}>
        <TableHead>
          <TableRow>
            <TableCell>{t('When')}</TableCell>
            <TableCell>{t('Type')}</TableCell>
            <TableCell>{t('Details')}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {(logs?.items || []).map((l) => (
            <TableRow key={l.id}>
              <TableCell sx={{ whiteSpace: 'nowrap' }}>{fmtDT(l.created_at)}</TableCell>
              <TableCell>
                <Chip
                  size="small"
                  variant="outlined"
                  color={LOG_TYPE_COLORS[l.type] || 'default'}
                  label={l.type.replace('retention_v2_', '')}
                />
              </TableCell>
              <TableCell>
                <code style={{ fontSize: '0.8rem' }}>{JSON.stringify(l.payload)}</code>
              </TableCell>
            </TableRow>
          ))}
          {!(logs?.items || []).length && (
            <TableRow>
              <TableCell colSpan={3}>
                <Typography variant="body2" color="text.secondary">
                  {t('No log entries yet — they appear as soon as the pipeline processes an event (or you inject one).')}
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      </Box>
    </CardContent>
  </Card>
);

// ---------------------------------------------------------------------------
// How it works & testing — the operator's guide to the whole agent loop
// ---------------------------------------------------------------------------
const Section = ({ title, children }) => (
  <Box sx={{ mb: 3 }}>
    <Typography variant="h6" sx={{ mb: 1 }}>
      {title}
    </Typography>
    {children}
  </Box>
);

const P = ({ children }) => (
  <Typography variant="body2" sx={{ mb: 1 }}>
    {children}
  </Typography>
);

const LI = ({ children }) => (
  <Typography component="li" variant="body2" sx={{ mb: 0.5 }}>
    {children}
  </Typography>
);

const GuideTab = ({ status }) => {
  const g = status?.guards || {};
  const decisionEvents = status?.decision_events || [];
  const photoEvents = status?.photo_events || [];
  const canonical = status?.canonical_events || [];
  const stateFood = canonical.filter(
    (n) => !decisionEvents.includes(n) && n !== 'bet_settled',
  );
  return (
    <Card>
      <CardContent>
        <Section title={t('What the proactive agent is')}>
          <P>
            {rich(t('An event-driven agent that reacts to what just happened at the casino. A canonical event (deposit, big loss, level-up, …) arrives, a cheap AI call decides whether Nika should say something, and if yes the normal retention persona writes the message. Very often the correct decision is **silence** — that is by design, and silence is logged too.'))}
          </P>
          <P>{t('The pipeline for every event, in order:')}</P>
          <Box component="ol" sx={{ pl: 3, my: 0 }}>
            <LI>
              {rich(t('**Event arrives** — from the casino’s webhook `POST /partner/{product_id}/event` or from the simulator on this page. Events are idempotent by `event_id`: a retried webhook is counted, not stored twice.'))}
            </LI>
            <LI>
              {rich(t('**State resolver (deterministic)** — computes the player snapshot the agent will see: user status (registered/active/at-risk/dormant), risk state, lifecycle stage, and the 24h net-loss window summed from `bet_settled` payloads.'))}
            </LI>
            <LI>
              {rich(t('**Guards (deterministic)** — decide whether contact is allowed at all and which actions are permitted (message / photo / silence). The model can never override a guard. See the table below.'))}
            </LI>
            <LI>
              {rich(t('**Agent decision** — one cheap strict-JSON model call. Input: the state snapshot, the event, the player’s recent events, the tail of their Telegram conversation, and the guard constraints. Output: `action` (silence/message/photo), `tone` (warm/celebrate/comfort/neutral), and a short `intent` brief. Anything malformed degrades to silence.'))}
            </LI>
            <LI>
              {rich(t('**Message generation** — the SAME persona stack that answers Telegram chats writes the text from the agent’s brief. Nothing here is agent-specific: persona, tone of voice, KB, language all come from the regular retention configuration (next section).'))}
            </LI>
            <LI>
              {rich(t('**Ledger** — ONE row per decision, whatever the outcome (sent, silence, blocked, dry-run), with the state snapshot, guard verdict, the agent’s reasoning and the summed cost. “Why did/didn’t the bot write?” is always answerable from the Decisions tab.'))}
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Turning it on and off')}>
          <Box component="ul" sx={{ pl: 3, my: 0 }}>
            <LI>
              {rich(t('**Agent enabled** (Settings → Retention bot → «Proactive agent») is the per-product switch. Off = the agent never writes first; queued events wait unprocessed and the ledger stays readable. The dialogue bot (replies to players who write), escalation hand-offs and the photo machinery inside dialogue are never affected.'))}
            </LI>
            <LI>
              {rich(t('**Dry-run** keeps the agent deciding and logging without sending — the safe review mode.'))}
            </LI>
            <LI>
              {rich(t('**Worker interval** (same Settings section) is how often the background worker drains the event queue — it applies live on the next tick, and 5 seconds gives near-realtime reactions.'))}
            </LI>
            <LI>
              {rich(t('**Deploy-level master switch**: `RETENTION_SCHEDULER_ENABLED` (Railway env) starts the background worker at all; with it off only «Process queue now» moves the queue. The worker chip in the header shows this.'))}
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Where the voice, persona and content come from')}>
          <Box component="ul" sx={{ pl: 3, my: 0 }}>
            <LI>
              {rich(t('**Persona & tone of voice** — Retention → Prompt variables (persona name, role, brand, products, `retention_tone_of_voice`). The agent only writes a short brief; the persona prompt writes the actual words. The full assembled prompt is visible in Retention → Prompt preview.'))}
            </LI>
            <LI>
              {rich(t('**Facts the bot may use** — the Retention KB document (Retention → KB), same as in dialogue.'))}
            </LI>
            <LI>
              {rich(t('**The message header** — every proactive message goes out under the italic “✨ A little note from {persona}” line: the `rtn_ping_header` key in Translations (per language).'))}
            </LI>
            <LI>
              {rich(t('**The inline button** — when the model attaches a `[[LINK:url]]` matching the occasion, the validated Site map page (Support chat → Site map) rides under the message as one button. Comfort mode strips it.'))}
            </LI>
            <LI>
              {rich(t('**Photos** — the Media library (Retention → Media), same stage × VIP gating and daily caps as in dialogue. Only positive occasions may carry a photo:'))}{' '}
              <code>{photoEvents.join(', ')}</code>.
            </LI>
            <LI>
              {rich(t('**Language** — the player’s sticky conversation language (the same one their Telegram chat drifted to).'))}
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Which events wake the agent')}>
          <P>
            {rich(t('**Decision-worthy** (the agent is consulted, a ledger row appears):'))}{' '}
            <code>{decisionEvents.join(', ') || '—'}</code>
          </P>
          <P>
            {rich(t('**Special:** `bet_settled` wakes the agent only when the player’s 24h net loss crosses the high-loss threshold (Settings → «High-loss threshold»); below it the event silently feeds the loss window.'))}
          </P>
          <P>
            {rich(t('**State food only** (no decision — they update activity timestamps, the loss window and the profile snapshot):'))}{' '}
            <code>{stateFood.join(', ') || '—'}</code>
          </P>
          <P>
            {rich(t("Every stored event also refreshes the player's activity timestamps: `deposit_confirmed → last_deposit_at`, `session_started/ended → last_login_at`, `bet_settled → last_played_at` — the state resolver (idle days, days since deposit) reads them."))}
          </P>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Guards — how often the agent may write to one player')}>
          <P>
            {t('Deterministic rails the model can never override. They are the knobs that decide the send frequency — all editable live in Settings → Retention bot → «Send-frequency guards». Current values for this product are shown in the table. Each blocked decision lists its reasons in the Guards column of the ledger:')}
          </P>
          <Box sx={{ overflowX: 'auto', maxWidth: 980 }}>
          <Table size="small" sx={{ minWidth: 640 }}>
            <TableHead>
              <TableRow>
                <TableCell>{t('Guard reason')}</TableCell>
                <TableCell>{t('Current value')}</TableCell>
                <TableCell>{t('What it means / which setting drives it')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {[
                ['not_subscribed', '—', t('The player has not passed the channel-subscription gate.')],
                ['player_opted_out', '—', t('The player sent /stop (they can /resume).')],
                ['bot_blocked_by_player', '—', t('Telegram returned 403 — the player blocked the bot.')],
                ['daily_cap_reached', `${g.ping_daily_cap ?? '—'} ${t('/ day')}`, t('«Max proactive messages per player per day» — the hard daily ceiling.')],
                ['min_gap_not_elapsed', `${g.ping_min_gap_hours ?? '—'} ${t('h')}`, t('«Min gap between messages (hours)» — spacing between any two proactive messages to one player (0 = off). Lower it to react to several events per day.')],
                ['same_event_cooldown', `${g.same_event_cooldown_hours ?? '—'} ${t('h')}`, t('«Same-event cooldown (hours)» — one reaction per event type per player per window. Set 0 while testing to re-run the same event.')],
                ['quiet_hours', `${g.quiet_hours_start ?? '—'}–${g.quiet_hours_end ?? '—'} (UTC${(g.quiet_hours_utc_offset ?? 0) >= 0 ? '+' : ''}${g.quiet_hours_utc_offset ?? 0})`, t('«Quiet hours start/end/UTC offset» — no proactive contact at night.')],
                ['daily_budget_reached', g.daily_budget_usd ? `$${g.daily_budget_usd} ${t('/ day')}` : t('no budget'), t('«Daily AI budget (USD)» — today’s ledger cost hit the budget.')],
                ['comfort window', `${g.loss_comfort_hours ?? '—'} ${t('h')} / $${g.loss_high_usd ?? '—'}`, t('«Loss comfort window» + «High-loss threshold» — after a big loss: empathetic tone only, no photo, no link, no play talk.')],
              ].map(([k, cur, v]) => (
                <TableRow key={k}>
                  <TableCell>
                    <code>{k}</code>
                  </TableCell>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{cur}</TableCell>
                  <TableCell>{v}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('How to test, step by step')}>
          <Box component="ol" sx={{ pl: 3, my: 0 }}>
            <LI>
              {rich(t('Select the product in the header switcher, then in Settings → Retention bot → «Proactive agent» turn **Agent enabled** ON and leave **dry-run** ON (safe: nothing is sent).'))}
            </LI>
            <LI>
              {rich(t('Link a test player to the Telegram bot: open the bot through a deeplink (easiest: escalate in the support-chat widget, or `POST /api/retention/deeplink` with a test `user_context`), press /start and subscribe to the channel. The `player_id` from that handshake is the id you feed the simulator.'))}
            </LI>
            <LI>
              {rich(t('In the simulator pick an event (e.g. `deposit_confirmed`), enter that player id, pick a sample payload, «Inject event». If several Telegram accounts are linked to the same test player, pick the exact recipient in «Telegram recipient» — on «auto» the message goes to the player’s most recently active link (the Decisions tab shows the actual @username either way).'))}
            </LI>
            <LI>
              {rich(t('Press «Process queue now» and open the Decisions tab: you should see the action, tone, the agent’s brief and reasoning, the guard verdict and the cost. Try a losing-day scenario: inject a few `bet_settled` «big loss» samples and watch the comfort constraints appear.'))}
            </LI>
            <LI>
              {rich(t('Blocked? The Guards column names the reason and the table above names the setting. For repeated testing: set «Same-event cooldown» to 0, raise the daily cap, widen quiet hours — or simply delete the previous decision row (that re-arms the cooldown and refunds the budget).'))}
            </LI>
            <LI>
              {rich(t('When the decisions look right, turn **dry-run OFF** and re-inject: the message reaches the player in Telegram — italic header + persona text (+ button/photo when chosen). It is also persisted into the player’s Retention → Conversations transcript.'))}
            </LI>
            <LI>
              {rich(t('Clean up after yourself: delete test rows one by one or «Clear all» on both tabs. Costs already logged to Analytics stay (they were real OpenAI calls).'))}
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title={t('Costs')}>
          <P>
            {rich(t('Every decision is one cheap model call; a sent message adds one generation call. Both land in `ai_interaction_logs` and in the Telegram cost split on Retention → Analytics. The daily budget (Settings) is a hard stop: when the day’s summed ledger cost reaches it, the agent stays quiet until tomorrow.'))}
          </P>
        </Section>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Triggers tab — which canonical events WAKE the agent (reach a decision) and
// which stay "state food" (update the player state silently). The set is the
// per-product `retention.v2_decision_events` setting; saving merges it into
// the product's stored retention overrides so no other knob is touched.
// ---------------------------------------------------------------------------
const EVENT_HINTS = {
  deposit_confirmed: 'A deposit landed — a warm thank-you moment.',
  deposit_failed: 'A payment attempt failed — a reassuring note.',
  withdrawal_settled: 'A payout arrived — congratulate.',
  level_up: 'New loyalty level — celebrate.',
  class_up: 'New loyalty class — celebrate.',
  kyc_approved: 'Verification passed — a nice milestone.',
  bonus_completed: 'Bonus wagered through — congratulate.',
  bonus_expired: 'A bonus expired unused — a gentle heads-up.',
};

const TriggersTab = ({ status, canWrite, onSaved }) => {
  const notify = useNotify();
  const [selected, setSelected] = useState(null); // Set of enabled event names
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (status) setSelected(new Set(status.decision_events || []));
  }, [status]);

  if (!status || selected === null) return <Typography>{t('Loading…')}</Typography>;

  const candidates = (status.canonical_events || []).filter(
    (n) => n !== 'bet_settled'
  );
  const defaults = new Set(status.decision_events_default || []);

  const toggle = (name, on) => {
    const next = new Set(selected);
    if (on) next.add(name);
    else next.delete(name);
    setSelected(next);
  };

  const save = async (value) => {
    setSaving(true);
    try {
      // Merge into the product's STORED retention overrides — a group is
      // saved whole, so the other knobs must round-trip unchanged.
      const { json } = await httpClient(withProduct(`${API_URL}/admin/settings`));
      const overrides = (json.overrides && json.overrides.retention) || {};
      const body = { ...overrides };
      if (value === null) delete body.v2_decision_events;
      else body.v2_decision_events = value;
      await httpClient(withProduct(`${API_URL}/admin/settings/retention`), {
        method: 'PUT',
        body: JSON.stringify({ value: body }),
      });
      notify(t('Triggers saved'), { type: 'success' });
      onSaved?.();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardContent>
        <Alert severity="info" sx={{ mb: 2 }}>
          {t(
            'Events with the switch ON wake the agent: each one goes through the guards and gets a row in the Decisions ledger (message / photo / silence). Events with the switch OFF are "state food": they still update the player state (activity timestamps, loss window) but never reach a decision — that is why they do not appear in Decisions.'
          )}
        </Alert>
        <Alert severity="warning" sx={{ mb: 2 }}>
          {t(
            'bet_settled is special and has no switch: it wakes the agent ONLY when the 24h net loss crosses the high-loss threshold (Settings → Retention bot → Send-frequency guards) — the comfort reaction.'
          )}
        </Alert>
        <Stack spacing={0.5}>
          {candidates.map((name) => (
            <Stack key={name} direction="row" alignItems="center" spacing={1}>
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={selected.has(name)}
                    disabled={!canWrite}
                    onChange={(e) => toggle(name, e.target.checked)}
                  />
                }
                label={<code>{name}</code>}
                sx={{ minWidth: 260, mr: 0 }}
              />
              <Typography variant="caption" color="text.secondary">
                {t(EVENT_HINTS[name] || 'State food by default — switch on to react to it.')}
                {defaults.has(name) ? '' : ` (${t('off by default')})`}
              </Typography>
            </Stack>
          ))}
        </Stack>
        {canWrite && (
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <Button
              variant="contained"
              disabled={saving}
              onClick={() => save([...selected].sort())}
            >
              {saving ? t('Saving…') : t('Save triggers')}
            </Button>
            <Button disabled={saving} onClick={() => save(null)}>
              {t('Reset to defaults')}
            </Button>
          </Stack>
        )}
      </CardContent>
    </Card>
  );
};

const RetentionAgentInner = () => {
  const notify = useNotify();
  const { permissions } = usePermissions();
  const canWrite = permissions === 'admin';
  const location = useLocation();
  const navigate = useNavigate();
  const tab = new URLSearchParams(location.search).get('tab') || 'events';

  const [status, setStatus] = useState(null);
  const [events, setEvents] = useState(null);
  const [decisions, setDecisions] = useState(null);
  const [logs, setLogs] = useState(null);
  const [running, setRunning] = useState(false);

  const load = useCallback(() => {
    httpClient(withProduct(`${API_URL}/admin/retention/v2/status`))
      .then(({ json }) => setStatus(json))
      .catch((e) => notify(e.message || t('Status load failed'), { type: 'error' }));
    httpClient(withProduct(`${API_URL}/admin/retention/v2/events`))
      .then(({ json }) => setEvents(json))
      .catch(() => {});
    httpClient(withProduct(`${API_URL}/admin/retention/v2/decisions`))
      .then(({ json }) => setDecisions(json))
      .catch(() => {});
    httpClient(withProduct(`${API_URL}/admin/retention/v2/logs`))
      .then(({ json }) => setLogs(json))
      .catch(() => {});
  }, [notify]);

  useEffect(() => {
    load();
  }, [load]);

  const runNow = async () => {
    setRunning(true);
    try {
      const { json } = await httpClient(withProduct(`${API_URL}/admin/retention/v2/run`), {
        method: 'POST',
        body: JSON.stringify({}),
      });
      const s = json.stats || {};
      notify(
        `${t('Processed')} ${s.events ?? 0} ${t('events')}, ${s.decided ?? 0} ${t('decisions')}, ${s.sent ?? 0} ${t('sent')}`,
        { type: 'success' },
      );
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Run failed'), { type: 'error' });
    } finally {
      setRunning(false);
    }
  };

  const del = async (url, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    try {
      await httpClient(withProduct(url), { method: 'DELETE' });
      notify(t('Deleted'), { type: 'success' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Delete failed'), { type: 'error' });
    }
  };

  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('Proactive agent')} />
      {status && !status.v2_enabled && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {t('The agent is OFF for this product — no proactive messages are sent (the dialogue bot still answers players who write). Enable it in Settings → Retention bot → «Proactive agent» (dry-run stays on until you turn it off, so enabling is safe).')}
        </Alert>
      )}
      <StatusHeader
        status={status}
        onRefresh={load}
        onRun={runNow}
        canWrite={canWrite}
        running={running}
      />
      {(tab === 'events' || tab === 'decisions') && (
        <Simulator status={status} onDone={load} canWrite={canWrite} />
      )}
      <Tabs
        value={tab}
        onChange={(_, v) => navigate(`/retention-agent?tab=${v}`)}
        variant="scrollable"
        allowScrollButtonsMobile
        sx={{ mb: 2 }}
      >
        <Tab value="events" label={`${t('Events')}${events ? ` (${events.total})` : ''}`} />
        <Tab value="decisions" label={`${t('Decisions')}${decisions ? ` (${decisions.total})` : ''}`} />
        <Tab value="triggers" label={t('Triggers')} />
        <Tab value="logs" label={`${t('System log')}${logs ? ` (${logs.total})` : ''}`} />
        <Tab value="guide" label={t('How it works & testing')} />
      </Tabs>
      {tab === 'triggers' && (
        <TriggersTab status={status} canWrite={canWrite} onSaved={load} />
      )}
      {tab === 'events' && (
        <EventsTab
          events={events}
          canWrite={canWrite}
          onDelete={(id) => del(`${API_URL}/admin/retention/v2/events/${id}`)}
          onClear={() =>
            del(
              `${API_URL}/admin/retention/v2/events`,
              t('Delete ALL events for this product? The loss window and recent-activity state derived from them resets too.'),
            )
          }
        />
      )}
      {tab === 'decisions' && (
        <DecisionsTab
          decisions={decisions}
          canWrite={canWrite}
          onDelete={(id) => del(`${API_URL}/admin/retention/v2/decisions/${id}`)}
          onClear={() =>
            del(
              `${API_URL}/admin/retention/v2/decisions`,
              t("Delete ALL decisions for this product? Today's budget counter and all same-event cooldowns reset."),
            )
          }
        />
      )}
      {tab === 'logs' && <LogsTab logs={logs} />}
      {tab === 'guide' && <GuideTab status={status} />}
    </Box>
  );
};

const RetentionAgent = () => (
  <RequireProduct title={t('Proactive agent')}>
    <RetentionAgentInner />
  </RequireProduct>
);

export default RetentionAgent;
