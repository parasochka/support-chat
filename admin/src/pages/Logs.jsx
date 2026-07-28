import { useCallback, useEffect, useRef, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import LinearProgress from '@mui/material/LinearProgress';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Tab from '@mui/material/Tab';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';

import { API_URL, httpClient } from '../httpClient';
import { scopeParams } from '../productScope';
import { getScopeName } from '../productScope';
import { t } from '../i18n';
import { wideTableSx, nowrapCellSx } from '../lib/table';
import { fmtDateTime } from '../lib/fmt';
import { notifyError } from '../lib/notifyError';
import useDebounced from '../lib/useDebounced';
import useIsMobile from '../lib/useIsMobile';

const LEVEL_COLOR = {
  ERROR: 'error',
  CRITICAL: 'error',
  WARNING: 'warning',
  INFO: 'info',
  DEBUG: 'default',
};

const fmt = (iso) => fmtDateTime(iso);

const buildQuery = (params) => {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') q.set(k, v);
  });
  const s = q.toString();
  return s ? `?${s}` : '';
};

// -------------------------------------------------------------------------
// System (runtime) logs — the Railway logs mirrored in-app.
// -------------------------------------------------------------------------
const SystemLogs = () => {
  const isMobile = useIsMobile();
  const [rows, setRows] = useState([]);
  const [level, setLevel] = useState('');
  const [q, setQ] = useState('');
  // Debounced: the fetch used to be keyed on `q` itself, so every keystroke
  // fired GET /admin/logs *and* POST /admin/logs/read, with nothing cancelling
  // them — a slower earlier response could land last and overwrite the newer
  // rows.
  const debouncedQ = useDebounced(q);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(null);
  const notify = useNotify();
  // Monotonic request id: only the newest in-flight load may write state.
  const reqId = useRef(0);

  const load = useCallback(
    async (beforeId) => {
      const mine = ++reqId.current;
      setLoading(true);
      try {
        const query = buildQuery({
          level, q: debouncedQ, before_id: beforeId, limit: 100,
        });
        const { json } = await httpClient(`${API_URL}/admin/logs${query}`);
        if (mine !== reqId.current) return;
        const items = json.items || [];
        setRows((prev) => (beforeId ? [...prev, ...items] : items));
        setHasMore(items.length === 100);
        setError(null);
      } catch (e) {
        if (mine !== reqId.current) return;
        // Without this the promise rejected unhandled AND the table simply
        // rendered "no logs match the filter" — so a 403 (this page needs
        // global read access) looked like an empty log.
        setRows([]);
        setHasMore(false);
        setError(e);
        notifyError(notify, e, t('Could not load logs'));
      } finally {
        if (mine === reqId.current) setLoading(false);
      }
    },
    [level, debouncedQ, notify]
  );

  // Load + clear the unread badge when the tab is opened / filters change.
  useEffect(() => {
    load();
    httpClient(`${API_URL}/admin/logs/read`, { method: 'POST' }).catch(() => {});
  }, [load]);

  return (
    <Box>
      <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', mb: 1.5 }}>
        <TextField
          select
          size="small"
          label={t('Level')}
          value={level}
          onChange={(e) => setLevel(e.target.value)}
          sx={{ minWidth: 160 }}
        >
          <MenuItem value="">{t('All levels')}</MenuItem>
          <MenuItem value="info">{t('Info')}</MenuItem>
          <MenuItem value="warning">{t('Warnings & errors')}</MenuItem>
          <MenuItem value="error">{t('Errors only')}</MenuItem>
        </TextField>
        <TextField
          size="small"
          label={t('Search text')}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          sx={{ flex: 1, minWidth: 200 }}
        />
        <Button variant="outlined" onClick={() => load()} disabled={loading}>
          {t('Refresh')}
        </Button>
      </Stack>
      {loading && <LinearProgress sx={{ mb: 1 }} />}
      {error && (
        <Alert severity="error" sx={{ mb: 1.5 }}>
          {error.status === 403
            ? t('This page needs global read access.')
            : t('Could not load logs')}
        </Alert>
      )}
      {isMobile ? (
        // A log line is one long monospace string: squeezed into a third of a
        // phone screen it becomes an unreadable ribbon, and left at its desktop
        // width it hangs off the edge. Stacked cards give it the full width.
        <Stack spacing={1}>
          {rows.map((r) => (
            <Card key={r.id} variant="outlined">
              <CardContent sx={{ py: 1, '&:last-child': { pb: 1 } }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                  <Chip size="small" color={LEVEL_COLOR[r.level] || 'default'} label={r.level} />
                  <Typography variant="caption" color="text.secondary">
                    {fmt(r.created_at)}
                  </Typography>
                </Box>
                <Typography
                  variant="body2"
                  sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontFamily: 'monospace', fontSize: 12 }}
                >
                  {r.message}
                </Typography>
              </CardContent>
            </Card>
          ))}
          {!rows.length && !loading && (
            <Typography color="text.secondary" sx={{ py: 2 }}>
              {t('No logs match the filter.')}
            </Typography>
          )}
        </Stack>
      ) : (
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={wideTableSx(640)}>
          <TableHead>
            <TableRow>
              <TableCell sx={nowrapCellSx}>{t('Time')}</TableCell>
              <TableCell>{t('Level')}</TableCell>
              <TableCell>{t('Message')}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.id}>
                <TableCell sx={{ ...nowrapCellSx, verticalAlign: 'top' }}>
                  {fmt(r.created_at)}
                </TableCell>
                <TableCell sx={{ verticalAlign: 'top' }}>
                  <Chip size="small" color={LEVEL_COLOR[r.level] || 'default'} label={r.level} />
                </TableCell>
                <TableCell sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontFamily: 'monospace', fontSize: 12 }}>
                  {r.message}
                </TableCell>
              </TableRow>
            ))}
            {!rows.length && !loading && (
              <TableRow>
                <TableCell colSpan={3}>
                  <Typography color="text.secondary" sx={{ py: 2 }}>
                    {t('No logs match the filter.')}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Box>
      )}
      {hasMore && (
        <Button
          sx={{ mt: 1 }}
          onClick={() => load(rows[rows.length - 1]?.id)}
          disabled={loading}
        >
          {t('Load more')}
        </Button>
      )}
    </Box>
  );
};

// -------------------------------------------------------------------------
// Activity log — the admin-action audit trail (who changed what).
// -------------------------------------------------------------------------
const ActivityLog = () => {
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState('');
  const debouncedQ = useDebounced(q);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(null);
  const notify = useNotify();
  const reqId = useRef(0);

  const load = useCallback(
    async (beforeId) => {
      const mine = ++reqId.current;
      setLoading(true);
      try {
        const query = buildQuery({
          q: debouncedQ, before_id: beforeId, limit: 100, ...scopeParams(),
        });
        const { json } = await httpClient(`${API_URL}/admin/audit${query}`);
        if (mine !== reqId.current) return;
        const items = json.items || [];
        setRows((prev) => (beforeId ? [...prev, ...items] : items));
        setHasMore(items.length === 100);
        setError(null);
      } catch (e) {
        if (mine !== reqId.current) return;
        setRows([]);
        setHasMore(false);
        setError(e);
        notifyError(notify, e, t('Could not load the activity log'));
      } finally {
        if (mine === reqId.current) setLoading(false);
      }
    },
    [debouncedQ, notify]
  );

  useEffect(() => {
    load();
  }, [load]);

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        {t(
          'Who changed what in the admin panel. You see actions within your access scope; administrators see every action in reach, managers see only manager-made changes.'
        )}
        {getScopeName() ? ` (${getScopeName()})` : ''}
      </Typography>
      <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', mb: 1.5 }}>
        <TextField
          size="small"
          label={t('Search (actor, action)')}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          sx={{ flex: 1, minWidth: 200 }}
        />
        <Button variant="outlined" onClick={() => load()} disabled={loading}>
          {t('Refresh')}
        </Button>
      </Stack>
      {loading && <LinearProgress sx={{ mb: 1 }} />}
      {error && (
        <Alert severity="error" sx={{ mb: 1.5 }}>
          {error.status === 403
            ? t('This page needs global read access.')
            : t('Could not load the activity log')}
        </Alert>
      )}
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={wideTableSx(680)}>
          <TableHead>
            <TableRow>
              <TableCell sx={nowrapCellSx}>{t('Time')}</TableCell>
              <TableCell>{t('Who')}</TableCell>
              <TableCell>{t('Action')}</TableCell>
              <TableCell>{t('Product')}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.id}>
                <TableCell sx={nowrapCellSx}>{fmt(r.created_at)}</TableCell>
                <TableCell sx={{ wordBreak: 'break-all' }}>
                  {r.actor_email}
                  {r.actor_role && (
                    <Chip size="small" variant="outlined" label={r.actor_role} sx={{ ml: 0.5 }} />
                  )}
                </TableCell>
                <TableCell>{r.action}</TableCell>
                <TableCell>{r.product_name || (r.product_id ? `#${r.product_id}` : '—')}</TableCell>
              </TableRow>
            ))}
            {!rows.length && !loading && (
              <TableRow>
                <TableCell colSpan={4}>
                  <Typography color="text.secondary" sx={{ py: 2 }}>
                    {t('No actions recorded yet.')}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Box>
      {hasMore && (
        <Button
          sx={{ mt: 1 }}
          onClick={() => load(rows[rows.length - 1]?.id)}
          disabled={loading}
        >
          {t('Load more')}
        </Button>
      )}
    </Box>
  );
};

const Logs = () => {
  const [tab, setTab] = useState(0);
  return (
    <Box>
      <Title title={t('Logs')} />
      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Tabs
            value={tab}
            onChange={(e, v) => setTab(v)}
            variant="scrollable"
            scrollButtons="auto"
            sx={{ mb: 2 }}
          >
            <Tab label={t('System logs')} />
            <Tab label={t('Activity (who changed what)')} />
          </Tabs>
          {tab === 0 ? <SystemLogs /> : <ActivityLog />}
        </CardContent>
      </Card>
    </Box>
  );
};

export default Logs;
