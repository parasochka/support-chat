import { useEffect, useState } from 'react';
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
import { API_URL, httpClient } from '../httpClient';
import { scopeParams } from '../productScope';

const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
const usd = (v) => (v == null ? '—' : `$${Number(v).toFixed(4)}`);
const num = (v) => (v == null ? '—' : String(v));

// Chart series colors: validated categorical slots (dataviz reference palette),
// stepped per theme mode so contrast holds on both surfaces.
const CHART_COLORS = {
  light: ['#2a78d6', '#1baf7a', '#eda100', '#008300'],
  dark: ['#3987e5', '#199e70', '#c98500', '#008300'],
};

const CHARTS = [
  ['sessions', 'Sessions over time', (v) => String(Math.round(v))],
  ['cost', 'Cost over time', usd],
  ['cost_per_session', 'Avg cost / session', usd],
  ['escalation_rate', 'Escalation rate', pct],
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
                <TableCell align="right">Sessions</TableCell>
                <TableCell align="right">Escalated</TableCell>
                <TableCell align="right">Cost $</TableCell>
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

/**
 * KPI dashboard over GET /admin/overview + the four daily time-series charts
 * (/admin/timeseries) + by-topic / by-language breakdowns. Everything follows
 * the header product scope; the default range is the backend's last 30 days.
 */
const Dashboard = () => {
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
      .catch((e) => setError(e.message || 'Failed to load dashboard'));
    CHARTS.forEach(([metric]) => {
      httpClient(`${API_URL}/admin/timeseries${qs}${amp}metric=${metric}&bucket=day`)
        .then(({ json }) =>
          setCharts((prev) => ({ ...prev, [metric]: json.series || [] }))
        )
        .catch(() => setCharts((prev) => ({ ...prev, [metric]: [] })));
    });
  }, []);

  const colors = CHART_COLORS[theme.palette.mode] || CHART_COLORS.dark;

  return (
    <Box sx={{ p: 2 }}>
      <Title title="Dashboard" />
      {error && <Typography color="error">{error}</Typography>}

      <Grid container spacing={2} alignItems="stretch">
        <Kpi label="Sessions (30d)" value={num(overview?.sessions_total)} />
        <Kpi
          label="Engaged"
          value={num(overview?.sessions_engaged)}
          hint="≥ 1 message"
        />
        <Kpi
          label="Open sessions"
          value={num(overview?.sessions_open)}
          hint="engaged, still open"
        />
        <Kpi
          label="Escalated"
          value={num(overview?.sessions_escalated)}
          hint={`rate ${pct(overview?.escalation_rate)}`}
        />
        <Kpi
          label="Resolution rate"
          value={pct(overview?.resolution_rate)}
          hint="proxy: not escalated"
        />
        <Kpi
          label="Avg msgs / session"
          value={num(overview?.avg_messages_per_session)}
        />
        <Kpi
          label="Cost (USD)"
          value={usd(overview?.cost_usd_total)}
          hint={
            overview?.cost_usd_per_session != null
              ? `${usd(overview.cost_usd_per_session)} / session`
              : undefined
          }
        />
        <Kpi label="Cache hit ratio" value={pct(overview?.cache_hit_ratio)} />
        <Kpi label="Key failovers" value={num(overview?.failovers)} />
        <Kpi
          label="Blocks"
          value={
            overview
              ? num((overview.rate_limit_blocks || 0) + (overview.injection_blocks || 0))
              : '—'
          }
          hint="rate-limit + injection"
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
          title="By topic"
          rows={topics}
          nameLabel="Topic"
          nameOf={(r) => r.topic || r.slug}
        />
        <BreakdownTable
          title="By language"
          rows={languages}
          nameLabel="Language"
          nameOf={(r) => r.lang}
        />
      </Grid>
    </Box>
  );
};

export default Dashboard;
