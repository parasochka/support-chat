import { useCallback, useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Grid from '@mui/material/Grid';
import Link from '@mui/material/Link';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { scopeParams } from '../productScope';
import { Kpi } from '../components/Kpi';
import GridPagination from '../components/GridPagination';
import { t } from '../i18n';
import { fmtDateTime } from '../lib/fmt';
import useIsMobile from '../lib/useIsMobile';

// ---------------------------------------------------------------------------
// Quality — the AI judge's verdicts on finished conversations (both facades).
//
// A background pass (ai/reviewer.py) scores each finished chat 1..5, tags what
// went wrong from a fixed taxonomy and lists the questions the knowledge base
// could not answer. Nothing here changes behaviour: this is the triage queue a
// human works from — worst conversations first, KB gaps counted.
// ---------------------------------------------------------------------------
const isoDay = (d) => d.toISOString().slice(0, 10);
const defaultRange = () => ({
  from: isoDay(new Date(Date.now() - 30 * 86400000)),
  to: isoDay(new Date()),
});

const SCORE_COLORS = { 1: 'error', 2: 'error', 3: 'warning', 4: 'success', 5: 'success' };

const qs = (params) =>
  Object.entries(params)
    .filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join('&');

const Quality = () => {
  const notify = useNotify();
  const isMobile = useIsMobile();
  const [range, setRange] = useState(defaultRange);
  const [filters, setFilters] = useState({ consumer: '', tag: '', max_score: '' });
  const [overview, setOverview] = useState(null);
  const [list, setList] = useState({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(null); // the review whose detail is shown
  const pageSize = 25;

  const loadOverview = useCallback(() => {
    const params = qs({ ...scopeParams(), from: range.from, to: range.to });
    httpClient(`${API_URL}/admin/quality/overview?${params}`)
      .then(({ json }) => setOverview(json))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [range, notify]);

  const loadList = useCallback(() => {
    const params = qs({
      ...scopeParams(),
      from: range.from,
      to: range.to,
      ...filters,
      page,
      page_size: pageSize,
    });
    httpClient(`${API_URL}/admin/quality/reviews?${params}`)
      .then(({ json }) => setList(json))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [range, filters, page, notify]);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);
  useEffect(() => {
    loadList();
  }, [loadList]);

  const taxonomy = overview?.taxonomy || [];
  const tagHelp = (tag) => taxonomy.find((x) => x.tag === tag)?.description || tag;

  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('Quality')} />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {t(
          'An AI judge reads finished conversations — the support widget and the Telegram bot — and scores how the assistant handled them. It changes nothing on its own: use it to find what to fix in the knowledge base, the prompt variables or the pacing. Switch it on (and cap its daily budget) in Settings → System settings.'
        )}
      </Typography>

      {/* The filters are a GRID, not a wrapping flex row: `flexWrap`/`alignItems`
          are MUI *system* props, which <Stack> no longer accepts (only sx does),
          so the old row never wrapped — four fields with minWidth 145 pinned the
          page at ~620px and the whole document scrolled sideways on a phone
          (mobile browsers then shrink-to-fit and everything renders tiny).
          `minmax(0, 1fr)` columns can shrink below the inputs' intrinsic width,
          so the row always fits: 2 columns on phones, 4 from `md` up. */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: 'repeat(2, minmax(0, 1fr))', md: 'repeat(4, minmax(0, 1fr))' },
          gap: 1,
          alignItems: 'center',
          mb: 2,
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
        <TextField
          size="small"
          select
          label={t('Channel')}
          value={filters.consumer}
          onChange={(e) => {
            setPage(1);
            setFilters({ ...filters, consumer: e.target.value });
          }}
        >
          <MenuItem value="">{t('All')}</MenuItem>
          <MenuItem value="web">{t('Support widget')}</MenuItem>
          <MenuItem value="telegram">{t('Telegram bot')}</MenuItem>
        </TextField>
        <TextField
          size="small"
          select
          label={t('Score at most')}
          value={filters.max_score}
          onChange={(e) => {
            setPage(1);
            setFilters({ ...filters, max_score: e.target.value });
          }}
        >
          <MenuItem value="">{t('Any')}</MenuItem>
          {[1, 2, 3, 4].map((n) => (
            <MenuItem key={n} value={n}>
              ≤ {n}
            </MenuItem>
          ))}
        </TextField>
        {filters.tag && (
          <Chip
            label={filters.tag}
            onDelete={() => {
              setPage(1);
              setFilters({ ...filters, tag: '' });
            }}
            sx={{ gridColumn: '1 / -1', justifySelf: 'start', maxWidth: '100%' }}
          />
        )}
      </Box>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Kpi size={{ xs: 6, sm: 4, md: 3 }} label={t('Reviewed')} value={overview?.reviews} hint={t('conversations in range')} />
        <Kpi
          size={{ xs: 6, sm: 4, md: 3 }}
          label={t('Average score')}
          value={overview?.avg_score != null ? overview.avg_score.toFixed(2) : undefined}
          hint={t('1 = bad, 5 = excellent')}
        />
        <Kpi size={{ xs: 6, sm: 4, md: 3 }} label={t('Poor (≤2)')} value={overview?.poor} hint={t('the triage queue')} />
        <Kpi size={{ xs: 6, sm: 4, md: 3 }} label={t('Good (≥4)')} value={overview?.good} />
        <Kpi size={{ xs: 6, sm: 4, md: 3 }} label={t('Telegram chats')} value={overview?.telegram} hint={t('the rest are widget chats')} />
        <Kpi
          size={{ xs: 6, sm: 4, md: 3 }}
          label={t('Review cost')}
          value={overview?.cost_usd != null ? `$${Number(overview.cost_usd).toFixed(4)}` : undefined}
          hint={t('what the judge itself cost')}
        />
      </Grid>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                {t('What goes wrong')}
              </Typography>
              {overview?.tags?.length ? (
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, minWidth: 0 }}>
                  {overview.tags.map((x) => (
                    <Tooltip key={x.tag} title={tagHelp(x.tag)}>
                      <Chip
                        label={`${x.tag} · ${x.count}`}
                        size="small"
                        color={x.tag === 'good' ? 'success' : 'default'}
                        variant={filters.tag === x.tag ? 'filled' : 'outlined'}
                        onClick={() => {
                          setPage(1);
                          setFilters({ ...filters, tag: filters.tag === x.tag ? '' : x.tag });
                        }}
                      />
                    </Tooltip>
                  ))}
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  {t('No reviews in this range yet.')}
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                {t('Knowledge-base gaps')}
              </Typography>
              <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
                {t('Questions players asked that the KB could not answer — the shortlist for the next KB edit.')}
              </Typography>
              {overview?.kb_gaps?.length ? (
                <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                  {overview.kb_gaps.map((g, i) => (
                    <Typography key={i} component="li" variant="body2" sx={{ mb: 0.5 }}>
                      {g.count > 1 ? `${g.count}× ` : ''}
                      {g.question}
                    </Typography>
                  ))}
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  {t('None found in this range.')}
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {isMobile ? (
        // Card rows on phones — the same pattern the other list views use. The
        // 760px table cut the Summary column (the actual verdict) off the screen
        // edge, which is the one thing this queue exists to show.
        <Stack spacing={1} sx={{ mb: 1 }}>
          {list.items.map((r) => (
            <Card
              key={r.id}
              variant="outlined"
              onClick={() => setOpen(r)}
              sx={{ cursor: 'pointer' }}
            >
              <CardContent sx={{ py: 1.25, '&:last-child': { pb: 1.25 } }}>
                <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 1 }}>
                  <Chip
                    size="small"
                    label={r.score ?? '—'}
                    color={SCORE_COLORS[r.score] || 'default'}
                    variant="outlined"
                  />
                  <Typography variant="subtitle2">
                    {r.consumer === 'telegram' ? t('Telegram') : t('Widget')}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
                    {fmtDateTime(r.created_at)}
                  </Typography>
                </Box>
                <Typography variant="body2" sx={{ mt: 0.5, overflowWrap: 'anywhere' }}>
                  {r.summary}
                </Typography>
                {(r.tags || []).length > 0 && (
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.75 }}>
                    {r.tags.map((tag) => (
                      <Chip key={tag} size="small" variant="outlined" label={tag} />
                    ))}
                  </Box>
                )}
              </CardContent>
            </Card>
          ))}
          {list.items.length === 0 && (
            <Typography color="text.secondary" sx={{ py: 2 }}>
              {t(
                'Nothing reviewed yet. The judge runs automatically in the background over finished conversations.'
              )}
            </Typography>
          )}
        </Stack>
      ) : (
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={{ minWidth: 760 }}>
          <TableHead>
            <TableRow>
              <TableCell align="right">{t('Score')}</TableCell>
              <TableCell>{t('Channel')}</TableCell>
              <TableCell>{t('Tags')}</TableCell>
              <TableCell>{t('Summary')}</TableCell>
              <TableCell>{t('Reviewed')}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {list.items.map((r) => (
              <TableRow key={r.id} hover sx={{ cursor: 'pointer' }} onClick={() => setOpen(r)}>
                <TableCell align="right">
                  <Chip
                    size="small"
                    label={r.score ?? '—'}
                    color={SCORE_COLORS[r.score] || 'default'}
                    variant="outlined"
                  />
                </TableCell>
                <TableCell>{r.consumer === 'telegram' ? t('Telegram') : t('Widget')}</TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                    {(r.tags || []).map((tag) => (
                      <Chip key={tag} size="small" variant="outlined" label={tag} />
                    ))}
                  </Box>
                </TableCell>
                <TableCell>{r.summary}</TableCell>
                <TableCell>{fmtDateTime(r.created_at)}</TableCell>
              </TableRow>
            ))}
            {list.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={5}>
                  <Typography color="text.secondary" sx={{ py: 2 }}>
                    {t(
                      'Nothing reviewed yet. The judge runs automatically in the background over finished conversations.'
                    )}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Box>
      )}
      <GridPagination count={list.total || 0} page={page} perPage={pageSize} onPage={setPage} />

      <Dialog
        open={Boolean(open)}
        onClose={() => setOpen(null)}
        maxWidth="md"
        fullWidth
        fullScreen={isMobile}
      >
        <DialogTitle>
          {t('Review')} · {open?.score ?? '—'}/5
        </DialogTitle>
        <DialogContent dividers>
          {open && (
            <Stack spacing={2}>
              <Typography variant="body1">{open.summary}</Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                {(open.tags || []).map((tag) => (
                  <Tooltip key={tag} title={tagHelp(tag)}>
                    <Chip size="small" label={tag} />
                  </Tooltip>
                ))}
              </Box>
              {(open.issues || []).length > 0 && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    {t('Issues')}
                  </Typography>
                  <Stack spacing={1}>
                    {open.issues.map((it, i) => (
                      <Box key={i}>
                        <Typography variant="body2" sx={{ fontStyle: 'italic' }}>
                          “{it.quote}”
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {it.why}
                        </Typography>
                      </Box>
                    ))}
                  </Stack>
                </Box>
              )}
              {(open.kb_gaps || []).length > 0 && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    {t('Knowledge-base gaps')}
                  </Typography>
                  {open.kb_gaps.map((g, i) => (
                    <Typography key={i} variant="body2">
                      • {g}
                    </Typography>
                  ))}
                </Box>
              )}
              <Typography variant="caption" color="text.secondary">
                {t('Conversation')}:{' '}
                <Link
                  href={`#/sessions/${open.session_id}/show`}
                  target="_blank"
                  rel="noopener"
                  underline="hover"
                >
                  {open.session_id}
                </Link>
                {open.product_name ? ` · ${open.product_name}` : ''}
                {open.model ? ` · ${open.model}` : ''}
              </Typography>
            </Stack>
          )}
        </DialogContent>
        {/* Full-screen on a phone has no backdrop to tap — the button is the
            only way out there. */}
        <DialogActions>
          <Button onClick={() => setOpen(null)}>{t('Close')}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default Quality;
