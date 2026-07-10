import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Title } from 'react-admin';
import { useTheme } from '@mui/material/styles';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Grid from '@mui/material/Grid';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import LineChart from '../components/LineChart';
import {
  CHART_COLORS,
  FunnelBars,
  SeriesLineChart,
  TelegramCostCharts,
} from '../components/charts';
import { API_URL, httpClient } from '../httpClient';
import { getProductId, scopeParams } from '../productScope';
import RequireProduct from '../components/RequireProduct';
import { t } from '../i18n';

const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
const usd = (v) => (v == null ? '—' : `$${Number(v).toFixed(4)}`);
const num = (v) => (v == null ? '—' : String(v));
// Latency reads as whole ms under a second, seconds above — the natural unit
// for a reasoning-model turn that often runs several seconds.
const ms = (v) =>
  v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)} ms`;

const CHARTS = [
  ['sessions', t('Sessions over time'), (v) => String(Math.round(v))],
  ['cost', t('Cost over time'), usd],
  ['cost_per_session', t('Avg cost / session'), usd],
  ['escalation_rate', t('Escalation rate'), pct],
];

const Kpi = ({ label, value, hint }) => (
  <Grid size={{ xs: 6, sm: 4, md: 3, lg: 2 }}>
    <Card sx={{ height: '100%' }}>
      <CardContent sx={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 0.5 }}>
        <Typography variant="overline" color="text.secondary" sx={{ lineHeight: 1.4 }}>
          {label}
        </Typography>
        <Typography variant="h5" sx={{ fontWeight: 600 }}>
          {value ?? '—'}
        </Typography>
        {hint && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 'auto' }}>
            {hint}
          </Typography>
        )}
      </CardContent>
    </Card>
  </Grid>
);

const ChartPanel = ({ title, series, color, format }) => {
  const values = (series || []).map((d) => d.value);
  const last = values.length ? values[values.length - 1] : null;
  const avg = values.length
    ? values.reduce((a, b) => a + b, 0) / values.length
    : null;
  return (
    <Grid size={{ xs: 12, md: 6 }}>
      <Card sx={{ height: '100%' }}>
        <CardContent>
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            flexWrap="wrap"
            useFlexGap
            spacing={1}
            sx={{ mb: 1 }}
          >
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              {title}
            </Typography>
            {last != null && (
              <Stack direction="row" spacing={1}>
                <Chip size="small" label={`latest ${format(last)}`} />
                <Chip size="small" variant="outlined" label={`avg ${format(avg)}`} />
              </Stack>
            )}
          </Stack>
          <LineChart series={series} color={color} format={format} />
        </CardContent>
      </Card>
    </Grid>
  );
};

const BreakdownTable = ({ title, rows, nameLabel, nameOf }) => (
  <Grid size={{ xs: 12, md: 6 }}>
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }} gutterBottom>
          {title}
        </Typography>
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{nameLabel}</TableCell>
                <TableCell align="right">{t('Sessions')}</TableCell>
                <TableCell align="right">{t('Escalated')}</TableCell>
                <TableCell align="right">{t('Cost $')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4}>
                    <Typography variant="body2" color="text.secondary">
                      No data for the period.
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => (
                <TableRow key={nameOf(r)}>
                  <TableCell>{nameOf(r)}</TableCell>
                  <TableCell align="right">{r.sessions}</TableCell>
                  <TableCell align="right">{r.escalated}</TableCell>
                  <TableCell align="right">
                    {r.cost_usd_total != null ? Number(r.cost_usd_total).toFixed(4) : '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      </CardContent>
    </Card>
  </Grid>
);

// Section heading between the two dashboard blocks.
const SectionTitle = ({ children }) => (
  <Typography variant="h6" sx={{ fontWeight: 600, mb: 1.5 }}>
    {children}
  </Typography>
);

// Quiet per-block failure note — one block failing must never break the page.
const BlockError = ({ label, error }) => (
  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
    {label} — {t('metrics could not be loaded')} ({error}).
  </Typography>
);

/**
 * Support-chat block: GET /admin/overview KPIs + the four daily time-series
 * charts (/admin/timeseries) + by-topic / by-language breakdowns. Follows the
 * header product scope; the default range is the backend's last 30 days.
 */
const SupportBlock = () => {
  const theme = useTheme();
  const [overview, setOverview] = useState(null);
  const [charts, setCharts] = useState({});
  const [topics, setTopics] = useState([]);
  const [languages, setLanguages] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    const scope = new URLSearchParams(scopeParams()).toString();
    const qs = scope ? `?${scope}` : '';
    const amp = scope ? '&' : '?';
    Promise.all([
      httpClient(`${API_URL}/admin/overview${qs}`),
      httpClient(`${API_URL}/admin/by-topic${qs}`),
      httpClient(`${API_URL}/admin/by-language${qs}`),
    ])
      .then(([o, t, l]) => {
        setOverview(o.json);
        setTopics(t.json.topics || []);
        setLanguages(l.json.languages || []);
      })
      .catch((e) => setError(e.message || 'load failed'));
    CHARTS.forEach(([metric]) => {
      httpClient(`${API_URL}/admin/timeseries${qs}${amp}metric=${metric}&bucket=day`)
        .then(({ json }) =>
          setCharts((prev) => ({ ...prev, [metric]: json.series || [] }))
        )
        .catch(() => setCharts((prev) => ({ ...prev, [metric]: [] })));
    });
  }, []);

  const colors = CHART_COLORS[theme.palette.mode] || CHART_COLORS.dark;

  if (error) return <BlockError label={t('Support chat')} error={error} />;

  return (
    <>
      {/* Row 1 — sessions & engagement (6 tiles). */}
      <Grid container spacing={2} alignItems="stretch">
        <Kpi label={t('Sessions (30d)')} value={num(overview?.sessions_total)} />
        <Kpi
          label={t('Engaged')}
          value={num(overview?.sessions_engaged)}
          hint={t('≥ 1 message')}
        />
        <Kpi
          label={t('Open sessions')}
          value={num(overview?.sessions_open)}
          hint={t('engaged, still open')}
        />
        <Kpi
          label={t('Escalated')}
          value={num(overview?.sessions_escalated)}
          hint={`${t('rate')} ${pct(overview?.escalation_rate)}`}
        />
        <Kpi
          label={t('Resolution rate')}
          value={pct(overview?.resolution_rate)}
          hint={t('proxy: not escalated')}
        />
        <Kpi
          label={t('Avg msgs / session')}
          value={num(overview?.avg_messages_per_session)}
        />
      </Grid>

      {/* Row 2 — AI, cost & performance (6 tiles). The gap-based Grid container
          carries no outer margin, so without an explicit top margin the two
          KPI rows touch. */}
      <Grid container spacing={2} sx={{ mt: 2 }} alignItems="stretch">
        <Kpi
          label={t('Cost (USD)')}
          value={usd(overview?.cost_usd_total)}
          hint={
            overview?.cost_usd_per_session != null
              ? `${usd(overview.cost_usd_per_session)} / ${t('session')}`
              : undefined
          }
        />
        <Kpi
          label={t('Avg response time')}
          value={ms(overview?.avg_latency_ms)}
          hint={t('AI generation, successful calls')}
        />
        <Kpi
          label={t('AI calls')}
          value={num(overview?.ai_calls_total)}
          hint={
            overview?.failed_calls
              ? `${overview.failed_calls} ${t('failed')}`
              : t('OpenAI requests')
          }
        />
        <Kpi
          label={t('Cache hit ratio')}
          value={pct(overview?.cache_hit_ratio)}
          hint={t('prefix-cache economics')}
        />
        <Kpi
          label={t('Key failovers')}
          value={num(overview?.failovers)}
          hint={t('fallback key engaged')}
        />
        <Kpi
          label={t('Blocks')}
          value={
            overview
              ? num((overview.rate_limit_blocks || 0) + (overview.injection_blocks || 0))
              : '—'
          }
          hint={t('rate-limit + injection')}
        />
      </Grid>

      <Grid container spacing={2} sx={{ mt: 2 }} alignItems="stretch">
        {CHARTS.map(([metric, title, format], i) => (
          <ChartPanel
            key={metric}
            title={title}
            series={charts[metric]}
            color={colors[i]}
            format={format}
          />
        ))}
      </Grid>

      <Grid container spacing={2} sx={{ mt: 2 }} alignItems="stretch">
        <BreakdownTable
          title={t('By topic')}
          rows={topics}
          nameLabel={t('Topic')}
          nameOf={(r) => r.topic || r.slug}
        />
        <BreakdownTable
          title={t('By language')}
          rows={languages}
          nameLabel={t('Language')}
          nameOf={(r) => r.lang}
        />
      </Grid>
    </>
  );
};

// Daily retention activity on the dashboard: player messages vs pings sent.
const RETENTION_SERIES = [
  { key: 'messages', label: 'Messages' },
  { key: 'pings', label: 'Pings' },
];

// Entry funnel steps shown beside the activity chart (deeplink → hand-off);
// mirrors the fuller funnel on Retention → Analytics.
const RETENTION_FUNNEL_STEPS = [
  ['deeplinks_created', 'Deeplinks minted'],
  ['starts', '/start redemptions'],
  ['new_users', 'New linked players'],
  ['subscribed', 'Subscribed to channel'],
  ['engaged', 'Engaged (wrote a message)'],
  ['photo_receivers', 'Received a photo'],
  ['handoffs', 'Handed off'],
];

/**
 * Retention · Telegram block: GET /admin/retention/overview + /timeseries in
 * the same header scope as the support block (product / partner / everything
 * the caller may read). The deep KPIs and the funnel live on
 * Retention → Analytics; this block is the at-a-glance summary.
 */
const RetentionBlock = () => {
  const [overview, setOverview] = useState(null);
  const [series, setSeries] = useState([]);
  const [funnel, setFunnel] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const scope = new URLSearchParams(scopeParams()).toString();
    const qs = scope ? `?${scope}` : '';
    httpClient(`${API_URL}/admin/retention/overview${qs}`)
      .then(({ json }) => setOverview(json))
      .catch((e) => setError(e.message || 'load failed'));
    httpClient(`${API_URL}/admin/retention/timeseries${qs}`)
      .then(({ json }) => setSeries(json.series || []))
      .catch(() => setSeries([]));
    httpClient(`${API_URL}/admin/retention/funnel${qs}`)
      .then(({ json }) => setFunnel(json))
      .catch(() => setFunnel(null));
  }, []);

  if (error) return <BlockError label={t('Retention')} error={error} />;

  const base = overview?.users;
  const inRange = overview?.range;

  return (
    <>
      <Grid container spacing={2} alignItems="stretch">
        <Kpi
          label={t('Linked players')}
          value={num(base?.total)}
          hint={
            base?.subscribed != null ? `${base.subscribed} subscribed` : 'lifetime'
          }
        />
        <Kpi label={t('Active (30d)')} value={num(inRange?.active_users)} hint={t('wrote in the bot')} />
        <Kpi
          label={t('Pings sent')}
          value={num(inRange?.pings_sent)}
          hint={`reply rate ${pct(inRange?.ping_reply_rate)}`}
        />
        <Kpi label={t('Photos sent')} value={num(inRange?.photos_sent)} />
        <Kpi label={t('Hand-offs')} value={num(inRange?.handoffs)} hint={t('to manager / support')} />
        <Kpi
          label={t('Cost (USD)')}
          value={usd(inRange?.cost_usd)}
          hint="TG turns + photo metadata"
        />
      </Grid>
      <Grid container spacing={2} sx={{ mt: 2 }} alignItems="stretch">
        <Grid size={{ xs: 12, md: 6 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                Messages & pings over time
              </Typography>
              <SeriesLineChart data={series} series={RETENTION_SERIES} height={190} />
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                Entry funnel
              </Typography>
              <FunnelBars
                steps={RETENTION_FUNNEL_STEPS.map(([key, label]) => ({
                  label,
                  value: funnel ? funnel[key] ?? 0 : null,
                }))}
              />
            </CardContent>
          </Card>
        </Grid>
      </Grid>
      <Box sx={{ mt: 2 }}>
        <TelegramCostCharts data={series} height={190} />
      </Box>
    </>
  );
};

/**
 * The combined dashboard: the support-chat block and the Retention · Telegram
 * block, each resilient on its own. ?module=support / ?module=retention
 * narrows the page to one block (the sidebar Analytics entries deep-link it).
 */
const Dashboard = () => {
  const [params] = useSearchParams();
  const module = params.get('module');
  const showSupport = module !== 'retention';
  const showRetention = module !== 'support';

  // The support-only Analytics view is per-product: without a concrete product
  // selected it would silently resolve to the default product, so gate it the
  // same way the KB / Prompt / Conversations screens are gated. The combined
  // dashboard (no module) and the retention view stay usable at the all/partner
  // scope.
  if (module === 'support' && !getProductId()) {
    return <RequireProduct title={t('Analytics')} />;
  }

  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('Dashboard')} />
      {showSupport && (
        <Box sx={{ mb: showRetention ? 4 : 0 }}>
          <SectionTitle>{t('Support chat')}</SectionTitle>
          <SupportBlock />
        </Box>
      )}
      {showRetention && (
        <Box>
          <SectionTitle>{t('Retention · Telegram')}</SectionTitle>
          <RetentionBlock />
        </Box>
      )}
    </Box>
  );
};

export default Dashboard;
