import { useEffect, useState } from 'react';
import { Title, useNotify, usePermissions } from 'react-admin';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import FormControlLabel from '@mui/material/FormControlLabel';
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';
import RequireProduct from '../components/RequireProduct';
import rich from '../components/Rich';
import { t } from '../i18n';

const PROFILE_FIELDS = [
  'id',
  'full_name',
  'email',
  'activation_status',
  'country',
  'balance',
  'vip_level',
  'registration_date',
];

/**
 * The test player profile that stands in for the host-site handshake in
 * dev/test (no WIDGET_HANDSHAKE_SECRET): the Layer-3 player data the model
 * sees, so the owner can test name personalization. Used to live as a block
 * on the Prompt variables tab; it moved to the Common sidebar section as its
 * own page.
 */
const TestProfileInner = () => {
  const notify = useNotify();
  const { permissions } = usePermissions();
  const readOnly = permissions !== 'admin';

  const [profile, setProfile] = useState(null);
  const [profileActive, setProfileActive] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    httpClient(withProduct(`${API_URL}/admin/test-profile`))
      .then(({ json }) => {
        setProfile(json.profile || {});
        setProfileActive(Boolean(json.active));
      })
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [notify]);

  const save = async () => {
    setSaving(true);
    try {
      await httpClient(withProduct(`${API_URL}/admin/test-profile`), {
        method: 'PUT',
        body: JSON.stringify({ value: profile }),
      });
      notify(t('Test profile saved'), { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  if (!profile) return <Box sx={{ p: 2 }}>{t('Loading…')}</Box>;

  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('Test player profile')} />
      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('Test player profile')}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {t(
              'Stands in for the host-site handshake in dev/test: the player data the model sees (name personalization, VIP level, balance). Ignored when a handshake secret is configured — the real site supplies the context then.'
            )}
          </Typography>
          {!profileActive && (
            <Alert severity="info" sx={{ mb: 1 }}>
              {rich(
                t(
                  "A handshake secret is configured — the host site supplies the player context, so this test profile is ignored at session create. To use this profile instead, clear the product's [Widget handshake secret in Structure](#/structure) (use its Clear button). A deploy-wide `WIDGET_HANDSHAKE_SECRET` env value can only be removed in Railway."
                )
              )}
            </Alert>
          )}
          <FormControlLabel
            control={
              <Switch
                checked={Boolean(profile.enabled)}
                onChange={(e) => setProfile({ ...profile, enabled: e.target.checked })}
                disabled={readOnly}
              />
            }
            label={t('Enabled (used when no handshake secret is set)')}
          />
          {PROFILE_FIELDS.map((f) => (
            <TextField
              key={f}
              label={f}
              value={profile[f] ?? ''}
              onChange={(e) => setProfile({ ...profile, [f]: e.target.value })}
              fullWidth
              margin="dense"
              disabled={readOnly}
            />
          ))}
          <Button
            variant="contained"
            onClick={save}
            disabled={saving || readOnly}
            sx={{ mt: 1 }}
          >
            {saving ? t('Saving…') : t('Save test profile')}
          </Button>
        </CardContent>
      </Card>
    </Box>
  );
};

const TestProfile = () => (
  <RequireProduct title={t('Test player profile')}>
    <TestProfileInner />
  </RequireProduct>
);

export default TestProfile;
