import { useEffect, useState } from 'react';
import { Title, useTheme } from 'react-admin';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import Stack from '@mui/material/Stack';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Typography from '@mui/material/Typography';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import LightModeIcon from '@mui/icons-material/LightMode';

import { API_URL, httpClient } from '../httpClient';
import { getAdminLang, setAdminLang, t } from '../i18n';
import { fmtDateTime } from '../lib/fmt';

const fmtDate = (iso) => fmtDateTime(iso) || '—';

// Human label for one membership scope row.
const scopeLabel = (m) => {
  if (m.scope_type === 'global') return t('Global (whole hub)');
  if (m.scope_type === 'partner')
    return `${t('Partner')}: ${m.partner_name || m.partner_id}`;
  return `${t('Product')}: ${m.product_name || m.product_id}`;
};

const Row = ({ label, children }) => (
  <Stack
    direction={{ xs: 'column', sm: 'row' }}
    spacing={{ xs: 0.25, sm: 2 }}
    sx={{ py: 1 }}
  >
    <Typography
      variant="body2"
      color="text.secondary"
      sx={{ minWidth: { sm: 180 }, flexShrink: 0 }}
    >
      {label}
    </Typography>
    <Box sx={{ minWidth: 0 }}>{children}</Box>
  </Stack>
);

const Account = () => {
  const [me, setMe] = useState(null);
  const [theme, setTheme] = useTheme();
  const lang = getAdminLang();
  // useTheme() returns undefined until a choice is stored; the app default is
  // dark, so treat "unset" as dark for the toggle's active state.
  const themeValue = theme === 'light' ? 'light' : 'dark';

  useEffect(() => {
    httpClient(`${API_URL}/admin/me`)
      .then(({ json }) => setMe(json))
      .catch(() => setMe(null));
  }, []);

  return (
    <Box>
      <Title title={t('Account & appearance')} />

      <Card sx={{ mt: 2, mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('Account')}
          </Typography>
          <Divider sx={{ mb: 1 }} />
          <Row label={t('Email')}>
            <Typography sx={{ wordBreak: 'break-all' }}>
              {me?.email || '—'}
            </Typography>
          </Row>
          <Row label={t('Status')}>
            <Chip
              size="small"
              color={me?.active === false ? 'default' : 'success'}
              label={me?.active === false ? t('Inactive') : t('Active')}
            />
          </Row>
          <Row label={t('Role')}>
            <Chip
              size="small"
              variant="outlined"
              color={me?.can_write ? 'primary' : 'default'}
              label={me?.can_write ? t('Administrator') : t('Manager (read-only)')}
            />
          </Row>
          <Row label={t('Registered')}>
            <Typography>{fmtDate(me?.created_at)}</Typography>
          </Row>
          <Row label={t('Access (groups)')}>
            <Stack spacing={0.75} sx={{ alignItems: 'flex-start' }}>
              {(me?.memberships || []).length === 0 && (
                <Typography color="text.secondary">{t('No memberships')}</Typography>
              )}
              {(me?.memberships || []).map((m) => (
                <Chip
                  key={m.id}
                  size="small"
                  variant="outlined"
                  label={`${scopeLabel(m)} · ${m.role}`}
                  sx={{ maxWidth: '100%', height: 'auto', '& .MuiChip-label': { whiteSpace: 'normal', py: 0.4 } }}
                />
              ))}
            </Stack>
          </Row>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('Appearance')}
          </Typography>
          <Divider sx={{ mb: 1.5 }} />
          <Row label={t('Theme')}>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={themeValue}
              onChange={(e, v) => v && setTheme(v)}
            >
              <ToggleButton value="light">
                <LightModeIcon fontSize="small" sx={{ mr: 0.75 }} />
                {t('Light')}
              </ToggleButton>
              <ToggleButton value="dark">
                <DarkModeIcon fontSize="small" sx={{ mr: 0.75 }} />
                {t('Dark')}
              </ToggleButton>
            </ToggleButtonGroup>
          </Row>
          <Row label={t('Admin language')}>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={lang}
              onChange={(e, v) => v && v !== lang && setAdminLang(v)}
            >
              <ToggleButton value="en">EN</ToggleButton>
              <ToggleButton value="ru">RU</ToggleButton>
            </ToggleButtonGroup>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
              {t('Switching the language reloads the panel.')}
            </Typography>
          </Row>
        </CardContent>
      </Card>
    </Box>
  );
};

export default Account;
