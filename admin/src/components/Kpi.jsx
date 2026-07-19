import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Grid from '@mui/material/Grid';
import Typography from '@mui/material/Typography';
import { t } from '../i18n';

// KPI tile shared by the dashboard and Retention → Analytics; `size` is the
// MUI Grid size prop (the two surfaces use different column layouts).
export const Kpi = ({ label, value, hint, size = { xs: 6, sm: 4, md: 3, lg: 2 } }) => (
  <Grid size={size}>
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

// The retention entry funnel (deeplink → hand-off) — ONE definition; the
// dashboard block and Retention → Analytics render the same steps.
export const RETENTION_FUNNEL_STEPS = [
  ['deeplinks_created', t('Deeplinks minted')],
  ['starts', t('/start redemptions')],
  ['new_users', t('New linked players')],
  ['subscribed', t('Subscribed to channel')],
  ['engaged', t('Engaged (wrote a message)')],
  ['photo_receivers', t('Received a photo')],
  ['handoffs', t('Handed off')],
];

// Retention timeseries series: Analytics shows all four; the dashboard block
// slices to the first/last pair it charts.
export const RETENTION_TIMESERIES_SERIES = [
  { key: 'messages', label: t('Messages') },
  { key: 'active_users', label: t('Active players') },
  { key: 'photos', label: t('Photos') },
  { key: 'pings', label: t('Pings') },
];
