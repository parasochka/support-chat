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

const Section = ({ title, children, sub }) => (
  <Card sx={{ mb: 2 }}>
    <CardContent>
      <Typography variant="h6" sx={{ mb: sub ? 0.5 : 1.5 }}>{title}</Typography>
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
          <Button sx={{ mt: 2 }} onClick={async () => {
            const res = await put(notify, '/attribution/run', {},
              t('Attribution sweep done'), 'POST');
            if (res) reloadUplift();
          }}>{t('Run attribution now')}</Button>
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
const SegmentationTab = () => {
  const notify = useNotify();
  const readOnly = useReadOnly();
  const [dist] = useGet('/scoring/distribution');
  const [cfg, reloadCfg] = useGet('/scoring/config');
  const [transitions] = useGet('/scoring/transitions?page_size=30');
  const [drafts, setDrafts] = useState({});
  const merged = (key) =>
    drafts[key] ?? cfg?.stored?.[key] ?? cfg?.defaults?.[key] ?? {};
  return (
    <>
      <Section title={t('Population')}>
        {dist && ['by_cohort', 'by_value_tier', 'by_vip_segment'].map((k) => (
          <Stack key={k} direction="row" spacing={1} sx={{ mb: 1, flexWrap: 'wrap' }}>
            <Typography variant="body2" sx={{ width: 130 }}>{k.replace('by_', '')}</Typography>
            {Object.entries(dist[k] || {}).map(([kk, v]) => (
              <Chip key={kk} size="small" label={`${kk}: ${v}`} />
            ))}
          </Stack>
        ))}
      </Section>
      <Section title={t('Thresholds (config-driven)')}
        sub={t('Dormancy cohort boundaries, value tiers (lifetime deposits, USD) and the casino loyalty-class → VIP-segment mapping. Shared with on-site surfaces so a banner and the bot never disagree about who is dormant.')}>
        <Stack spacing={2}>
          {['dormancy_boundaries', 'value_tiers', 'vip_mapping'].map((key) => (
            <Box key={key}>
              <JsonField label={key} value={merged(key)} disabled={readOnly}
                onChange={(v) => setDrafts((d) => ({ ...d, [key]: v }))} />
              {!readOnly && (
                <Button size="small" sx={{ mt: 0.5 }} onClick={async () => {
                  const ok = await put(notify, '/scoring/config',
                    { config_key: key, params: merged(key) });
                  if (ok) reloadCfg();
                }}>{t('Save')} {key}</Button>
              )}
            </Box>
          ))}
        </Stack>
      </Section>
      <Section title={t('Recent cohort transitions')}>
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
      <Section title={t('Touch priorities (P1 critical … P5 discovery)')}>
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
      <Section title={t('Grant ledger')}>
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
            <Button variant="outlined" onClick={async () => {
              const res = await put(notify, '/scenarios/seed', {},
                t('Starter library seeded (draft + dry-run)'), 'POST');
              if (res) reload();
            }}>{t('Seed starter library')}</Button>
            <Button onClick={async () => {
              const res = await put(notify, '/journeys/run', {},
                t('Journey sweep executed'), 'POST');
              if (res) reload();
            }}>{t('Run due steps now')}</Button>
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
      <Section title={t('Delivery monitor')}>
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
const TABS = [
  { key: 'measurement', label: 'Measurement', el: <MeasurementTab /> },
  { key: 'rg', label: 'RG guard', el: <RgTab /> },
  { key: 'segmentation', label: 'Segmentation', el: <SegmentationTab /> },
  { key: 'frequency', label: 'Frequency', el: <FrequencyTab /> },
  { key: 'offers', label: 'Offers', el: <OffersTab /> },
  { key: 'journeys', label: 'Journeys', el: <JourneysTab /> },
  { key: 'templates', label: 'Templates', el: <TemplatesTab /> },
  { key: 'channels', label: 'Channels', el: <ChannelsTab /> },
];

const OrchestratorPage = () => {
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'measurement';
  const current = TABS.find((x) => x.key === tab) || TABS[0];
  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('Orchestrator')} />
      <Tabs value={current.key} variant="scrollable" scrollButtons="auto"
        onChange={(_e, v) => setParams({ tab: v })} sx={{ mb: 2 }}>
        {TABS.map((x) => (
          <Tab key={x.key} value={x.key} label={t(x.label)} />
        ))}
      </Tabs>
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
