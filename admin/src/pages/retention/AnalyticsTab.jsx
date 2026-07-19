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

const AnalyticsTab = ({ productId }) => {
  const notify = useNotify();
  const [range, setRange] = useState(defaultRange);
  const [overview, setOverview] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [series, setSeries] = useState([]);
  const [users, setUsers] = useState([]);

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
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
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
      <Grid container spacing={2} alignItems="stretch" sx={{ mb: 2 }}>
        <KpiCard label={t('Linked players')} value={base?.total} hint={t('lifetime deeplink entries')} />
        <KpiCard label={t('Subscribed')} value={base?.subscribed} hint={t('passed the channel gate')} />
        <KpiCard label={t('Pings muted')} value={base?.pings_muted} hint={t('opted out via /stop')} />
        <KpiCard label={t('Unreachable')} value={base?.unreachable} hint={t('blocked the bot / sends fail')} />
      </Grid>

      <Typography variant="h6" sx={{ mb: 1 }}>
        {t('In range')}
      </Typography>
      <Grid container spacing={2} alignItems="stretch" sx={{ mb: 2 }}>
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

      <Grid container spacing={2} alignItems="stretch" sx={{ mb: 2 }}>
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
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={{ minWidth: 760 }}>
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
    </Box>
  );
};

// ---------------------------------------------------------------------------
// page shell — needs a concrete product (retention is strictly per-product)

export default AnalyticsTab;
