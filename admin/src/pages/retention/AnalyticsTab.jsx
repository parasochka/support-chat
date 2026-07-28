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
import { compactTableSx, wideTableSx } from '../../lib/table';
import GridPagination from '../../components/GridPagination';

// The /admin/retention/users endpoint caps a page at 500; 100 is its default
// and a comfortable table page.
const USERS_PER_PAGE = 100;
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

// Long CTA URLs and media descriptions are the norm here, and one unbreakable
// string used to push the numeric columns off the card.
// One line of text on the table layout, free to wrap in the phone's block
// layout — the same component serves both.
const ellipsis = {
  display: 'block',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: { xs: 'normal', sm: 'nowrap' },
  overflowWrap: { xs: 'anywhere', sm: 'normal' },
};

/** A count with its rate underneath — vertical, so the column stays ~64px. */
const CountCell = ({ value, rate }) => (
  <TableCell align="right" sx={{ verticalAlign: 'top' }}>
    <Typography variant="body2" component="div" sx={{ lineHeight: 1.3 }}>
      {value ?? 0}
    </Typography>
    {rate != null && (
      <Typography variant="caption" color="text.secondary" component="div" sx={{ lineHeight: 1.2 }}>
        {pct(rate)}
      </Typography>
    )}
  </TableCell>
);

// The numeric columns are FIXED-width and the label column takes whatever is
// left (tableLayout: 'fixed'), so the table always fits its card — no
// horizontal scroll, on any width. Widths are sized for the real figures: a
// 4-digit count plus a percentage under it.
const OUTCOME_COL_PX = 66;

/**
 * One cut of the attribution ledger. Above `sm` a fixed-layout table; on a
 * phone a block per row (six columns at 400px turn into a one-word-per-line
 * ribbon whose last figures fall off the edge). On the table layout a long list
 * scrolls VERTICALLY inside the card under a sticky header — the media cut
 * returns up to 100 rows, which would otherwise push the rest of the page far
 * below the fold. The phone layout keeps the page's own scroll (a scrollable
 * box inside a touch page is a trap).
 */
const OutcomeTable = ({ rows, firstLabel, renderFirst, empty, maxHeight = 340 }) => {
  const isMobile = useIsMobile();
  if (!rows?.length) {
    return (
      <Typography variant="body2" color="text.secondary">
        {empty}
      </Typography>
    );
  }
  if (isMobile) {
    return (
      <Stack spacing={1}>
        {rows.map((r, i) => (
          <Box key={i} sx={{ borderTop: i ? 1 : 0, borderColor: 'divider', pt: i ? 1 : 0 }}>
            <Typography variant="body2" component="div" sx={{ overflowWrap: 'anywhere' }}>
              {renderFirst(r)}
            </Typography>
            <Stack
              direction="row"
              spacing={1.5}
              useFlexGap
              sx={{ flexWrap: 'wrap', mt: 0.5 }}
            >
              <MobileStat label={t('Sent')} value={r.sends} />
              <MobileStat label={t('Replied')} value={r.replies} rate={r.reply_rate} />
              <MobileStat label={t('Returned')} value={r.returns} rate={r.return_rate} />
              <MobileStat label={t('Deposited')} value={r.deposits} rate={r.deposit_rate} />
              <MobileStat label={t('Avg reply')} value={latency(r.avg_reply_latency_sec)} />
            </Stack>
          </Box>
        ))}
      </Stack>
    );
  }
  return (
    <Box sx={{ maxHeight, overflowY: 'auto' }}>
      <Table size="small" sx={{ tableLayout: 'fixed', ...compactTableSx }}>
        <colgroup>
          <col />
          <col style={{ width: OUTCOME_COL_PX }} />
          <col style={{ width: OUTCOME_COL_PX }} />
          <col style={{ width: OUTCOME_COL_PX }} />
          <col style={{ width: OUTCOME_COL_PX }} />
          <col style={{ width: OUTCOME_COL_PX }} />
        </colgroup>
        <TableHead
          sx={{
            position: 'sticky',
            top: 0,
            zIndex: 1,
            bgcolor: 'background.paper',
          }}
        >
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
              <TableCell sx={{ verticalAlign: 'top' }}>{renderFirst(r)}</TableCell>
              <CountCell value={r.sends} />
              <CountCell value={r.replies} rate={r.reply_rate} />
              <CountCell value={r.returns} rate={r.return_rate} />
              <CountCell value={r.deposits} rate={r.deposit_rate} />
              <TableCell align="right" sx={{ verticalAlign: 'top' }}>
                {latency(r.avg_reply_latency_sec)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
};

const MobileStat = ({ label, value, rate }) => (
  <Typography variant="caption" color="text.secondary" component="span">
    {label}: <Box component="span" sx={{ color: 'text.primary' }}>{value ?? 0}</Box>
    {rate != null ? ` (${pct(rate)})` : ''}
  </Typography>
);

/**
 * The label column of an outcome row: a title that truncates instead of
 * widening the table, plus a muted detail line (also truncated). `title` keeps
 * the full value reachable on hover.
 */
const OutcomeLabel = ({ title, detail }) => (
  <>
    <Typography
      variant="body2"
      component="span"
      title={typeof title === 'string' ? title : undefined}
      sx={ellipsis}
    >
      {title}
    </Typography>
    {detail ? (
      <Typography
        variant="caption"
        color="text.secondary"
        component="span"
        title={typeof detail === 'string' ? detail : undefined}
        sx={ellipsis}
      >
        {detail}
      </Typography>
    ) : null}
  </>
);

/** A CTA page reads as its path — the origin repeats on every row. */
const linkLabel = (url) => {
  try {
    const u = new URL(url);
    return `${u.pathname}${u.search}` || '/';
  } catch {
    return url;
  }
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
      {/* Two-up only from `xl`. Below that each cut gets the full width: a
          half-width card on a 1440px laptop left ~570px for six columns, which
          is what forced the horizontal scroll these tables used to have. */}
      <Grid container spacing={2} sx={{ mb: 2 }}>
        <OutcomeCard title={t('Media')}>
          <OutcomeTable
            rows={data?.media}
            firstLabel={t('Photo / video')}
            empty={t('No media delivered in this range yet.')}
            renderFirst={(r) => (
              <OutcomeLabel
                title={`#${r.photo_id} · ${r.media_type}`}
                detail={r.description || t('(deleted)')}
              />
            )}
          />
        </OutcomeCard>
        <OutcomeCard title={t('Site pages (CTA buttons)')}>
          <OutcomeTable
            rows={data?.links}
            firstLabel={t('Page')}
            empty={t('No CTA buttons attached in this range yet.')}
            renderFirst={(r) => (
              <OutcomeLabel title={linkLabel(r.link_url)} detail={r.link_url} />
            )}
          />
        </OutcomeCard>
        <OutcomeCard title={t('Idle ladder rungs')}>
          <OutcomeTable
            rows={data?.idle_rules}
            firstLabel={t('Rule')}
            empty={t('No idle pings fired in this range yet.')}
            renderFirst={(r) => (
              <OutcomeLabel
                title={r.name}
                detail={[
                  r.inactivity_days != null ? `${r.inactivity_days}d` : null,
                  r.trigger_kind || null,
                  r.enabled === false ? t('disabled') : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              />
            )}
          />
        </OutcomeCard>
        <OutcomeCard title={t('Triggers')}>
          <OutcomeTable
            rows={data?.events}
            firstLabel={t('Trigger')}
            empty={t('No proactive touches in this range yet.')}
            renderFirst={(r) => (
              <OutcomeLabel title={r.event_name} />
            )}
          />
        </OutcomeCard>
      </Grid>
    </>
  );
};

const OutcomeCard = ({ title, children }) => (
  <Grid size={{ xs: 12, xl: 6 }}>
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
          {title}
        </Typography>
        {children}
      </CardContent>
    </Card>
  </Grid>
);

const AnalyticsTab = ({ productId }) => {
  const isMobile = useIsMobile();
  const notify = useNotify();
  const [range, setRange] = useState(defaultRange);
  const [overview, setOverview] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [series, setSeries] = useState([]);
  const [users, setUsers] = useState([]);
  const [usersTotal, setUsersTotal] = useState(0);
  const [usersPage, setUsersPage] = useState(1);
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

  // Paginated: the endpoint caps at 100 rows per page, so fetching it bare
  // rendered "Linked players (100)" next to a KPI showing the real, larger
  // total — two contradicting numbers on one screen, with the table silently
  // truncated and no way to reach the rest.
  useEffect(() => {
    let alive = true;
    const offset = (usersPage - 1) * USERS_PER_PAGE;
    httpClient(
      `${API_URL}/admin/retention/users?product_id=${productId}` +
        `&limit=${USERS_PER_PAGE}&offset=${offset}`
    )
      .then(({ json }) => {
        if (!alive) return;
        setUsers(json.items || []);
        setUsersTotal(json.total || 0);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [productId, usersPage]);

  // A product switch invalidates the page number.
  useEffect(() => { setUsersPage(1); }, [productId]);

  const base = overview?.users;
  const inRange = overview?.range;
  const replyRate =
    inRange?.ping_reply_rate != null
      ? t('{pct}% reply rate').replace('{pct}', (inRange.ping_reply_rate * 100).toFixed(1))
      : t('no pings in range');

  return (
    <Box>
      {/* The two date fields share ONE row (a 2-column grid whose columns can
          shrink below the inputs' intrinsic width), so they never wrap into a
          stack on narrow screens; the caption gets its own line below. */}
      <Box sx={{ mb: 2 }}>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: 1,
            maxWidth: 440,
          }}
        >
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
        </Box>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
          {t('Both days inclusive. “Player base” below is lifetime; everything else counts this range.')}
        </Typography>
      </Box>

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
          hint={t('dialogue + agent + media + review')}
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
        {t('Linked players')} ({usersTotal})
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
      {usersTotal > USERS_PER_PAGE && (
        <GridPagination
          count={usersTotal}
          page={usersPage}
          perPage={USERS_PER_PAGE}
          onPage={setUsersPage}
          unit={t('players')}
        />
      )}
    </Box>
  );
};

// ---------------------------------------------------------------------------
// page shell — needs a concrete product (retention is strictly per-product)

export default AnalyticsTab;
