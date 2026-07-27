import { useEffect, useState } from 'react';
import { useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Grid from '@mui/material/Grid';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../../httpClient';
import { FunnelBars, MiniBarChart, SeriesLineChart, TelegramCostCharts } from '../../components/charts';
import { Kpi, RETENTION_FUNNEL_STEPS, RETENTION_TIMESERIES_SERIES } from '../../components/Kpi';
import { t } from '../../i18n';
import { fmtDateTime } from '../../lib/fmt';
import { wideTableSx } from '../../lib/table';
import useIsMobile from '../../lib/useIsMobile';

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

// This page's KPI tiles use a 4-column layout (the dashboard uses 6).
const KpiCard = (props) => <Kpi size={{ xs: 6, sm: 4, md: 3 }} {...props} />;

const TIMESERIES_SERIES = RETENTION_TIMESERIES_SERIES;
const FUNNEL_STEPS = RETENTION_FUNNEL_STEPS;

// --- effectiveness (the attribution ledger) --------------------------------
// Every delivered touch opens an outcome row; a sweep fills in what the player
// did next. These little tables are the four cuts of that one ledger — which
// media, which CTA page, which idle rung and which trigger actually earn a
// reply / a return / a deposit, and what that costs.
const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`);
const money = (v) => (v == null ? '—' : `$${Number(v).toFixed(4)}`);
const latency = (s) => {
  if (s == null) return '—';
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
};

const OutcomeTable = ({ rows, firstLabel, renderFirst, minWidth = 640, empty }) => {
  const isMobile = useIsMobile();
  if (!rows?.length) {
    return (
      <Typography variant="body2" color="text.secondary">
        {empty}
      </Typography>
    );
  }
  if (isMobile) {
    // Six columns whose first one is a caption: squeezed onto a phone the text
    // becomes a one-word-per-line ribbon and the last figures fall off the
    // edge. One block per row instead, numbers on their own line.
    return (
      <Stack spacing={1}>
        {rows.map((r, i) => (
          <Box key={i} sx={{ borderTop: i ? 1 : 0, borderColor: 'divider', pt: i ? 1 : 0 }}>
            <Typography variant="body2" component="div" sx={{ overflowWrap: 'anywhere' }}>
              {renderFirst(r)}
            </Typography>
            <Typography variant="caption" color="text.secondary" component="div">
              {t('Sent')}: {r.sends} · {t('Replied')}: {r.replies} ({pct(r.reply_rate)}) ·{' '}
              {t('Returned')}: {r.returns} · {t('Deposited')}: {r.deposits} · {t('Avg reply')}:{' '}
              {latency(r.avg_reply_latency_sec)}
            </Typography>
          </Box>
        ))}
      </Stack>
    );
  }
  return (
    <Box sx={{ overflowX: 'auto' }}>
      <Table size="small" sx={wideTableSx(minWidth)}>
        <TableHead>
          <TableRow>
            <TableCell>{firstLabel}</TableCell>
            <TableCell align="right">{t('Sent')}</TableCell>
            <TableCell align="right">{t('Replied')}</TableCell>
            <TableCell align="right">{t('Returned')}</TableCell>
            <TableCell align="right">{t('Deposited')}</TableCell>
            <TableCell align="right">{t('Avg reply')}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r, i) => (
            <TableRow key={i}>
              <TableCell>{renderFirst(r)}</TableCell>
              <TableCell align="right">{r.sends}</TableCell>
              <TableCell align="right">
                {r.replies} <Typography component="span" variant="caption" color="text.secondary">({pct(r.reply_rate)})</Typography>
              </TableCell>
              <TableCell align="right">{r.returns}</TableCell>
              <TableCell align="right">{r.deposits}</TableCell>
              <TableCell align="right">{latency(r.avg_reply_latency_sec)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
};

const EffectivenessSection = ({ data }) => {
  const s = data?.summary?.proactive;
  const w = data?.windows;
  return (
    <>
      <Typography variant="h6" sx={{ mb: 0.5 }}>
        {t('Effectiveness (what the touches achieved)')}
      </Typography>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
        {t('Every delivered touch is measured: a reply within {r}h, a return to the casino or a deposit within {c}h.')
          .replace('{r}', w?.reply_hours ?? 48)
          .replace('{c}', w?.conversion_hours ?? 72)}
      </Typography>
      <Grid container spacing={2} sx={{ mb: 2 }}>
        <KpiCard label={t('Proactive touches')} value={s?.sends} hint={t('messages the bot started')} />
        <KpiCard label={t('Answered')} value={s?.replies} hint={pct(s?.reply_rate)} />
        <KpiCard label={t('Came back')} value={s?.returns} hint={pct(s?.return_rate)} />
        <KpiCard label={t('Deposited after')} value={s?.deposits} hint={pct(s?.deposit_rate)} />
        <KpiCard label={t('Cost per reply')} value={money(s?.cost_per_reply_usd)} hint={t('proactive AI spend / replies')} />
        <KpiCard label={t('Cost per return')} value={money(s?.cost_per_return_usd)} />
        <KpiCard label={t('Cost per deposit')} value={money(s?.cost_per_deposit_usd)} />
        <KpiCard
          label={t('Settled')}
          value={s?.settled}
          hint={t('windows elapsed — the rest may still change')}
        />
      </Grid>
      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid size={{ xs: 12, lg: 6 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                {t('Media')}
              </Typography>
              <OutcomeTable
                rows={data?.media}
                firstLabel={t('Photo / video')}
                empty={t('No media delivered in this range yet.')}
                renderFirst={(r) => (
                  <>
                    #{r.photo_id} · {r.media_type}
                    <Typography variant="caption" color="text.secondary" display="block">
                      {(r.description || t('(deleted)')).slice(0, 70)}
                    </Typography>
                  </>
                )}
              />
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, lg: 6 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                {t('Site pages (CTA buttons)')}
              </Typography>
              <OutcomeTable
                rows={data?.links}
                firstLabel={t('Page')}
                minWidth={560}
                empty={t('No CTA buttons attached in this range yet.')}
                renderFirst={(r) => r.link_url}
              />
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, lg: 6 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                {t('Idle ladder rungs')}
              </Typography>
              <OutcomeTable
                rows={data?.idle_rules}
                firstLabel={t('Rule')}
                minWidth={560}
                empty={t('No idle pings fired in this range yet.')}
                renderFirst={(r) => (
                  <>
                    {r.name}
                    <Typography variant="caption" color="text.secondary" display="block">
                      {r.inactivity_days != null ? `${r.inactivity_days}d · ` : ''}
                      {r.trigger_kind || ''} {r.enabled === false ? `· ${t('disabled')}` : ''}
                    </Typography>
                  </>
                )}
              />
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, lg: 6 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                {t('Triggers')}
              </Typography>
              <OutcomeTable
                rows={data?.events}
                firstLabel={t('Trigger')}
                minWidth={560}
                empty={t('No proactive touches in this range yet.')}
                renderFirst={(r) => r.event_name}
              />
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </>
  );
};

const AnalyticsTab = ({ productId }) => {
  const isMobile = useIsMobile();
  const notify = useNotify();
  const [range, setRange] = useState(defaultRange);
  const [overview, setOverview] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [series, setSeries] = useState([]);
  const [users, setUsers] = useState([]);
  const [effect, setEffect] = useState(null);

  useEffect(() => {
    const qs = `product_id=${productId}&from=${range.from}&to=${range.to}`;
    httpClient(`${API_URL}/admin/retention/overview?${qs}`)
      .then(({ json }) => setOverview(json))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
    httpClient(`${API_URL}/admin/retention/funnel?${qs}`)
      .then(({ json }) => setFunnel(json))
      .catch(() => setFunnel(null));
    httpClient(`${API_URL}/admin/retention/timeseries?${qs}`)
      .then(({ json }) => setSeries(json.series || []))
      .catch(() => setSeries([]));
    httpClient(`${API_URL}/admin/retention/effectiveness?${qs}`)
      .then(({ json }) => setEffect(json))
      .catch(() => setEffect(null));
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
      ? t('{pct}% reply rate').replace('{pct}', (inRange.ping_reply_rate * 100).toFixed(1))
      : t('no pings in range');

  return (
    <Box>
      <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'center', mb: 2 }}>
        <TextField
          size="small"
          type="date"
          label={t('From')}
          value={range.from}
          onChange={(e) => e.target.value && setRange({ ...range, from: e.target.value })}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <TextField
          size="small"
          type="date"
          label={t('To')}
          value={range.to}
          onChange={(e) => e.target.value && setRange({ ...range, to: e.target.value })}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <Typography variant="caption" color="text.secondary">
          {t('Both days inclusive. “Player base” below is lifetime; everything else counts this range.')}
        </Typography>
      </Stack>

      <Typography variant="h6" sx={{ mb: 1 }}>
        {t('Player base')}
      </Typography>
      <Grid container spacing={2} sx={{ mb: 2 }}>
        <KpiCard label={t('Linked players')} value={base?.total} hint={t('lifetime deeplink entries')} />
        <KpiCard label={t('Subscribed')} value={base?.subscribed} hint={t('passed the channel gate')} />
        <KpiCard label={t('Pings muted')} value={base?.pings_muted} hint={t('opted out via /stop')} />
        <KpiCard label={t('Unreachable')} value={base?.unreachable} hint={t('blocked the bot / sends fail')} />
      </Grid>

      <Typography variant="h6" sx={{ mb: 1 }}>
        {t('In range')}
      </Typography>
      <Grid container spacing={2} sx={{ mb: 2 }}>
        <KpiCard label={t('Active players')} value={inRange?.active_users} hint={t('wrote in the range')} />
        <KpiCard label={t('New players')} value={inRange?.new_users} hint={t('first deeplink entry')} />
        <KpiCard label={t('Player messages')} value={inRange?.user_messages} />
        <KpiCard label={t('Photos sent')} value={inRange?.photos_sent} />
        <KpiCard
          label={t('Pings sent')}
          value={inRange?.pings_sent}
          hint={inRange?.pings_failed ? `${inRange.pings_failed} ${t('failed')}` : t('proactive nudges')}
        />
        <KpiCard label={t('Ping replies')} value={inRange?.ping_replies} hint={replyRate} />
        <KpiCard label={t('Hand-offs')} value={inRange?.handoffs} hint={t('to manager / site support')} />
        <KpiCard
          label={t('Cost (USD)')}
          value={inRange?.cost_usd != null ? `$${Number(inRange.cost_usd).toFixed(4)}` : undefined}
          hint={t('TG dialog + photo metadata')}
        />
      </Grid>

      <EffectivenessSection data={effect} />

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
            {t('Daily activity')}
          </Typography>
          <SeriesLineChart data={series} series={TIMESERIES_SERIES} />
        </CardContent>
      </Card>

      <Box sx={{ mb: 2 }}>
        <TelegramCostCharts data={series} height={220} />
      </Box>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid size={{ xs: 12, md: 7 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                {t('Entry funnel')}
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
                {t('Stage distribution')}
              </Typography>
              <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
                {t('Players per unlocked photo stage (lifetime).')}
              </Typography>
              <MiniBarChart
                data={overview?.stage_distribution || []}
                xKey="stage"
                yKey="users"
                label={t('Players')}
              />
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Typography variant="h6" sx={{ mb: 1 }}>
        {t('Linked players')} ({users.length})
      </Typography>
      {isMobile ? (
        <Stack spacing={1}>
          {users.map((u, i) => (
            <Card key={u.id ?? i} variant="outlined">
              <CardContent sx={{ py: 1.25, '&:last-child': { pb: 1.25 } }}>
                <Typography variant="subtitle2" sx={{ overflowWrap: 'anywhere' }}>
                  {u.player_id} · {u.tg_username ? `@${u.tg_username}` : u.tg_user_id}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {u.entry_type} · {t('VIP')}: {u.vip_level || '—'} · {t('Stage')}:{' '}
                  {u.unlocked_stage}
                </Typography>
                <Typography variant="caption" color="text.secondary" component="div">
                  {t('Msgs')}: {u.meaningful_msgs} · {t('Photos')}: {u.photos_total} ·{' '}
                  {t('Manager')}: {u.manager_name || '—'}
                  {u.last_active_at ? ` · ${fmtDateTime(u.last_active_at)}` : ''}
                </Typography>
              </CardContent>
            </Card>
          ))}
        </Stack>
      ) : (
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={wideTableSx(760)}>
          <TableHead>
            <TableRow>
              <TableCell>{t('Player')}</TableCell>
              <TableCell>{t('TG user')}</TableCell>
              <TableCell>{t('Entry')}</TableCell>
              <TableCell>{t('VIP')}</TableCell>
              <TableCell align="right">{t('Stage')}</TableCell>
              <TableCell align="right">{t('Msgs')}</TableCell>
              <TableCell align="right">{t('Photos')}</TableCell>
              <TableCell>{t('Manager')}</TableCell>
              <TableCell>{t('Last active')}</TableCell>
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
                    ? fmtDateTime(u.last_active_at)
                    : '—'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      )}
    </Box>
  );
};

// ---------------------------------------------------------------------------
// page shell — needs a concrete product (retention is strictly per-product)

export default AnalyticsTab;
