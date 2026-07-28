import { useEffect, useMemo, useState } from 'react';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import MenuItem from '@mui/material/MenuItem';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { StackedBarChart, usd } from './charts';
import { API_URL, httpClient } from '../httpClient';
import { getProductId } from '../productScope';
import { t } from '../i18n';

// ---------------------------------------------------------------------------
// Platform-wide AI spend histogram (System → Settings, under the AI-model
// group): GET /admin/ai-costs — daily OpenAI cost across EVERY call in scope
// (both bots + the background passes), stacked by call type. Its scope filter
// is deliberately independent of the header switcher (the header product only
// seeds the initial value), so the operator can widen to a partner or the
// whole platform without re-scoping the entire panel.
// ---------------------------------------------------------------------------

const isoDay = (d) => d.toISOString().slice(0, 10);
const defaultRange = () => ({
  from: isoDay(new Date(Date.now() - 30 * 86400000)),
  to: isoDay(new Date()),
});

// The call types behind the split — same labels as the Telegram cost-by-source
// panel; `chat` here is dialogue spend of BOTH facades (widget + Telegram).
// Resolved per call: t() reads the admin language chosen at runtime.
const sourceSeries = () => [
  { key: 'cost_chat_usd', value: 'chat', label: t('Dialogue') },
  { key: 'cost_agent_usd', value: 'agent', label: t('Proactive agent') },
  { key: 'cost_media_usd', value: 'media', label: t('Media cataloguing') },
  { key: 'cost_review_usd', value: 'review', label: t('Quality review') },
  { key: 'cost_legacy_usd', value: 'legacy', label: t('Unattributed (legacy)') },
];

const AiCostsPanel = () => {
  const headerProduct = getProductId();
  // 'all' | 'partner:<id>' | 'product:<id>' — seeded from the header switcher.
  const [scope, setScope] = useState(headerProduct ? `product:${headerProduct}` : 'all');
  const [source, setSource] = useState('');
  const [range, setRange] = useState(defaultRange);
  const [structure, setStructure] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    httpClient(`${API_URL}/admin/structure`)
      .then(({ json }) => setStructure(json))
      .catch(() => setStructure({ partners: [] }));
  }, []);

  useEffect(() => {
    const params = new URLSearchParams({ from: range.from, to: range.to });
    if (scope.startsWith('product:')) params.set('product_id', scope.slice(8));
    else if (scope.startsWith('partner:')) params.set('partner_id', scope.slice(8));
    httpClient(`${API_URL}/admin/ai-costs?${params.toString()}`)
      .then(({ json }) => {
        setError(null);
        setData(json);
      })
      .catch((e) => setError(e.message || t('Load failed')));
  }, [scope, range]);

  // A source filter narrows the stack to that one series; otherwise all types
  // show, with the legacy series dropped once every bucket of it is zero.
  const series = useMemo(() => {
    const all = sourceSeries();
    if (source) return all.filter((s) => s.value === source);
    return all.filter(
      (s) =>
        s.value !== 'legacy' ||
        (data?.series || []).some((row) => Number(row?.cost_legacy_usd) > 0)
    );
  }, [source, data]);

  const totals = data?.totals;
  const shownTotal = source
    ? totals?.[series[0]?.key]
    : totals?.cost_usd;

  // The scope picker options come from /admin/structure, so a scoped admin
  // only ever sees (and can only request) what it may read anyway.
  const partners = structure?.partners || [];

  return (
    <Card sx={{ mt: 2 }}>
      <CardContent>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          {t('AI cost by call type')}
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1.5 }}>
          {t('Every OpenAI call in the selected scope — both bots and the background passes — stacked per day by what the call was: player dialogue, the proactive agent, media cataloguing, the AI quality judge.')}
        </Typography>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: 'repeat(2, minmax(0, 1fr))', md: 'repeat(4, minmax(0, 1fr))' },
            gap: 1,
            mb: 1.5,
          }}
        >
          <TextField
            size="small"
            select
            label={t('Scope')}
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            <MenuItem value="all">{t('Entire platform (all available)')}</MenuItem>
            {partners.flatMap((pa) => [
              <MenuItem key={`partner:${pa.id}`} value={`partner:${pa.id}`}>
                {t('Partner')}: {pa.name}
              </MenuItem>,
              ...(pa.products || []).map((pr) => (
                <MenuItem key={`product:${pr.id}`} value={`product:${pr.id}`} sx={{ pl: 4 }}>
                  {pr.name}
                </MenuItem>
              )),
            ])}
          </TextField>
          <TextField
            size="small"
            select
            label={t('Call type')}
            value={source}
            onChange={(e) => setSource(e.target.value)}
          >
            <MenuItem value="">{t('All types')}</MenuItem>
            {sourceSeries().map((s) => (
              <MenuItem key={s.value} value={s.value}>
                {s.label}
              </MenuItem>
            ))}
          </TextField>
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
        {error ? (
          <Typography variant="body2" color="text.secondary">
            {t('metrics could not be loaded')} ({error})
          </Typography>
        ) : (
          <>
            <StackedBarChart
              data={data?.series || []}
              series={series}
              height={240}
              valueFormatter={usd}
            />
            {totals && (
              <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
                {t('Total for the range')}: {usd(shownTotal)}
                {!source && totals.calls != null
                  ? ` · ${totals.calls} ${t('AI calls')}`
                  : ''}
              </Typography>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
};

export default AiCostsPanel;
