import { useCallback, useEffect, useState } from 'react';
import { Title, useNotify, usePermissions } from 'react-admin';
import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
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
import { useLocation, useNavigate } from 'react-router-dom';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';
import RequireProduct from '../components/RequireProduct';

/**
 * Retention v2 (agentic, event-driven) — the parallel proactive regime next to
 * the v1 ping matrix. This tab is its home: the status header (switches live
 * in Settings → Retention bot → "Retention v2"), the canonical-event log with
 * a simulator (exercise the pipeline before the casino integration exists),
 * and the agent decision ledger (state snapshot + guard verdict + decision +
 * cost per row — the full audit trail, dry-run rows included).
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

const StatusHeader = ({ status, onRefresh, onRun, canWrite, running }) => {
  if (!status) return null;
  return (
    <Card sx={{ mb: 2 }}>
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <Chip
            label={status.v2_enabled ? 'v2 ENABLED' : 'v2 disabled (v1 ping matrix active)'}
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
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Switches and knobs live in Settings → Retention bot → «Retention v2».
          While v2 is enabled the v1 ping matrix stands down for this product
          (exactly one proactive regime runs). Dry-run ships ON: the agent
          decides and logs to the ledger below without sending — review its
          decisions, then turn dry-run off.
        </Typography>
      </CardContent>
    </Card>
  );
};

const Simulator = ({ status, onDone, canWrite }) => {
  const notify = useNotify();
  const [eventName, setEventName] = useState('deposit_confirmed');
  const [playerId, setPlayerId] = useState('');
  const [payload, setPayload] = useState('{"amount": 100, "currency": "USDT"}');
  const [busy, setBusy] = useState(false);

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
        body: JSON.stringify({ event_name: eventName, player_id: playerId, payload: parsed }),
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
            onChange={(e) => setEventName(e.target.value)}
            sx={{ flex: '0 0 240px' }}
          >
            {(status?.canonical_events || []).map((n) => (
              <MenuItem key={n} value={n}>
                {n}
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
      </CardContent>
    </Card>
  );
};

const EventsTab = ({ events }) => (
  <Card>
    <CardContent>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>When (casino time)</TableCell>
            <TableCell>Event</TableCell>
            <TableCell>Player</TableCell>
            <TableCell>Source</TableCell>
            <TableCell>Payload</TableCell>
            <TableCell>Processed</TableCell>
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
            </TableRow>
          ))}
          {!(events?.items || []).length && (
            <TableRow>
              <TableCell colSpan={6}>
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

const DecisionsTab = ({ decisions }) => (
  <Card>
    <CardContent>
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
          </TableRow>
        </TableHead>
        <TableBody>
          {(decisions?.items || []).map((d) => (
            <TableRow key={d.id}>
              <TableCell>{fmtDT(d.created_at)}</TableCell>
              <TableCell>{d.full_name || d.tg_username || d.player_id || '—'}</TableCell>
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
            </TableRow>
          ))}
          {!(decisions?.items || []).length && (
            <TableRow>
              <TableCell colSpan={9}>
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
      <Simulator status={status} onDone={load} canWrite={canWrite} />
      <Tabs
        value={tab}
        onChange={(_, v) => navigate(`/retention-v2?tab=${v}`)}
        sx={{ mb: 2 }}
      >
        <Tab value="events" label={`Events${events ? ` (${events.total})` : ''}`} />
        <Tab value="decisions" label={`Decisions${decisions ? ` (${decisions.total})` : ''}`} />
      </Tabs>
      {tab === 'events' ? <EventsTab events={events} /> : <DecisionsTab decisions={decisions} />}
    </Box>
  );
};

const RetentionV2 = () => (
  <RequireProduct title="Retention v2 (agent)">
    <RetentionV2Inner />
  </RequireProduct>
);

export default RetentionV2;
