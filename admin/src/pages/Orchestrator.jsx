import { useCallback, useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import { useSearchParams } from 'react-router-dom';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import MenuItem from '@mui/material/MenuItem';
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
import Tooltip from '@mui/material/Tooltip';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import Link from '@mui/material/Link';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';
import RequireProduct from '../components/RequireProduct';
import { t } from '../i18n';
import { notifyError } from '../lib/notifyError';
import { useReadOnly } from '../lib/useReadOnly';

/**
 * Retention Orchestrator — the admin surface of the DOC-1..DOC-7 layer:
 * measurement (holdout/uplift), RG guard, segmentation, frequency, offers,
 * journeys, templates and channels. The on/off knobs live in Retention →
 * Settings (Orchestrator section); this page carries the rich editors,
 * ledgers and diagnostics.
 */

const api = (path) => withProduct(`${API_URL}/admin/retention${path}`);

const useGet = (path, deps = []) => {
  const notify = useNotify();
  const [data, setData] = useState(null);
  const [reloadTick, setReloadTick] = useState(0);
  const reload = useCallback(() => setReloadTick((x) => x + 1), []);
  useEffect(() => {
    let alive = true;
    httpClient(api(path))
      .then(({ json }) => alive && setData(json))
      .catch((e) => notifyError(notify, e, t('Load failed')));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notify, path, reloadTick, ...deps]);
  return [data, reload];
};

const put = async (notify, path, body, okMsg, method = 'PUT') => {
  try {
    const { json } = await httpClient(api(path), {
      method,
      body: JSON.stringify(body),
    });
    notify(okMsg || t('Saved'), { type: 'success' });
    return json;
  } catch (e) {
    notifyError(notify, e, t('Save failed'));
    return null;
  }
};

/** A small (i) icon with an explanation on hover — the page's one tooltip idiom. */
const InfoTip = ({ text }) => (
  <Tooltip title={text} arrow placement="top" enterTouchDelay={0}>
    <InfoOutlinedIcon
      fontSize="inherit"
      sx={{ ml: 0.5, verticalAlign: 'middle', color: 'text.disabled', cursor: 'help' }}
    />
  </Tooltip>
);

const Section = ({ title, children, sub, tip }) => (
  <Card sx={{ mb: 2 }}>
    <CardContent>
      <Typography variant="h6" sx={{ mb: sub ? 0.5 : 1.5 }}>
        {title}
        {tip && <InfoTip text={tip} />}
      </Typography>
      {sub && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          {sub}
        </Typography>
      )}
      {children}
    </CardContent>
  </Card>
);

const Cell = TableCell;

const JsonField = ({ label, value, onChange, rows = 4, disabled }) => {
  const [text, setText] = useState(JSON.stringify(value ?? {}, null, 2));
  const [bad, setBad] = useState(false);
  useEffect(() => setText(JSON.stringify(value ?? {}, null, 2)), [value]);
  return (
    <TextField
      label={label}
      value={text}
      multiline
      minRows={rows}
      fullWidth
      error={bad}
      helperText={bad ? t('Invalid JSON') : undefined}
      disabled={disabled}
      onChange={(e) => {
        setText(e.target.value);
        try {
          onChange(JSON.parse(e.target.value));
          setBad(false);
        } catch {
          setBad(true);
        }
      }}
      sx={{ fontFamily: 'monospace' }}
    />
  );
};

// ---------------------------------------------------------------------------
// Measurement
// ---------------------------------------------------------------------------
const MeasurementTab = () => {
  const notify = useNotify();
  const readOnly = useReadOnly();
  const [cfg, reloadCfg] = useGet('/holdout/config');
  const [status, reloadStatus] = useGet('/holdout/status');
  const [uplift, reloadUplift] = useGet('/uplift');
  const [pct, setPct] = useState('');
  const [salt, setSalt] = useState('');
  const [note, setNote] = useState('');
  useEffect(() => {
    if (cfg) {
      setPct(String(cfg.holdout_pct ?? ''));
      setSalt(cfg.holdout_salt || 'default');
    }
  }, [cfg]);
  return (
    <>
      <Section
        title={t('Holdout control group')}
        sub={t('A deterministic share of players is never touched proactively — their behaviour is the base rate uplift compares against. Rotating the salt starts a NEW experiment (every player re-buckets).')}
      >
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ mb: 2 }}>
          <TextField label={t('Holdout %')} value={pct} type="number"
            inputProps={{ min: 0, max: 50 }} disabled={readOnly}
            onChange={(e) => setPct(e.target.value)} sx={{ width: 140 }} />
          <TextField label={t('Experiment salt')} value={salt} disabled={readOnly}
            onChange={(e) => setSalt(e.target.value)} sx={{ width: 220 }} />
          <TextField label={t('Note')} value={note} disabled={readOnly}
            onChange={(e) => setNote(e.target.value)} fullWidth />
          {!readOnly && (
            <Button variant="contained" onClick={async () => {
              const ok = await put(notify, '/holdout/config',
                { holdout_pct: Number(pct), holdout_salt: salt, note });
              if (ok) { reloadCfg(); reloadStatus(); }
            }}>{t('Save')}</Button>
          )}
        </Stack>
        {status && (
          <Stack direction="row" spacing={1}>
            <Chip label={`${t('treatment')}: ${status.treatment}`} />
            <Chip color="warning" label={`${t('holdout')}: ${status.holdout}`} />
            <Chip variant="outlined" label={`${t('unassigned')}: ${status.unassigned}`} />
          </Stack>
        )}
      </Section>
      <Section title={t('Uplift (last 28 days)')}
        sub={t('Conversion of touched (treatment) vs held-out players, by the group at touch time. Uplift in percentage points is the honest answer to “does retention work”.')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('Conversion')}</Cell>
              <Cell align="right">{t('Treatment players')}</Cell>
              <Cell align="right">{t('Holdout players')}</Cell>
              <Cell align="right">{t('Treatment rate')}</Cell>
              <Cell align="right">{t('Holdout rate')}</Cell>
              <Cell align="right">{t('Uplift (pp)')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(uplift?.windows || []).map((w) => (
              <TableRow key={w.conversion_type}>
                <Cell>
                  {w.conversion_type}
                  {w.low_confidence && (
                    <Chip size="small" label={t('low confidence')} sx={{ ml: 1 }} />
                  )}
                </Cell>
                <Cell align="right">{w.treatment_players}</Cell>
                <Cell align="right">{w.holdout_players}</Cell>
                <Cell align="right">{w.treatment_rate ?? '—'}</Cell>
                <Cell align="right">{w.holdout_rate ?? '—'}</Cell>
                <Cell align="right">
                  {w.uplift_pp ?? (w.reason ? t('holdout disabled') : '—')}
                </Cell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {!readOnly && (
          <Tooltip arrow title={t('Settles open outcome rows immediately (normally a background sweep does this every few minutes) and refreshes the uplift table.')}>
            <Button sx={{ mt: 2 }} onClick={async () => {
              const res = await put(notify, '/attribution/run', {},
                t('Attribution sweep done'), 'POST');
              if (res) reloadUplift();
            }}>{t('Run attribution now')}</Button>
          </Tooltip>
        )}
      </Section>
    </>
  );
};

// ---------------------------------------------------------------------------
// RG
// ---------------------------------------------------------------------------
const RG_SIGNALS = ['chase_pattern', 'deposit_frequency_spike',
  'escalating_bets', 'session_length_spike', 'support_help_request'];

const RgTab = () => {
  const notify = useNotify();
  const readOnly = useReadOnly();
  const [signals, reloadSignals] = useGet('/rg/signals');
  const [audit] = useGet('/rg/audit?page_size=30');
  const [playerId, setPlayerId] = useState('');
  const [playerRg, setPlayerRg] = useState(null);
  const [newStatus, setNewStatus] = useState('self_exclude');
  const byKey = Object.fromEntries(
    (signals?.items || []).map((s) => [s.signal_key, s]));
  return (
    <>
      <Section title={t('Player RG status')}
        sub={t('The casino platform is the source of truth (player-update feed). Manual marking is the bridge while the feed is not wired — global admin only.')}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
          <TextField label={t('Player ID')} value={playerId}
            onChange={(e) => setPlayerId(e.target.value)} sx={{ width: 220 }} />
          <Button onClick={async () => {
            try {
              const { json } = await httpClient(
                api(`/rg/status&player_id=${encodeURIComponent(playerId)}`)
                  .replace('/rg/status&', '/rg/status?'));
              setPlayerRg(json);
            } catch (e) {
              notifyError(notify, e, t('Lookup failed'));
            }
          }}>{t('Look up')}</Button>
          <TextField select label={t('Set status')} value={newStatus}
            onChange={(e) => setNewStatus(e.target.value)} sx={{ width: 180 }}>
            {['ok', 'cool_off', 'rg_hold', 'self_exclude'].map((s) => (
              <MenuItem key={s} value={s}>{s}</MenuItem>
            ))}
          </TextField>
          {!readOnly && (
            <Button color="warning" variant="outlined" onClick={() =>
              put(notify, '/rg/set-status',
                { player_id: playerId, rg_status: newStatus },
                t('RG status set'), 'POST')
            }>{t('Apply')}</Button>
          )}
        </Stack>
        {playerRg && (
          <Box component="pre" sx={{ mt: 2, fontSize: 13 }}>
            {JSON.stringify(playerRg, null, 2)}
          </Box>
        )}
      </Section>
      <Section title={t('Behavioral signals')}
        sub={t('Config-driven and DISABLED by default (MVP). computed = derived from the event feed; casino_flag = accepted pre-computed from the casino risk engine.')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('Signal')}</Cell><Cell>{t('Enabled')}</Cell>
              <Cell>{t('Block class')}</Cell><Cell>{t('Source')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {RG_SIGNALS.map((key) => {
              const row = byKey[key] || { signal_key: key, enabled: false, block_class: 'conditional', source: key === 'chase_pattern' || key === 'deposit_frequency_spike' ? 'computed' : 'casino_flag', params: {} };
              return (
                <TableRow key={key}>
                  <Cell>{key}</Cell>
                  <Cell>
                    <Switch size="small" checked={!!row.enabled} disabled={readOnly}
                      onChange={async (e) => {
                        await put(notify, '/rg/signals',
                          { ...row, enabled: e.target.checked });
                        reloadSignals();
                      }} />
                  </Cell>
                  <Cell>{row.block_class}</Cell>
                  <Cell>{row.source}</Cell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Section>
      <Section title={t('Compliance audit (global admin)')}
        sub={t('Append-only, 5+ years retention, every evaluation including passes.')}>
        {audit?.summary && (
          <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
            <Chip label={`${t('checks')}: ${audit.summary.checks}`} />
            {Object.entries(audit.summary.by_decision || {}).map(([k, v]) => (
              <Chip key={k} variant="outlined" label={`${k}: ${v}`} />
            ))}
          </Stack>
        )}
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('When')}</Cell><Cell>{t('Player')}</Cell>
              <Cell>{t('Trigger')}</Cell><Cell>{t('Decision')}</Cell>
              <Cell>{t('Reason')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(audit?.items || []).map((r) => (
              <TableRow key={r.id}>
                <Cell>{(r.decided_at || '').slice(0, 16)}</Cell>
                <Cell>{r.player_id}</Cell>
                <Cell>{r.trigger}</Cell>
                <Cell>{r.decision}</Cell>
                <Cell>{r.reason}</Cell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
    </>
  );
};

// ---------------------------------------------------------------------------
// Segmentation
// ---------------------------------------------------------------------------
const COHORT_LABEL = {
  by_cohort: 'Dormancy cohort',
  by_value_tier: 'Value tier',
  by_vip_segment: 'VIP segment',
};

const DORMANCY_ROWS = [
  ['d7', 'dormant_d7'], ['d10', 'dormant_d10'], ['d14', 'dormant_d14'],
  ['d21', 'dormant_d21'], ['d30', 'dormant_d30'],
];

const VIP_SEGMENTS = ['mass', 'vip', 'vip_plus'];

const SegmentationTab = () => {
  const notify = useNotify();
  const readOnly = useReadOnly();
  const [dist] = useGet('/scoring/distribution');
  const [cfg, reloadCfg] = useGet('/scoring/config');
  const [transitions] = useGet('/scoring/transitions?page_size=30');
  const [drafts, setDrafts] = useState({});
  const merged = (key) =>
    drafts[key] ?? cfg?.stored?.[key] ?? cfg?.defaults?.[key] ?? {};
  const setDraft = (key, v) => setDrafts((d) => ({ ...d, [key]: v }));
  const saveBtn = (key, label) => !readOnly && (
    <Button size="small" variant="outlined" sx={{ mt: 1 }} onClick={async () => {
      const ok = await put(notify, '/scoring/config',
        { config_key: key, params: merged(key) });
      if (ok) reloadCfg();
    }}>{label}</Button>
  );

  const bounds = merged('dormancy_boundaries');
  const tiers = merged('value_tiers');
  const mapping = merged('vip_mapping');
  const [newClass, setNewClass] = useState('');

  return (
    <>
      <Section title={t('Population')}
        tip={t('A live snapshot of the player base by each scoring dimension. Recomputed by the background scoring sweep (hourly by default) — enable "Scoring" in Retention → Settings → Orchestrator for it to fill.')}>
        {dist && ['by_cohort', 'by_value_tier', 'by_vip_segment'].map((k) => (
          <Stack key={k} direction="row" spacing={1} sx={{ mb: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            <Typography variant="body2" sx={{ width: 160 }}>{t(COHORT_LABEL[k])}</Typography>
            {Object.entries(dist[k] || {}).map(([kk, v]) => (
              <Chip key={kk} size="small" label={`${kk}: ${v}`} />
            ))}
          </Stack>
        ))}
        {!dist && (
          <Typography variant="body2" color="text.secondary">{t('Loading…')}</Typography>
        )}
      </Section>

      <Section title={t('Dormancy cohorts (days of inactivity)')}
        sub={t('How many idle days put a player into each dormancy cohort. Each cohort is a from–to range in days; "lost" starts at its threshold and has no upper bound. Journeys and recovery scenarios target these cohorts, so a boundary change re-aims them too.')}>
        <Table size="small" sx={{ maxWidth: 480 }}>
          <TableHead>
            <TableRow>
              <Cell>{t('Cohort')}</Cell>
              <Cell align="right">{t('From (days)')}</Cell>
              <Cell align="right">{t('To (days)')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {DORMANCY_ROWS.map(([key, label]) => {
              const range = Array.isArray(bounds[key]) ? bounds[key] : ['', ''];
              const setPart = (idx, val) => {
                const next = [...range];
                next[idx] = val === '' ? '' : Number(val);
                setDraft('dormancy_boundaries', { ...bounds, [key]: next });
              };
              return (
                <TableRow key={key}>
                  <Cell>{label}</Cell>
                  {[0, 1].map((idx) => (
                    <Cell key={idx} align="right">
                      <TextField size="small" type="number" value={range[idx] ?? ''}
                        disabled={readOnly} sx={{ width: 90 }}
                        onChange={(e) => setPart(idx, e.target.value)} />
                    </Cell>
                  ))}
                </TableRow>
              );
            })}
            <TableRow>
              <Cell>
                lost
                <InfoTip text={t('Players idle at least this many days count as lost — the deepest cohort, no upper bound.')} />
              </Cell>
              <Cell align="right">
                <TextField size="small" type="number"
                  value={bounds.lost ?? ''} disabled={readOnly} sx={{ width: 90 }}
                  onChange={(e) => setDraft('dormancy_boundaries',
                    { ...bounds, lost: e.target.value === '' ? '' : Number(e.target.value) })} />
              </Cell>
              <Cell align="right">—</Cell>
            </TableRow>
          </TableBody>
        </Table>
        {saveBtn('dormancy_boundaries', t('Save cohorts'))}
      </Section>

      <Section title={t('Value tiers (lifetime deposits, USD)')}
        sub={t('The minimum lifetime-deposit sum that puts a player into each tier. Frequency caps and offer eligibility can differ by tier.')}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
          {['low', 'mid', 'high', 'whale'].map((tier) => (
            <TextField key={tier} size="small" type="number" label={tier}
              value={tiers[tier] ?? ''} disabled={readOnly} sx={{ width: 120 }}
              onChange={(e) => setDraft('value_tiers',
                { ...tiers, [tier]: e.target.value === '' ? '' : Number(e.target.value) })} />
          ))}
        </Stack>
        {saveBtn('value_tiers', t('Save tiers'))}
      </Section>

      <Section title={t('VIP mapping (casino loyalty class → segment)')}
        sub={t('Maps the loyalty class the casino reports (vip_level in the profile) onto the orchestrator segments: mass, vip, vip_plus. VIP segments get relaxed frequency caps, and a high-loss VIP goes to the host queue instead of an auto-bonus. Class names are matched case-insensitively.')}>
        <Table size="small" sx={{ maxWidth: 480 }}>
          <TableHead>
            <TableRow>
              <Cell>{t('Casino class')}</Cell>
              <Cell>{t('Segment')}</Cell>
              <Cell />
            </TableRow>
          </TableHead>
          <TableBody>
            {Object.entries(mapping).map(([cls, seg]) => (
              <TableRow key={cls}>
                <Cell sx={{ fontFamily: 'monospace' }}>{cls}</Cell>
                <Cell>
                  <TextField select size="small" value={seg} disabled={readOnly}
                    sx={{ width: 140 }}
                    onChange={(e) => setDraft('vip_mapping',
                      { ...mapping, [cls]: e.target.value })}>
                    {VIP_SEGMENTS.map((s) => (
                      <MenuItem key={s} value={s}>{s}</MenuItem>))}
                  </TextField>
                </Cell>
                <Cell>
                  {!readOnly && (
                    <Button size="small" color="error" onClick={() => {
                      const next = { ...mapping };
                      delete next[cls];
                      setDraft('vip_mapping', next);
                    }}>{t('Remove')}</Button>
                  )}
                </Cell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {!readOnly && (
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            <TextField size="small" label={t('Add class')} value={newClass}
              onChange={(e) => setNewClass(e.target.value)} sx={{ width: 180 }} />
            <Button size="small" disabled={!newClass.trim()} onClick={() => {
              setDraft('vip_mapping', { ...mapping, [newClass.trim().toLowerCase()]: 'mass' });
              setNewClass('');
            }}>{t('Add')}</Button>
          </Stack>
        )}
        {saveBtn('vip_mapping', t('Save mapping'))}
      </Section>
      <Section title={t('Recent cohort transitions')}
        tip={t('One row whenever a player crosses a dormancy boundary (at most one per player per cohort per day) — the raw material of "who just went quiet".')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('When')}</Cell><Cell>{t('Player')}</Cell>
              <Cell>{t('From')}</Cell><Cell>{t('To')}</Cell>
              <Cell align="right">{t('Idle days')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(transitions?.items || []).map((r) => (
              <TableRow key={r.id}>
                <Cell>{(r.transitioned_at || '').slice(0, 16)}</Cell>
                <Cell>{r.player_id}</Cell>
                <Cell>{r.from_cohort}</Cell>
                <Cell>{r.to_cohort}</Cell>
                <Cell align="right">{r.days_inactive}</Cell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
    </>
  );
};

// ---------------------------------------------------------------------------
// Frequency
// ---------------------------------------------------------------------------
const FrequencyTab = () => {
  const notify = useNotify();
  const readOnly = useReadOnly();
  const [caps, reloadCaps] = useGet('/frequency/caps');
  const [prios, reloadPrios] = useGet('/frequency/priorities');
  const [capDraft, setCapDraft] = useState({ channel: 'telegram', cohort: 'mass', per_day: 3, per_week: 10, burst_per_hour: 1, enabled: true });
  const [prioDraft, setPrioDraft] = useState({ touch_type: 'event_reaction', priority: 3, channel_switch_on_cap: false });
  return (
    <>
      <Section title={t('Cap matrix (channel × cohort)')}
        sub={t('Stored rows override the built-in defaults shown greyed. Email rides its OWN row — it never consumes the intrusive-touch budget (intrusive = push + Telegram). P1/P2 touches are never cut by a cap.')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('Channel')}</Cell><Cell>{t('Cohort')}</Cell>
              <Cell align="right">{t('Per day')}</Cell>
              <Cell align="right">{t('Per week')}</Cell>
              <Cell align="right">{t('Burst / hour')}</Cell>
              <Cell>{t('Source')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(caps?.items || []).map((r) => (
              <TableRow key={`s-${r.channel}-${r.cohort}`}>
                <Cell>{r.channel}</Cell><Cell>{r.cohort}</Cell>
                <Cell align="right">{r.per_day}</Cell>
                <Cell align="right">{r.per_week}</Cell>
                <Cell align="right">{r.burst_per_hour}</Cell>
                <Cell><Chip size="small" label={t('stored')} /></Cell>
              </TableRow>
            ))}
            {(caps?.defaults || [])
              .filter((d) => !(caps?.items || []).some(
                (r) => r.channel === d.channel && r.cohort === d.cohort))
              .map((r) => (
                <TableRow key={`d-${r.channel}-${r.cohort}`} sx={{ opacity: 0.55 }}>
                  <Cell>{r.channel}</Cell><Cell>{r.cohort}</Cell>
                  <Cell align="right">{r.per_day}</Cell>
                  <Cell align="right">{r.per_week}</Cell>
                  <Cell align="right">{r.burst_per_hour}</Cell>
                  <Cell><Chip size="small" variant="outlined" label={t('default')} /></Cell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
        {!readOnly && (
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} sx={{ mt: 2 }}>
            <TextField select size="small" label={t('Channel')} value={capDraft.channel}
              onChange={(e) => setCapDraft({ ...capDraft, channel: e.target.value })}
              sx={{ width: 130 }}>
              {['telegram', 'push', 'email', 'in_app'].map((c) => (
                <MenuItem key={c} value={c}>{c}</MenuItem>))}
            </TextField>
            <TextField size="small" label={t('Cohort')} value={capDraft.cohort}
              onChange={(e) => setCapDraft({ ...capDraft, cohort: e.target.value })}
              sx={{ width: 120 }} />
            {['per_day', 'per_week', 'burst_per_hour'].map((f) => (
              <TextField key={f} size="small" type="number" label={f}
                value={capDraft[f]} sx={{ width: 110 }}
                onChange={(e) => setCapDraft({ ...capDraft, [f]: Number(e.target.value) })} />
            ))}
            <Button variant="outlined" onClick={async () => {
              const ok = await put(notify, '/frequency/caps', capDraft);
              if (ok) reloadCaps();
            }}>{t('Save row')}</Button>
          </Stack>
        )}
      </Section>
      <Section title={t('Touch priorities (P1 critical … P5 discovery)')}
        tip={t('P1 = service-critical (always goes out), P2 = money moments, P3 = event reactions, P4 = recovery, P5 = discovery. When a cap is hit, lower-priority touches are cut first; P1/P2 are never cut.')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('Touch type')}</Cell>
              <Cell align="right">{t('Priority')}</Cell>
              <Cell>{t('Switch channel on cap')}</Cell>
              <Cell>{t('Source')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(prios?.items || []).map((r) => (
              <TableRow key={`s-${r.touch_type}`}>
                <Cell>{r.touch_type}</Cell>
                <Cell align="right">P{r.priority}</Cell>
                <Cell>{r.channel_switch_on_cap ? t('yes') : t('no')}</Cell>
                <Cell><Chip size="small" label={t('stored')} /></Cell>
              </TableRow>
            ))}
            {Object.entries(prios?.defaults || {})
              .filter(([k]) => !(prios?.items || []).some((r) => r.touch_type === k))
              .map(([k, v]) => (
                <TableRow key={`d-${k}`} sx={{ opacity: 0.55 }}>
                  <Cell>{k}</Cell>
                  <Cell align="right">P{v}</Cell>
                  <Cell>{t('no')}</Cell>
                  <Cell><Chip size="small" variant="outlined" label={t('default')} /></Cell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
        {!readOnly && (
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <TextField size="small" label={t('Touch type')} value={prioDraft.touch_type}
              onChange={(e) => setPrioDraft({ ...prioDraft, touch_type: e.target.value })} />
            <TextField size="small" type="number" label={t('Priority')}
              value={prioDraft.priority} sx={{ width: 100 }}
              onChange={(e) => setPrioDraft({ ...prioDraft, priority: Number(e.target.value) })} />
            <Button variant="outlined" onClick={async () => {
              const ok = await put(notify, '/frequency/priorities', prioDraft);
              if (ok) reloadPrios();
            }}>{t('Save')}</Button>
          </Stack>
        )}
      </Section>
    </>
  );
};

// ---------------------------------------------------------------------------
// Offers
// ---------------------------------------------------------------------------
const OffersTab = () => {
  const notify = useNotify();
  const readOnly = useReadOnly();
  const [catalog, reloadCatalog] = useGet('/offers/catalog');
  const [triggers, reloadTriggers] = useGet('/offers/triggers');
  const [grants] = useGet('/offers/grants?page_size=30');
  const [tasks, reloadTasks] = useGet('/host-tasks');
  const [draft, setDraft] = useState({ offer_key: '', offer_type: 'bonus', partner_bonus_id: '', cost_estimate_usd: 0, description: '', enabled: false });
  const [trigDraft, setTrigDraft] = useState({ trigger_key: 'loss_high', offer_key: '', vip_suppress: true, enabled: false });
  return (
    <>
      {grants?.budget && (
        <Alert severity={grants.budget.daily_budget_usd > 0 ? 'info' : 'warning'} sx={{ mb: 2 }}>
          {t('Stimulus budget today')}: ${grants.budget.spent_today} / $
          {grants.budget.daily_budget_usd}
          {grants.budget.daily_budget_usd === 0 && ` — ${t('granting blocked (zero budget)')}`}
        </Alert>
      )}
      <Section title={t('Offer catalog (bonus-CMS IDs)')}
        sub={t('The casino’s Bonus Engine owns the mechanics; a catalog row references the bonus by its CMS ID and carries the cost estimate for the budget guard. Granting = calling the casino: “credit bonus <ID> to player <X>”.')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('Key')}</Cell><Cell>{t('Type')}</Cell>
              <Cell>{t('Bonus CMS ID')}</Cell>
              <Cell align="right">{t('Cost est. $')}</Cell>
              <Cell>{t('Enabled')}</Cell><Cell>{t('Description')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(catalog?.items || []).map((r) => (
              <TableRow key={r.offer_key}>
                <Cell>{r.offer_key}</Cell><Cell>{r.offer_type}</Cell>
                <Cell>{r.partner_bonus_id || '—'}</Cell>
                <Cell align="right">{r.cost_estimate_usd}</Cell>
                <Cell>
                  <Switch size="small" checked={!!r.enabled} disabled={readOnly}
                    onChange={async (e) => {
                      await put(notify, '/offers/catalog',
                        { ...r, enabled: e.target.checked });
                      reloadCatalog();
                    }} />
                </Cell>
                <Cell>{r.description}</Cell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {!readOnly && (
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} sx={{ mt: 2 }}>
            <TextField size="small" label={t('Key')} value={draft.offer_key}
              onChange={(e) => setDraft({ ...draft, offer_key: e.target.value })} />
            <TextField size="small" label={t('Bonus CMS ID')} value={draft.partner_bonus_id}
              onChange={(e) => setDraft({ ...draft, partner_bonus_id: e.target.value })} />
            <TextField size="small" type="number" label={t('Cost est. $')}
              value={draft.cost_estimate_usd} sx={{ width: 110 }}
              onChange={(e) => setDraft({ ...draft, cost_estimate_usd: Number(e.target.value) })} />
            <TextField size="small" label={t('Description (English)')} fullWidth
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            <Button variant="outlined" onClick={async () => {
              const ok = await put(notify, '/offers/catalog', draft);
              if (ok) reloadCatalog();
            }}>{t('Save offer')}</Button>
          </Stack>
        )}
      </Section>
      <Section title={t('Direct triggers (loss tiers)')}
        sub={t('The MVP event path: loss_mid / loss_high map straight to an offer. Recovery-by-cohort granting lives in Journeys — keep a cohort’s direct trigger OFF when its journey is active. VIP suppress: a high-loss VIP is routed to the host queue instead of an auto-bonus.')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('Trigger')}</Cell><Cell>{t('Offer key')}</Cell>
              <Cell>{t('VIP suppress')}</Cell><Cell>{t('Enabled')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(triggers?.items || []).map((r) => (
              <TableRow key={r.trigger_key}>
                <Cell>{r.trigger_key}</Cell><Cell>{r.offer_key}</Cell>
                <Cell>{r.vip_suppress ? t('yes') : t('no')}</Cell>
                <Cell>
                  <Switch size="small" checked={!!r.enabled} disabled={readOnly}
                    onChange={async (e) => {
                      await put(notify, '/offers/triggers',
                        { ...r, enabled: e.target.checked });
                      reloadTriggers();
                    }} />
                </Cell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {!readOnly && (
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <TextField select size="small" label={t('Trigger')} value={trigDraft.trigger_key}
              sx={{ width: 140 }}
              onChange={(e) => setTrigDraft({ ...trigDraft, trigger_key: e.target.value })}>
              {['loss_mid', 'loss_high', 'idle_d10', 'idle_d14', 'ftd_d1'].map((k) => (
                <MenuItem key={k} value={k}>{k}</MenuItem>))}
            </TextField>
            <TextField size="small" label={t('Offer key')} value={trigDraft.offer_key}
              onChange={(e) => setTrigDraft({ ...trigDraft, offer_key: e.target.value })} />
            <Button variant="outlined" onClick={async () => {
              const ok = await put(notify, '/offers/triggers', trigDraft);
              if (ok) reloadTriggers();
            }}>{t('Save trigger')}</Button>
          </Stack>
        )}
      </Section>
      <Section title={t('Grant ledger')}
        tip={t('Every offer decision that reached the casino: granted / fraud_hold / failed, the casino\'s reference and the cost. The persona mentions a bonus only after a granted row.')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('When')}</Cell><Cell>{t('Player')}</Cell>
              <Cell>{t('Offer')}</Cell><Cell>{t('Status')}</Cell>
              <Cell>{t('Partner ref')}</Cell><Cell align="right">$</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(grants?.items || []).map((r) => (
              <TableRow key={r.id}>
                <Cell>{(r.created_at || '').slice(0, 16)}</Cell>
                <Cell>{r.player_id}</Cell>
                <Cell>{r.offer_key}</Cell>
                <Cell><Chip size="small" label={r.status}
                  color={r.status === 'granted' ? 'success'
                    : r.status === 'fraud_hold' ? 'warning'
                      : r.status === 'failed' ? 'error' : 'default'} /></Cell>
                <Cell>{r.partner_ref || '—'}</Cell>
                <Cell align="right">{r.cost_usd}</Cell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
      <Section title={t('VIP host queue')}
        sub={t('Human route: high-loss VIPs and vip_host journey steps land here — a manager reaches out personally, the bot never writes on this route.')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('When')}</Cell><Cell>{t('Player')}</Cell>
              <Cell>{t('Reason')}</Cell><Cell>{t('Status')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(tasks?.items || []).map((r) => (
              <TableRow key={r.id}>
                <Cell>{(r.created_at || '').slice(0, 16)}</Cell>
                <Cell>{r.player_id}</Cell>
                <Cell>{r.reason}</Cell>
                <Cell>
                  <TextField select size="small" value={r.status} disabled={readOnly}
                    onChange={async (e) => {
                      await put(notify, `/host-tasks/${r.id}`,
                        { status: e.target.value });
                      reloadTasks();
                    }}>
                    {['open', 'claimed', 'done'].map((s) => (
                      <MenuItem key={s} value={s}>{s}</MenuItem>))}
                  </TextField>
                </Cell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
    </>
  );
};

// ---------------------------------------------------------------------------
// Journeys
// ---------------------------------------------------------------------------
const JourneysTab = () => {
  const notify = useNotify();
  const readOnly = useReadOnly();
  const [data, reload] = useGet('/journeys');
  const [warnings, setWarnings] = useState(null);
  const [editing, setEditing] = useState(null);
  return (
    <>
      {warnings && warnings.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }} onClose={() => setWarnings(null)}>
          {t('This journey overlaps enabled idle-ladder rungs — disable them on the Idle pings tab to avoid double touches')}:{' '}
          {warnings.map((w) => `${w.name} (${w.inactivity_days}d)`).join(', ')}
        </Alert>
      )}
      <Section title={t('Journeys')}
        sub={t('Declarative multi-step trajectories. Everything seeds as draft + dry-run; activate one by one after reviewing. Every step passes the full guard chain (RG, holdout, frequency, comfort).')}>
        {!readOnly && (
          <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
            <Tooltip arrow title={t('Creates the starter journey pack (recovery × 5 cohorts, loss scenarios, FTD onboarding, weekly ritual, cashier abandonment) as draft + dry-run. Idempotent: a journey you already edited is never overwritten.')}>
              <Button variant="outlined" onClick={async () => {
                const res = await put(notify, '/scenarios/seed', {},
                  t('Starter library seeded (draft + dry-run)'), 'POST');
                if (res) reload();
              }}>{t('Seed starter library')}</Button>
            </Tooltip>
            <Tooltip arrow title={t('Runs scheduled matching + the due-step drain immediately instead of waiting for the background sweep.')}>
              <Button onClick={async () => {
                const res = await put(notify, '/journeys/run', {},
                  t('Journey sweep executed'), 'POST');
                if (res) reload();
              }}>{t('Run due steps now')}</Button>
            </Tooltip>
          </Stack>
        )}
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('Key')}</Cell><Cell>{t('Trigger')}</Cell>
              <Cell>{t('Status')}</Cell><Cell>{t('Dry-run')}</Cell>
              <Cell>{t('Enrollments')}</Cell><Cell />
            </TableRow>
          </TableHead>
          <TableBody>
            {(data?.items || []).map((j) => (
              <TableRow key={`${j.journey_key}-${j.version}`}>
                <Cell>{j.journey_key}{j.is_starter && (
                  <Chip size="small" label="starter" sx={{ ml: 1 }} />)}</Cell>
                <Cell>{j.trigger?.type === 'event'
                  ? `event: ${j.trigger.event_name}`
                  : `scheduled: ${JSON.stringify(j.trigger?.match || {})}`}</Cell>
                <Cell>
                  <TextField select size="small" value={j.status} disabled={readOnly}
                    onChange={async (e) => {
                      const res = await put(notify,
                        `/journeys/${j.journey_key}/status`,
                        { status: e.target.value });
                      if (res) {
                        setWarnings(res.ladder_overlap_warning || []);
                        reload();
                      }
                    }}>
                    {['draft', 'active', 'paused'].map((s) => (
                      <MenuItem key={s} value={s}>{s}</MenuItem>))}
                  </TextField>
                </Cell>
                <Cell>
                  <Switch size="small" checked={!!j.dry_run} disabled={readOnly}
                    onChange={async (e) => {
                      await put(notify, `/journeys/${j.journey_key}/status`,
                        { status: j.status, dry_run: e.target.checked });
                      reload();
                    }} />
                </Cell>
                <Cell>{Object.entries(j.enrollments || {})
                  .map(([k, v]) => `${k}: ${v}`).join(', ') || '—'}</Cell>
                <Cell>
                  <Button size="small" onClick={() =>
                    setEditing(editing?.journey_key === j.journey_key ? null : j)
                  }>{t('Definition')}</Button>
                </Cell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {editing && (
          <Box sx={{ mt: 2 }}>
            <JsonField label={`${editing.journey_key} (definition)`}
              value={{
                journey_key: editing.journey_key, name: editing.name,
                version: editing.version, status: editing.status,
                trigger: editing.trigger,
                entry_conditions: editing.entry_conditions,
                exit_conditions: editing.exit_conditions,
                steps: editing.steps, dry_run: editing.dry_run,
                priority: editing.priority, metadata: editing.metadata,
              }}
              rows={14} disabled={readOnly}
              onChange={(v) => setEditing({ ...editing, _draft: v })} />
            {!readOnly && (
              <Button sx={{ mt: 1 }} variant="outlined" onClick={async () => {
                const ok = await put(notify, '/journeys',
                  editing._draft || editing);
                if (ok) { setEditing(null); reload(); }
              }}>{t('Save definition')}</Button>
            )}
          </Box>
        )}
      </Section>
    </>
  );
};

// ---------------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------------
const TemplatesTab = () => {
  const notify = useNotify();
  const readOnly = useReadOnly();
  const [data, reload] = useGet('/templates');
  return (
    <Section title={t('Template library')}
      sub={t('persona_brief (default): the template is a managed BRIEF — marketing-ops sets the occasion/intent, the persona writes the final text. verbatim: exact copy (legal wording), bypasses the persona by explicit choice.')}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <Cell>{t('Key')}</Cell><Cell>{t('Mode')}</Cell>
            <Cell>{t('Status')}</Cell><Cell sx={{ width: '55%' }}>{t('Intent (English brief)')}</Cell>
          </TableRow>
        </TableHead>
        <TableBody>
          {(data?.items || []).map((r) => (
            <TemplateRow key={`${r.template_key}-${r.version}`} row={r}
              readOnly={readOnly} notify={notify} reload={reload} />
          ))}
        </TableBody>
      </Table>
    </Section>
  );
};

const TemplateRow = ({ row, readOnly, notify, reload }) => {
  const [intent, setIntent] = useState(row.intent || '');
  useEffect(() => setIntent(row.intent || ''), [row.intent]);
  return (
    <TableRow>
      <Cell>{row.template_key}{row.is_starter && (
        <Chip size="small" label="starter" sx={{ ml: 1 }} />)}</Cell>
      <Cell>{row.mode}</Cell>
      <Cell>{row.status}</Cell>
      <Cell>
        <Stack direction="row" spacing={1}>
          <TextField size="small" fullWidth multiline value={intent}
            disabled={readOnly} onChange={(e) => setIntent(e.target.value)} />
          {!readOnly && intent !== row.intent && (
            <Button size="small" onClick={async () => {
              const ok = await put(notify, '/templates', {
                template_key: row.template_key, type: row.type,
                mode: row.mode, intent, localizations: row.localizations,
                variables: row.variables, status: row.status,
              });
              if (ok) reload();
            }}>{t('Save')}</Button>
          )}
        </Stack>
      </Cell>
    </TableRow>
  );
};

// ---------------------------------------------------------------------------
// Channels
// ---------------------------------------------------------------------------
const CHANNEL_HELP = {
  telegram: 'Own transport (the bot). Always executable.',
  email: 'Customer.io App API. Needs the email API key (product secrets) + region/from in the config.',
  push: 'Delegated: the casino delivers on-device. Needs the delivery endpoint URL + partner key.',
  in_app: 'Delegated, like push.',
  vip_host: 'Human route — a task in the host queue, never a bot message.',
};

const ChannelsTab = () => {
  const notify = useNotify();
  const readOnly = useReadOnly();
  const [data, reload] = useGet('/channels');
  const [deliveries] = useGet('/deliveries?page_size=30');
  const [endpoints, setEndpoints] = useState({ offer_grant_url: '', delivery_endpoint_url: '' });
  useEffect(() => {
    if (data?.endpoints) setEndpoints({
      offer_grant_url: data.endpoints.offer_grant_url || '',
      delivery_endpoint_url: data.endpoints.delivery_endpoint_url || '',
    });
  }, [data]);
  const byChannel = Object.fromEntries(
    (data?.items || []).map((r) => [r.channel, r]));
  return (
    <>
      <Section title={t('Casino endpoints (outbound)')}
        sub={t('OUR calls TO the casino: the bonus-grant endpoint (offer engine) and the delegated push/in-app delivery endpoint. The Bearer secret for them (partner outbound key) and the Customer.io key are set in Structure → product secrets.')}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
          <TextField label={t('Offer-grant URL')} fullWidth disabled={readOnly}
            value={endpoints.offer_grant_url}
            onChange={(e) => setEndpoints({ ...endpoints, offer_grant_url: e.target.value })} />
          <TextField label={t('Delivery endpoint URL')} fullWidth disabled={readOnly}
            value={endpoints.delivery_endpoint_url}
            onChange={(e) => setEndpoints({ ...endpoints, delivery_endpoint_url: e.target.value })} />
          {!readOnly && (
            <Button variant="outlined" onClick={async () => {
              const ok = await put(notify, '/partner-endpoints', endpoints);
              if (ok) reload();
            }}>{t('Save')}</Button>
          )}
        </Stack>
      </Section>
      <Section title={t('Channels')}
        sub={t('Strict opt-in: the router never picks a channel the player has not consented to — no fallback, no exception. No consented channel at all = undeliverable.')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('Channel')}</Cell><Cell>{t('Enabled')}</Cell>
              <Cell align="right">{t('Priority')}</Cell><Cell>{t('Notes')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {['telegram', 'push', 'in_app', 'email', 'vip_host'].map((ch) => {
              const row = byChannel[ch] || { channel: ch, enabled: ch === 'telegram', priority: 100, config: {} };
              return (
                <TableRow key={ch}>
                  <Cell>{ch}</Cell>
                  <Cell>
                    <Switch size="small" checked={!!row.enabled}
                      disabled={readOnly || ch === 'telegram'}
                      onChange={async (e) => {
                        await put(notify, '/channels',
                          { ...row, enabled: e.target.checked });
                        reload();
                      }} />
                  </Cell>
                  <Cell align="right">{row.priority}</Cell>
                  <Cell><Typography variant="caption">{t(CHANNEL_HELP[ch])}</Typography></Cell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Section>
      <Section title={t('Delivery monitor')}
        tip={t('The delivery ledger across all channels: status, attempts and the failure reason. Transient failures retry with backoff (1m / 5m / 30m); permanent ones never retry.')}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <Cell>{t('When')}</Cell><Cell>{t('Player')}</Cell>
              <Cell>{t('Channel')}</Cell><Cell>{t('Status')}</Cell>
              <Cell>{t('Attempts')}</Cell><Cell>{t('Failure')}</Cell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(deliveries?.items || []).map((r) => (
              <TableRow key={r.id}>
                <Cell>{(r.created_at || '').slice(0, 16)}</Cell>
                <Cell>{r.player_id}</Cell>
                <Cell>{r.channel}</Cell>
                <Cell><Chip size="small" label={r.status} /></Cell>
                <Cell>{r.attempts}</Cell>
                <Cell>{r.fail_reason || '—'}</Cell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
    </>
  );
};

// ---------------------------------------------------------------------------
// How it works — the plain-language map of the whole orchestrator, tying the
// mechanics of the other tabs into ONE pipeline and linking each step to the
// tab (or page) where it is configured. Mirrors the Retention → How it works
// algorithm map, which covers the underlying bot — this page covers the layer
// on top of it.
// ---------------------------------------------------------------------------
const HOW_STEPS = [
  {
    title: '1. Triggers — what can start a touch',
    text: 'Three sources: a casino event (deposit, loss, level-up — the proactive agent), a rung of the idle-pings ladder (the player went quiet), or a step of a journey (a declarative multi-step scenario). All three produce a CANDIDATE touch — nothing is sent yet.',
    links: [
      ['Journeys', '#/orchestrator?tab=journeys'],
      ['Idle pings', '#/retention-agent?tab=idle'],
      ['Proactive agent', '#/retention-agent'],
    ],
  },
  {
    title: '2. Guard chain — may we contact this player at all?',
    text: 'Every candidate passes the same checks in a FIXED order, and the first failed check stops it: ① RG guard (responsible gaming — self-exclusion beats everything), ② hard denies (/stop, unsubscribed, blocked bot), ③ holdout (the control group never gets proactive touches — that is what makes uplift measurable), ④ frequency (adaptive caps by channel × cohort and priorities P1..P5 — or the legacy static caps when adaptive is off), ⑤ daily AI budget, ⑥ same-event cooldown, ⑦ the post-loss comfort window. Every verdict — pass or block — is written to a ledger with its reason.',
    links: [
      ['RG guard', '#/orchestrator?tab=rg'],
      ['Measurement', '#/orchestrator?tab=measurement'],
      ['Frequency', '#/orchestrator?tab=frequency'],
    ],
  },
  {
    title: '3. Who is the player — scoring',
    text: 'The background sweep keeps per-player dimensions fresh: the dormancy cohort (active … lost), RFM bands, the value tier by lifetime deposits, and the VIP segment mapped from the casino loyalty class. Journeys target cohorts, frequency caps differ by cohort, and offers/host-routing read the VIP segment.',
    links: [['Segmentation', '#/orchestrator?tab=segmentation']],
  },
  {
    title: '4. Offers — a real bonus, resolved BEFORE the model',
    text: 'When a trigger maps to an offer, deterministic code resolves it first: trigger enabled → RG → VIP suppression → eligibility → cooldown/lifetime caps → the stimulus budget. Only a fully approved offer is granted at the casino (by its bonus-CMS ID, idempotently) — and ONLY after the casino confirms does the persona mention the gift. A high-loss VIP goes to the host queue instead of an auto-bonus.',
    links: [['Offers', '#/orchestrator?tab=offers']],
  },
  {
    title: '5. The text — briefs, not canned copy',
    text: 'A journey step references a template. In persona_brief mode (the default) the template is only the INTENT — the persona writes the final text in the player\'s language with all her usual rules; verbatim mode (exact legal copy) exists behind an explicit flag. Event reactions and idle pings are written the same way the bot always wrote them.',
    links: [['Templates', '#/orchestrator?tab=templates']],
  },
  {
    title: '6. The channel — strict opt-in routing',
    text: 'The router picks the delivery channel: telegram (our bot), email (Customer.io), push / in-app (delegated: we send a delivery order to the casino, the casino delivers on-device and reports status back), or vip_host (a human task, never a bot message). A channel without the player\'s consent is NEVER used — not even as a fallback; with multichannel off everything goes to Telegram as before.',
    links: [['Channels', '#/orchestrator?tab=channels']],
  },
  {
    title: '7. Delivery, retries, and the ledgers',
    text: 'Deliveries live in their own ledger with backoff retries (1m / 5m / 30m; permanent failures never retry). The casino confirms delegated deliveries via the delivery-status callback (statuses only move forward). Every touch also opens an outcome row.',
    links: [['Delivery monitor', '#/orchestrator?tab=channels']],
  },
  {
    title: '8. Measurement — did it work?',
    text: 'The outcome sweep settles each touch: did the player reply, come back, deposit — inside fixed windows. Because the holdout group was never touched, comparing its conversion against touched players gives the uplift in percentage points: the honest answer to "does retention pay for itself". The feedback also loops into the agent\'s next decision and the photo/CTA ordering.',
    links: [
      ['Measurement', '#/orchestrator?tab=measurement'],
      ['Analytics', '#/retention?tab=analytics'],
    ],
  },
];

const HowTab = () => (
  <>
    <Section title={t('The orchestrator in one paragraph')}>
      <Typography variant="body2" color="text.secondary">
        {t('The orchestrator is the layer BETWEEN "something happened" and "the player got a message". The bot underneath (dialogue, persona, photos) is unchanged — the orchestrator decides WHETHER, WHEN, over WHICH channel and WITH WHAT stimulus a proactive touch goes out, and then MEASURES what it achieved. Each mechanic ships behind its own switch in Retention → Settings → Orchestrator; with every switch off the behaviour is exactly the pre-orchestrator bot. The full bot pipeline (dialogue turns, the event agent, the idle ladder) is drawn on Retention → How it works — this page describes the layer on top of it.')}
      </Typography>
      <Stack direction="row" spacing={1} sx={{ mt: 1.5, flexWrap: 'wrap' }} useFlexGap>
        <Chip size="small" clickable component={Link} underline="none"
          href="#/retention?tab=algorithm" label={t('Bot algorithm map')} />
        <Chip size="small" clickable component={Link} underline="none"
          href="#/retention-settings" label={t('Orchestrator switches (Settings)')} />
        <Chip size="small" clickable component={Link} underline="none"
          href="#/integration-checklist" label={t('Integration checklist')} />
      </Stack>
    </Section>
    {HOW_STEPS.map((s) => (
      <Card key={s.title} sx={{ mb: 1.5 }}>
        <CardContent sx={{ '&:last-child': { pb: 2 } }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>{t(s.title)}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {t(s.text)}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 1, flexWrap: 'wrap' }} useFlexGap>
            {s.links.map(([label, href]) => (
              <Chip key={href + label} size="small" variant="outlined" clickable
                component={Link} underline="none" href={href} label={t(label)} />
            ))}
          </Stack>
        </CardContent>
      </Card>
    ))}
  </>
);

// ---------------------------------------------------------------------------
const TABS = [
  { key: 'how', label: 'How it works', el: <HowTab /> },
  { key: 'measurement', label: 'Measurement', el: <MeasurementTab /> },
  { key: 'rg', label: 'RG guard', el: <RgTab /> },
  { key: 'segmentation', label: 'Segmentation', el: <SegmentationTab /> },
  { key: 'frequency', label: 'Frequency', el: <FrequencyTab /> },
  { key: 'offers', label: 'Offers', el: <OffersTab /> },
  { key: 'journeys', label: 'Journeys', el: <JourneysTab /> },
  { key: 'templates', label: 'Templates', el: <TemplatesTab /> },
  { key: 'channels', label: 'Channels', el: <ChannelsTab /> },
];

// One-line purpose of each tab, shown right under the tab strip.
const TAB_INTRO = {
  how: 'The whole orchestrator as one pipeline — read this first.',
  measurement: 'The holdout control group and the uplift it makes measurable: does retention actually bring players back?',
  rg: 'Responsible gaming: who must never be touched, and the audit trail proving it.',
  segmentation: 'Who the player is: dormancy cohorts, value tiers and VIP segments the other mechanics target.',
  frequency: 'How often we may write: cap matrix per channel × cohort, touch priorities, smart send time.',
  offers: 'Real bonuses by their bonus-CMS IDs: catalog, triggers, budget, the grant ledger and the VIP host queue.',
  journeys: 'Declarative multi-step scenarios: recovery by cohort, FTD onboarding, weekly rituals, cashier abandonment.',
  templates: 'The scenario library: briefs the persona writes from (or exact copy when required).',
  channels: 'Where a touch is delivered: Telegram, email, delegated push/in-app, VIP host — strict opt-in.',
};

const OrchestratorPage = () => {
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'how';
  const current = TABS.find((x) => x.key === tab) || TABS[0];
  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('Orchestrator')} />
      <Tabs value={current.key} variant="scrollable" scrollButtons="auto"
        onChange={(_e, v) => setParams({ tab: v })} sx={{ mb: 0.5 }}>
        {TABS.map((x) => (
          <Tab key={x.key} value={x.key} label={t(x.label)} />
        ))}
      </Tabs>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {t(TAB_INTRO[current.key])}
      </Typography>
      {current.el}
    </Box>
  );
};

const Orchestrator = () => (
  <RequireProduct>
    <OrchestratorPage />
  </RequireProduct>
);

export default Orchestrator;
