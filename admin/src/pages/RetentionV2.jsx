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
import RefreshIcon from '@mui/icons-material/Refresh';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import DeleteIcon from '@mui/icons-material/Delete';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import { useLocation, useNavigate } from 'react-router-dom';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';
import RequireProduct from '../components/RequireProduct';

/**
 * Retention v2 (agentic, event-driven) — the parallel proactive regime next to
 * the v1 ping matrix. This tab is its home: the status header (switches live
 * in Settings → Retention bot → "Retention v2"), the canonical-event log with
 * a simulator (exercise the pipeline before the casino integration exists),
 * the agent decision ledger (state snapshot + guard verdict + decision +
 * cost per row — the full audit trail, dry-run rows included), and the
 * "How it works & testing" guide. Events and decisions are deletable (one row
 * or the whole log) so live testing never leaves duplicated rows behind.
 */

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
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <Chip
            label={status.v2_enabled ? 'v2 ENABLED (v1 ping matrix standing down)' : 'v2 disabled (v1 ping matrix active)'}
            color={status.v2_enabled ? 'success' : 'default'}
          />
          <Chip
            label={status.v2_dry_run ? 'DRY-RUN (decides, never sends)' : 'LIVE sending'}
            color={status.v2_dry_run ? 'info' : 'warning'}
          />
          <Chip
            label={`today: ${fmtCost(status.cost_today_usd)} / budget ${
              status.daily_budget_usd ? `$${status.daily_budget_usd}` : 'none'
            }`}
          />
          <Chip label={`queued events: ${status.queued_events}`} />
          <Box sx={{ flex: 1 }} />
          <Button size="small" startIcon={<RefreshIcon />} onClick={onRefresh}>
            Refresh
          </Button>
          <Tooltip title="Drain the event queue through the pipeline now (the worker does the same on its timer).">
            <span>
              <Button
                size="small"
                variant="contained"
                startIcon={<PlayArrowIcon />}
                onClick={onRun}
                disabled={!canWrite || running || !status.v2_enabled}
              >
                {running ? 'Running…' : 'Process queue now'}
              </Button>
            </span>
          </Tooltip>
        </Stack>
        {/* Agent / worker liveness — derived from the durable tables, so it
            answers "is the agent actually running and what did it just do?" */}
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
          <Chip
            size="small"
            variant="outlined"
            color={status.scheduler_enabled ? 'success' : 'error'}
            label={
              status.scheduler_enabled
                ? `worker: running every ${status.sweep_interval_sec}s`
                : 'worker: OFF (RETENTION_SCHEDULER_ENABLED=0 in deploy env — only «Process queue now» works)'
            }
          />
          <Chip size="small" variant="outlined" label={`last event: ${fmtDT(act.last_event_at)}`} />
          <Chip size="small" variant="outlined" label={`last processed: ${fmtDT(act.last_processed_at)}`} />
          <Chip size="small" variant="outlined" label={`last decision: ${fmtDT(act.last_decision_at)}`} />
          <Chip
            size="small"
            variant="outlined"
            label={`today: ${todayMix || 'no decisions'}${
              act.delivered_today ? ` · delivered ${act.delivered_today}` : ''
            }`}
          />
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Switches and knobs live in Settings → Retention bot → «Retention v2».
          While v2 is enabled the v1 ping matrix stands down for this product:
          its Pings rules stop firing (they stay saved) — disable v2 and they
          resume. Dry-run ships ON: the agent decides and logs to the ledger
          below without sending — review its decisions, then turn dry-run off.
          New here? Read the «How it works &amp; testing» tab.
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
        notify('Payload is not valid JSON', { type: 'error' });
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
      notify('Event injected', { type: 'success' });
      onDone();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Simulation failed', { type: 'error' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card sx={{ mb: 2 }}>
      <CardContent>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Event simulator — inject a canonical event as if the casino sent it
        </Typography>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1}>
          <TextField
            select
            label="Event"
            size="small"
            value={eventName}
            onChange={(e) => pickEvent(e.target.value)}
            sx={{ flex: '0 0 240px' }}
          >
            {(status?.canonical_events || []).map((n) => (
              <MenuItem key={n} value={n}>
                {n}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Telegram recipient"
            size="small"
            value={tgUserId}
            onChange={(e) => pickTgUser(e.target.value)}
            sx={{ flex: '0 0 240px' }}
            helperText={
              tgUserId
                ? undefined
                : 'auto = the player’s most recently active link'
            }
          >
            <MenuItem value="">auto (by player id)</MenuItem>
            {linked.map((u) => (
              <MenuItem key={u.tg_user_id} value={String(u.tg_user_id)}>
                {u.tg_username ? `@${u.tg_username}` : u.tg_user_id}
                {u.player_id ? ` · ${u.player_id}` : ''}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="Player id"
            size="small"
            value={playerId}
            onChange={(e) => setPlayerId(e.target.value)}
            sx={{ flex: '0 0 200px' }}
            placeholder="the casino player_id"
          />
          <TextField
            label="Payload (JSON)"
            size="small"
            value={payload}
            onChange={(e) => setPayload(e.target.value)}
            sx={{ flex: 1 }}
          />
          <Button variant="contained" onClick={send} disabled={busy || !canWrite || !playerId.trim()}>
            {busy ? 'Sending…' : 'Inject event'}
          </Button>
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
          <Typography variant="caption" color="text.secondary">
            Sample payloads:
          </Typography>
          {samples.map((s) => (
            <Chip
              key={s.label}
              size="small"
              variant="outlined"
              label={s.label}
              onClick={() => setPayload(JSON.stringify(s.payload))}
            />
          ))}
          <Box sx={{ flex: 1 }} />
          {isLossFeed ? (
            <Chip size="small" color="info" variant="outlined"
              label="state food — wakes the agent only when the 24h net loss crosses the high-loss threshold" />
          ) : (
            <Chip
              size="small"
              color={wakesAgent ? 'success' : 'default'}
              variant="outlined"
              label={wakesAgent ? 'wakes the agent (a decision will be ledgered)' : 'state food only (no decision, feeds player state)'}
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
        Clear all
      </Button>
    </span>
  </Tooltip>
);

const EventsTab = ({ events, canWrite, onDelete, onClear }) => (
  <Card>
    <CardContent>
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
          The event log is also the state resolver’s memory (loss window,
          recent activity) — deleting rows rewrites that derived state. Meant
          for wiping simulator/test rows.
        </Typography>
        <ClearAllButton
          label="Delete ALL of this product's events (decisions stay, minus the event link)."
          onClear={onClear}
          canWrite={canWrite}
        />
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>When (casino time)</TableCell>
            <TableCell>Event</TableCell>
            <TableCell>Player</TableCell>
            <TableCell>Source</TableCell>
            <TableCell>Payload</TableCell>
            <TableCell>Processed</TableCell>
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
              <TableCell>{e.processed_at ? fmtDT(e.processed_at) : 'queued'}</TableCell>
              <TableCell align="right">
                <Tooltip title="Delete this event">
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
                  No events yet. The casino posts them to{' '}
                  <code>POST /partner/{'{product_id}'}/event</code>, or inject one with the
                  simulator above.
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </CardContent>
  </Card>
);

const DecisionsTab = ({ decisions, canWrite, onDelete, onClear }) => (
  <Card>
    <CardContent>
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
          Deleting a decision “refunds” its cost from today’s budget and
          re-arms the same-event cooldown for that event type — so a wiped
          test decision can be re-run immediately.
        </Typography>
        <ClearAllButton
          label="Delete ALL of this product's decisions (resets today's budget counter and all same-event cooldowns)."
          onClear={onClear}
          canWrite={canWrite}
        />
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>When</TableCell>
            <TableCell>Player</TableCell>
            <TableCell>Event</TableCell>
            <TableCell>Decision</TableCell>
            <TableCell>Tone</TableCell>
            <TableCell>Why / brief</TableCell>
            <TableCell>Guards</TableCell>
            <TableCell>Delivered</TableCell>
            <TableCell align="right">Cost</TableCell>
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
                    brief: {d.intent}
                  </Typography>
                )}
              </TableCell>
              <TableCell sx={{ maxWidth: 220 }}>
                {(d.guard?.reasons || []).join(', ') ||
                  (d.guard?.comfort ? 'comfort window' : 'clear')}
              </TableCell>
              <TableCell>{d.delivered ? 'yes' : d.detail || 'no'}</TableCell>
              <TableCell align="right">{fmtCost(d.cost_usd)}</TableCell>
              <TableCell align="right">
                <Tooltip title="Delete this decision">
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
                  No decisions yet — inject an event and press «Process queue now».
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
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
        Every v2 action leaves a durable trace here: agent decisions, simulator
        injections, manual queue runs, deletes. The same facts stream to the
        deploy (Railway) logs as <code>retention_v2_*</code> lines — decisions,
        guard blocks and failed sends included — so this view and the deploy
        logs always tell one story.
      </Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>When</TableCell>
            <TableCell>Type</TableCell>
            <TableCell>Details</TableCell>
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
                  No log entries yet — they appear as soon as the pipeline
                  processes an event (or you inject one).
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </CardContent>
  </Card>
);

// ---------------------------------------------------------------------------
// How it works & testing — the operator's guide to the whole v2 loop
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
  const decisionEvents = status?.decision_events || [];
  const photoEvents = status?.photo_events || [];
  const canonical = status?.canonical_events || [];
  const stateFood = canonical.filter(
    (n) => !decisionEvents.includes(n) && n !== 'bet_settled',
  );
  return (
    <Card>
      <CardContent>
        <Section title="What Retention v2 is">
          <P>
            An event-driven agent that reacts to what just happened at the
            casino — instead of the v1 ping matrix’s fixed “N idle days →
            send” rules. A canonical event (deposit, big loss, level-up, …)
            arrives, a cheap AI call decides whether Nika should say
            something, and if yes the normal retention persona writes the
            message. Very often the correct decision is <b>silence</b> — that
            is by design, and silence is logged too.
          </P>
          <P>The pipeline for every event, in order:</P>
          <Box component="ol" sx={{ pl: 3, my: 0 }}>
            <LI>
              <b>Event arrives</b> — from the casino’s webhook{' '}
              <code>POST /partner/{'{product_id}'}/event</code> or from the
              simulator on this page. Events are idempotent by{' '}
              <code>event_id</code>: a retried webhook is counted, not stored
              twice.
            </LI>
            <LI>
              <b>State resolver (deterministic)</b> — computes the player
              snapshot the agent will see: user status
              (registered/active/at-risk/dormant), risk state, lifecycle
              stage, and the 24h net-loss window summed from{' '}
              <code>bet_settled</code> payloads.
            </LI>
            <LI>
              <b>Guards (deterministic)</b> — decide whether contact is
              allowed at all and which actions are permitted (message / photo
              / silence). The model can never override a guard. See the table
              below.
            </LI>
            <LI>
              <b>Agent decision</b> — one cheap strict-JSON model call. Input:
              the state snapshot, the event, the player’s recent events, the
              tail of their Telegram conversation, and the guard constraints.
              Output: <code>action</code> (silence/message/photo),{' '}
              <code>tone</code> (warm/celebrate/comfort/neutral), and a short{' '}
              <code>intent</code> brief. Anything malformed degrades to
              silence.
            </LI>
            <LI>
              <b>Message generation</b> — the SAME persona stack that answers
              Telegram chats writes the text from the agent’s brief. Nothing
              here is v2-specific: persona, tone of voice, KB, language all
              come from the regular retention configuration (next section).
            </LI>
            <LI>
              <b>Ledger</b> — ONE row per decision, whatever the outcome
              (sent, silence, blocked, dry-run), with the state snapshot,
              guard verdict, the agent’s reasoning and the summed cost. “Why
              did/didn’t the bot write?” is always answerable from the
              Decisions tab.
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title="What flips when you switch v1 ↔ v2">
          <P>
            Exactly ONE proactive regime runs per product. The switch is{' '}
            <b>Settings → Retention bot → «Retention v2 enabled»</b>, applies
            on the next worker sweep (no redeploy) and changes only who is
            allowed to write first:
          </P>
          <Box component="ul" sx={{ pl: 3, my: 0 }}>
            <LI>
              <b>v2 ON</b> — the v1 ping matrix (Retention → Pings) stands
              down: its rules stop firing but stay saved and editable, and the
              send ledger stays readable. The v2 event loop takes over
              proactive contact.
            </LI>
            <LI>
              <b>v2 OFF</b> — the v1 rules resume on the next sweep exactly as
              configured; the v2 loop stops deciding (queued events wait
              unprocessed, the ledger stays readable).
            </LI>
            <LI>
              <b>Never affected by the switch</b>: the dialogue bot itself
              (replies to players who write), the photo machinery inside
              dialogue, escalation hand-offs, and the shared anti-annoyance
              protection — daily cap, min gap, quiet hours, /stop and the
              blocked-bot flag are ONE set of per-player counters used by both
              regimes, so switching never resets a player’s protection.
            </LI>
            <LI>
              <b>Deploy-level master switch</b>:{' '}
              <code>RETENTION_SCHEDULER_ENABLED</code> (Railway env) starts
              both background workers; with it off neither regime sweeps and
              only «Process queue now» moves the v2 queue. The worker chip in
              the header shows this.
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title="Where the voice, persona and content come from">
          <Box component="ul" sx={{ pl: 3, my: 0 }}>
            <LI>
              <b>Persona &amp; tone of voice</b> — Retention → Prompt
              variables (persona name, role, brand, products,{' '}
              <code>retention_tone_of_voice</code>). The v2 agent only writes
              a short brief; the persona prompt writes the actual words. The
              full assembled prompt is visible in Retention → Prompt preview.
            </LI>
            <LI>
              <b>Facts the bot may use</b> — the Retention KB document
              (Retention → KB), same as in dialogue.
            </LI>
            <LI>
              <b>The message header</b> — every proactive message goes out
              under the italic “✨ A little note from {'{persona}'}” line: the{' '}
              <code>rtn_ping_header</code> key in Translations (per language).
            </LI>
            <LI>
              <b>The inline button</b> — when the model attaches a{' '}
              <code>[[LINK:url]]</code> matching the occasion, the validated
              Site map page (Support chat → Site map) rides under the message
              as one button. Comfort mode strips it.
            </LI>
            <LI>
              <b>Photos</b> — the Media library (Retention → Media), same
              stage × VIP gating and daily caps as in dialogue. Only positive
              occasions may carry a photo: <code>{photoEvents.join(', ')}</code>.
            </LI>
            <LI>
              <b>Language</b> — the player’s sticky conversation language
              (the same one their Telegram chat drifted to).
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title="Which events wake the agent">
          <P>
            <b>Decision-worthy</b> (the agent is consulted, a ledger row
            appears):{' '}
            <code>{decisionEvents.join(', ') || '—'}</code>
          </P>
          <P>
            <b>Special:</b> <code>bet_settled</code> wakes the agent only when
            the player’s 24h net loss crosses the high-loss threshold
            (Settings → «v2 high-loss threshold»); below it the event silently
            feeds the loss window.
          </P>
          <P>
            <b>State food only</b> (no decision — they update activity
            timestamps, the loss window and the profile snapshot):{' '}
            <code>{stateFood.join(', ') || '—'}</code>
          </P>
          <P>
            Every stored event also runs the legacy bridge into the v1 fields:{' '}
            <code>deposit_confirmed → last_deposit_at</code>,{' '}
            <code>session_started/ended → last_login_at</code>,{' '}
            <code>bet_settled → last_played_at</code> — so one partner feed
            powers both regimes.
          </P>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title="Guards and the settings behind them">
          <P>
            All knobs live in Settings → Retention bot (the «Retention v2»
            section plus the shared ping-protection knobs). Each blocked
            decision lists its reasons in the Guards column:
          </P>
          <Table size="small" sx={{ maxWidth: 900 }}>
            <TableHead>
              <TableRow>
                <TableCell>Guard reason</TableCell>
                <TableCell>What it means / which setting drives it</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {[
                ['not_subscribed', 'The player has not passed the channel-subscription gate.'],
                ['player_opted_out', 'The player sent /stop (they can /resume).'],
                ['bot_blocked_by_player', 'Telegram returned 403 — the player blocked the bot.'],
                ['min_gap_not_elapsed', '«Ping min gap (hours)» — shared with v1, one counter per player.'],
                ['daily_cap_reached', '«Ping daily cap» — shared with v1.'],
                ['quiet_hours', '«Quiet hours start/end/UTC offset» — no proactive contact at night.'],
                ['daily_budget_reached', '«v2 daily AI budget (USD)» — today’s ledger cost hit the budget.'],
                ['same_event_cooldown', '«v2 same-event cooldown (hours)» — one reaction per event type per player per window. Set 0 while testing to re-run the same event.'],
                ['comfort window', '«v2 loss comfort window» + «v2 high-loss threshold» — after a big loss: empathetic tone only, no photo, no link, no play talk.'],
              ].map(([k, v]) => (
                <TableRow key={k}>
                  <TableCell>
                    <code>{k}</code>
                  </TableCell>
                  <TableCell>{v}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title="How to test, step by step">
          <Box component="ol" sx={{ pl: 3, my: 0 }}>
            <LI>
              Select the product in the header switcher, then in Settings →
              Retention bot → «Retention v2» turn <b>v2 enabled</b> ON and
              leave <b>dry-run</b> ON (safe: nothing is sent).
            </LI>
            <LI>
              Link a test player to the Telegram bot: open the bot through a
              deeplink (easiest: escalate in the support-chat widget, or{' '}
              <code>POST /api/retention/deeplink</code> with a test{' '}
              <code>user_context</code>), press /start and subscribe to the
              channel. The <code>player_id</code> from that handshake is the
              id you feed the simulator.
            </LI>
            <LI>
              In the simulator pick an event (e.g.{' '}
              <code>deposit_confirmed</code>), enter that player id, pick a
              sample payload, «Inject event». If several Telegram accounts are
              linked to the same test player, pick the exact recipient in
              «Telegram recipient» — on «auto» the message goes to the
              player’s most recently active link (the Decisions tab shows the
              actual @username either way).
            </LI>
            <LI>
              Press «Process queue now» and open the Decisions tab: you should
              see the action, tone, the agent’s brief and reasoning, the guard
              verdict and the cost. Try a losing-day scenario: inject a few{' '}
              <code>bet_settled</code> «big loss» samples and watch the
              comfort constraints appear.
            </LI>
            <LI>
              Blocked? The Guards column names the reason. For repeated
              testing: set «v2 same-event cooldown» to 0, raise «Ping daily
              cap», widen quiet hours — or simply delete the previous decision
              row (that re-arms the cooldown and refunds the budget).
            </LI>
            <LI>
              When the decisions look right, turn <b>dry-run OFF</b> and
              re-inject: the message reaches the player in Telegram — italic
              header + persona text (+ button/photo when chosen). It is also
              persisted into the player’s Retention → Conversations
              transcript.
            </LI>
            <LI>
              Clean up after yourself: delete test rows one by one or «Clear
              all» on both tabs. Costs already logged to Analytics stay (they
              were real OpenAI calls).
            </LI>
          </Box>
        </Section>
        <Divider sx={{ mb: 2 }} />

        <Section title="Costs">
          <P>
            Every decision is one cheap model call; a sent message adds one
            generation call. Both land in <code>ai_interaction_logs</code> and
            in the Telegram cost split on Retention → Analytics. The daily
            budget (Settings) is a hard stop: when the day’s summed ledger
            cost reaches it, the agent stays quiet until tomorrow.
          </P>
        </Section>
      </CardContent>
    </Card>
  );
};

const RetentionV2Inner = () => {
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
      .catch((e) => notify(e.message || 'Status load failed', { type: 'error' }));
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
        `Processed ${s.events ?? 0} events, ${s.decided ?? 0} decisions, ${s.sent ?? 0} sent`,
        { type: 'success' },
      );
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Run failed', { type: 'error' });
    } finally {
      setRunning(false);
    }
  };

  const del = async (url, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    try {
      await httpClient(withProduct(url), { method: 'DELETE' });
      notify('Deleted', { type: 'success' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Delete failed', { type: 'error' });
    }
  };

  return (
    <Box sx={{ p: 2 }}>
      <Title title="Retention v2 (agent)" />
      {status && !status.v2_enabled && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Retention v2 is OFF for this product — the v1 ping matrix is running.
          Enable it in Settings → Retention bot → «Retention v2» (dry-run stays
          on until you turn it off, so enabling is safe).
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
        onChange={(_, v) => navigate(`/retention-v2?tab=${v}`)}
        sx={{ mb: 2 }}
      >
        <Tab value="events" label={`Events${events ? ` (${events.total})` : ''}`} />
        <Tab value="decisions" label={`Decisions${decisions ? ` (${decisions.total})` : ''}`} />
        <Tab value="logs" label={`System log${logs ? ` (${logs.total})` : ''}`} />
        <Tab value="guide" label="How it works & testing" />
      </Tabs>
      {tab === 'events' && (
        <EventsTab
          events={events}
          canWrite={canWrite}
          onDelete={(id) => del(`${API_URL}/admin/retention/v2/events/${id}`)}
          onClear={() =>
            del(
              `${API_URL}/admin/retention/v2/events`,
              'Delete ALL events for this product? The loss window and recent-activity state derived from them resets too.',
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
              "Delete ALL decisions for this product? Today's budget counter and all same-event cooldowns reset.",
            )
          }
        />
      )}
      {tab === 'logs' && <LogsTab logs={logs} />}
      {tab === 'guide' && <GuideTab status={status} />}
    </Box>
  );
};

const RetentionV2 = () => (
  <RequireProduct title="Retention v2 (agent)">
    <RetentionV2Inner />
  </RequireProduct>
);

export default RetentionV2;
