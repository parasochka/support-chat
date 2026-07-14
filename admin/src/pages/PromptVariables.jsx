import { useEffect, useState } from 'react';
import { useNotify, usePermissions } from 'react-admin';
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
import TextStats from '../components/TextStats';
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
 * The Prompt → variables surface, mirroring the legacy SPA sub-tab: the brand
 * prompt variables, the escalation keyword lists (the `escalation` settings
 * group — content tuning, edited here and only here), and the test player
 * profile that stands in for the host-site handshake in dev.
 *
 * Rendered as the Variables tab of the Prompt page (pages/Prompt.jsx).
 */
const PromptVariables = () => {
  const notify = useNotify();
  // Managers are read-only server-side (403 on PUT) — pre-disable the editors
  // instead of letting them type and lose the edit, matching SiteMap.
  const { permissions } = usePermissions();
  const readOnly = permissions !== 'admin';

  // --- prompt variables ---
  const [vars, setVars] = useState([]);
  const [values, setValues] = useState({});
  const [savingVars, setSavingVars] = useState(false);

  // --- escalation keyword lists ---
  const [highRisk, setHighRisk] = useState('');
  const [humanReq, setHumanReq] = useState('');
  const [escalation, setEscalation] = useState(null);
  const [savingKw, setSavingKw] = useState(false);

  // --- test player profile ---
  const [profile, setProfile] = useState(null);
  const [profileActive, setProfileActive] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);

  useEffect(() => {
    httpClient(withProduct(`${API_URL}/admin/prompt-variables`))
      .then(({ json }) => {
        setVars(json.variables || []);
        const v = {};
        (json.variables || []).forEach((x) => {
          v[x.key] = x.value ?? '';
        });
        setValues(v);
      })
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));

    httpClient(withProduct(`${API_URL}/admin/settings`))
      .then(({ json }) => {
        const esc = json.resolved?.escalation || {};
        setEscalation(esc);
        setHighRisk((esc.high_risk_keywords || []).join('\n'));
        setHumanReq((esc.human_request_keywords || []).join('\n'));
      })
      .catch(() => setEscalation(null));

    httpClient(withProduct(`${API_URL}/admin/test-profile`))
      .then(({ json }) => {
        setProfile(json.profile || {});
        setProfileActive(Boolean(json.active));
      })
      .catch(() => setProfile(null));
  }, [notify]);

  const saveVars = async () => {
    setSavingVars(true);
    try {
      await httpClient(withProduct(`${API_URL}/admin/prompt-variables`), {
        method: 'PUT',
        body: JSON.stringify({ value: values }),
      });
      notify(t('Prompt variables saved'), { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    } finally {
      setSavingVars(false);
    }
  };

  const saveKeywords = async () => {
    setSavingKw(true);
    const lines = (s) =>
      s
        .split('\n')
        .map((x) => x.trim())
        .filter(Boolean);
    try {
      await httpClient(withProduct(`${API_URL}/admin/settings/escalation`), {
        method: 'PUT',
        body: JSON.stringify({
          value: {
            ...escalation,
            high_risk_keywords: lines(highRisk),
            human_request_keywords: lines(humanReq),
          },
        }),
      });
      notify(t('Escalation keywords saved'), { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    } finally {
      setSavingKw(false);
    }
  };

  const saveProfile = async () => {
    setSavingProfile(true);
    try {
      await httpClient(withProduct(`${API_URL}/admin/test-profile`), {
        method: 'PUT',
        body: JSON.stringify({ value: profile }),
      });
      notify(t('Test profile saved'), { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    } finally {
      setSavingProfile(false);
    }
  };

  return (
    <Box sx={{ maxWidth: 900 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {rich(
          t(
            'Brand values substituted into the shared prompt template. Empty values fall back to the built-in defaults. The prompt wording itself is edited in `prompts.py` (see the read-only Prompt preview page). The Telegram retention persona has its own variables in [Telegram · Retention → Prompt variables](#/retention?tab=variables) — a separate prompt: empty retention fields fall back to the built-in retention defaults, never to these support values.'
          )
        )}
      </Typography>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('Prompt variables')}
          </Typography>
          <Alert severity="info" sx={{ mb: 1 }}>
            <b>{t('English only')}.</b>{' '}
            {t(
              'Model-facing content must be in English — the backend rejects other scripts. Player-facing copy belongs in Translations.'
            )}
          </Alert>
          {/* Combined volume of the values as they will render into the prompt
              (an empty field contributes its default). */}
          <TextStats
            label={t('Total')}
            text={vars.map((v) => values[v.key] || v.default || '')}
          />
          {vars.map((v) => (
            <TextField
              key={v.key}
              label={v.key}
              helperText={v.description}
              value={values[v.key] ?? ''}
              onChange={(e) => setValues({ ...values, [v.key]: e.target.value })}
              fullWidth
              multiline
              margin="normal"
              placeholder={v.default}
              disabled={readOnly}
            />
          ))}
          <Button variant="contained" onClick={saveVars} disabled={savingVars || readOnly} sx={{ mt: 1 }}>
            {savingVars ? t('Saving…') : t('Save variables')}
          </Button>
        </CardContent>
      </Card>

      {escalation && (
        <Card sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              {t('Escalation keyword lists')}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              {t(
                "One entry per line; multilingual stems scan the player's raw message before the model call (soft hand-off, no tokens burned)."
              )}
            </Typography>
            <TextField
              label={t('High-risk keywords (fraud / legal)')}
              value={highRisk}
              onChange={(e) => setHighRisk(e.target.value)}
              fullWidth
              multiline
              minRows={6}
              margin="normal"
              disabled={readOnly}
            />
            <TextField
              label={t('Human-request keywords')}
              value={humanReq}
              onChange={(e) => setHumanReq(e.target.value)}
              fullWidth
              multiline
              minRows={6}
              margin="normal"
              disabled={readOnly}
            />
            <Button variant="contained" onClick={saveKeywords} disabled={savingKw || readOnly}>
              {savingKw ? t('Saving…') : t('Save keywords')}
            </Button>
          </CardContent>
        </Card>
      )}

      {profile && (
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              {t('Test player profile')}
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
              onClick={saveProfile}
              disabled={savingProfile || readOnly}
              sx={{ mt: 1 }}
            >
              {savingProfile ? t('Saving…') : t('Save test profile')}
            </Button>
          </CardContent>
        </Card>
      )}
    </Box>
  );
};

export default PromptVariables;
