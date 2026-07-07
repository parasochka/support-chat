import { useTheme } from '@mui/material/styles';
import Box from '@mui/material/Box';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

/**
 * Theme-aware recharts wrappers shared by the dashboard and the retention
 * analytics. One palette, assigned in FIXED order (never cycled), stepped per
 * theme mode so contrast holds on both surfaces — the same validated
 * categorical slots the hand-rolled LineChart panels use.
 */
export const CHART_COLORS = {
  light: ['#2a78d6', '#1baf7a', '#eda100', '#008300'],
  dark: ['#3987e5', '#199e70', '#c98500', '#008300'],
};

export const useChartColors = () => {
  const theme = useTheme();
  return CHART_COLORS[theme.palette.mode] || CHART_COLORS.dark;
};

const fmtDay = (iso) => {
  const d = new Date(iso);
  return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}`;
};

const EmptyNote = () => (
  <Box sx={{ py: 4, textAlign: 'center' }}>
    <Typography variant="body2" color="text.secondary">
      No data for the period.
    </Typography>
  </Box>
);

// Shared tooltip/axis styling so every recharts panel reads like one system.
const useChartChrome = () => {
  const theme = useTheme();
  return {
    grid: theme.palette.divider,
    text: theme.palette.text.secondary,
    tooltip: {
      backgroundColor: theme.palette.background.paper,
      border: `1px solid ${theme.palette.divider}`,
      borderRadius: 4,
      fontSize: 12,
      color: theme.palette.text.primary,
    },
  };
};

/**
 * Multi-series daily line chart over [{ <xKey>: 'YYYY-MM-DD', ...metrics }].
 * `series` = [{ key, label }] in fixed palette order; a legend appears for
 * two or more series (a single series is named by the panel title).
 */
export const SeriesLineChart = ({ data, series, xKey = 'date', height = 220 }) => {
  const colors = useChartColors();
  const chrome = useChartChrome();
  if (!data || !data.length) return <EmptyNote />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
        <CartesianGrid stroke={chrome.grid} vertical={false} />
        <XAxis
          dataKey={xKey}
          tickFormatter={fmtDay}
          tick={{ fontSize: 10, fill: chrome.text }}
          stroke={chrome.grid}
          tickLine={false}
        />
        <YAxis
          allowDecimals={false}
          tick={{ fontSize: 10, fill: chrome.text }}
          stroke={chrome.grid}
          tickLine={false}
          width={44}
        />
        <Tooltip labelFormatter={fmtDay} contentStyle={chrome.tooltip} />
        {series.length > 1 && (
          <Legend wrapperStyle={{ fontSize: 12 }} iconType="plainline" />
        )}
        {series.map((s, i) => (
          <Line
            key={s.key}
            type="monotone"
            dataKey={s.key}
            name={s.label}
            stroke={colors[i % colors.length]}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
};

/**
 * Small vertical bar chart for a short categorical distribution
 * (e.g. players per unlocked photo stage). One hue — magnitude, not identity.
 */
export const MiniBarChart = ({ data, xKey, yKey, label, height = 200 }) => {
  const colors = useChartColors();
  const chrome = useChartChrome();
  if (!data || !data.length) return <EmptyNote />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
        <CartesianGrid stroke={chrome.grid} vertical={false} />
        <XAxis
          dataKey={xKey}
          tick={{ fontSize: 10, fill: chrome.text }}
          stroke={chrome.grid}
          tickLine={false}
        />
        <YAxis
          allowDecimals={false}
          tick={{ fontSize: 10, fill: chrome.text }}
          stroke={chrome.grid}
          tickLine={false}
          width={44}
        />
        <Tooltip contentStyle={chrome.tooltip} cursor={{ fill: 'transparent' }} />
        <Bar
          dataKey={yKey}
          name={label}
          fill={colors[0]}
          radius={[4, 4, 0, 0]}
          maxBarSize={48}
        />
      </BarChart>
    </ResponsiveContainer>
  );
};

/**
 * Horizontal funnel bars: each step a bar scaled to the FIRST step, with the
 * count at the end and the step-to-step conversion under the label. Steps =
 * [{ label, value }] in order. Hand-rolled (recharts' category bars can't
 * carry the per-step conversion), but themed like the recharts panels.
 */
export const FunnelBars = ({ steps }) => {
  const colors = useChartColors();
  const rows = (steps || []).filter((s) => s.value != null);
  if (!rows.length) return <EmptyNote />;
  const max = Math.max(...rows.map((s) => s.value), 1);
  return (
    <Stack spacing={1.25}>
      {rows.map((s, i) => {
        const prev = i > 0 ? rows[i - 1].value : null;
        const conv = prev ? Math.round((s.value / prev) * 100) : null;
        return (
          <Box key={s.label}>
            <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.25 }}>
              <Typography variant="caption" color="text.secondary">
                {s.label}
                {conv != null && ` · ${conv}% of previous`}
              </Typography>
              <Typography variant="caption" sx={{ fontWeight: 600 }}>
                {s.value}
              </Typography>
            </Stack>
            <Box sx={{ bgcolor: 'action.hover', borderRadius: 1, height: 14 }}>
              <Box
                sx={{
                  width: `${Math.max((s.value / max) * 100, s.value ? 2 : 0)}%`,
                  height: '100%',
                  borderRadius: 1,
                  bgcolor: colors[0],
                  opacity: 1 - i * 0.08,
                }}
              />
            </Box>
          </Box>
        );
      })}
    </Stack>
  );
};
