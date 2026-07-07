import { useEffect, useState } from 'react';
import { Title } from 'react-admin';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Grid from '@mui/material/Grid';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';

const Kpi = ({ label, value, hint }) => (
  <Grid item xs={12} sm={6} md={3}>
    <Card>
      <CardContent>
        <Typography variant="overline" color="text.secondary">
          {label}
        </Typography>
        <Typography variant="h4">{value ?? '—'}</Typography>
        {hint && (
          <Typography variant="caption" color="text.secondary">
            {hint}
          </Typography>
        )}
      </CardContent>
    </Card>
  </Grid>
);

const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);

/** KPI dashboard over GET /admin/overview + by-topic / by-language tables. */
const Dashboard = () => {
  const [overview, setOverview] = useState(null);
  const [topics, setTopics] = useState([]);
  const [languages, setLanguages] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([
      httpClient(`${API_URL}/admin/overview`),
      httpClient(`${API_URL}/admin/by-topic`),
      httpClient(`${API_URL}/admin/by-language`),
    ])
      .then(([o, t, l]) => {
        setOverview(o.json);
        setTopics(t.json.topics || []);
        setLanguages(l.json.languages || []);
      })
      .catch((e) => setError(e.message || 'Failed to load dashboard'));
  }, []);

  return (
    <Box sx={{ p: 2 }}>
      <Title title="Dashboard" />
      {error && <Typography color="error">{error}</Typography>}
      <Grid container spacing={2}>
        <Kpi label="Sessions (30d)" value={overview?.sessions_total} />
        <Kpi
          label="Open sessions"
          value={overview?.sessions_open}
          hint="engaged, still open"
        />
        <Kpi
          label="Escalated"
          value={overview?.sessions_escalated}
          hint={`escalation rate ${pct(overview?.escalation_rate)}`}
        />
        <Kpi
          label="Cost (USD)"
          value={
            overview?.cost_usd_total != null
              ? `$${overview.cost_usd_total.toFixed(4)}`
              : undefined
          }
          hint={
            overview?.cost_usd_per_session != null
              ? `$${overview.cost_usd_per_session.toFixed(4)} / session`
              : undefined
          }
        />
        <Kpi
          label="Resolution rate"
          value={pct(overview?.resolution_rate)}
          hint="proxy: not escalated (incl. abandoned)"
        />
        <Kpi label="Avg msgs / session" value={overview?.avg_messages_per_session} />
        <Kpi label="Cache hit ratio" value={pct(overview?.cache_hit_ratio)} />
        <Kpi
          label="Blocks"
          value={
            overview
              ? (overview.rate_limit_blocks || 0) + (overview.injection_blocks || 0)
              : undefined
          }
          hint="rate-limit + injection"
        />
      </Grid>

      <Grid container spacing={2} sx={{ mt: 0.5 }}>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                By topic
              </Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Topic</TableCell>
                    <TableCell align="right">Sessions</TableCell>
                    <TableCell align="right">Escalated</TableCell>
                    <TableCell align="right">Cost $</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {topics.map((t) => (
                    <TableRow key={t.topic || t.slug}>
                      <TableCell>{t.topic || t.slug}</TableCell>
                      <TableCell align="right">{t.sessions}</TableCell>
                      <TableCell align="right">{t.escalated}</TableCell>
                      <TableCell align="right">{t.cost_usd_total}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                By language
              </Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Language</TableCell>
                    <TableCell align="right">Sessions</TableCell>
                    <TableCell align="right">Escalated</TableCell>
                    <TableCell align="right">Cost $</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {languages.map((l) => (
                    <TableRow key={l.lang}>
                      <TableCell>{l.lang}</TableCell>
                      <TableCell align="right">{l.sessions}</TableCell>
                      <TableCell align="right">{l.escalated}</TableCell>
                      <TableCell align="right">{l.cost_usd_total}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
};

export default Dashboard;
