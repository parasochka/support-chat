import { useEffect, useState } from 'react';
import { useNotify } from 'react-admin';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import FormControlLabel from '@mui/material/FormControlLabel';
import Link from '@mui/material/Link';
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';
import TextStats from '../components/TextStats';
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
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));

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
      notify('Prompt variables saved', { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
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
      notify('Escalation keywords saved', { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
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
      notify('Test profile saved', { type: 'success' });
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    } finally {
      setSavingProfile(false);
    }
  };

  return (
    <Box sx={{ maxWidth: 900 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Brand values substituted into the shared prompt template. Empty values
        fall back to the built-in defaults. The prompt wording itself is edited
        in <code>prompts.py</code> (see the read-only Prompt preview page). The
        Telegram retention persona has its own variables in{' '}
        <Link href="#/retention?tab=variables">
          Telegram · Retention → Prompt variables
        </Link>{' '}
        — a separate prompt: empty retention fields fall back to the built-in
        retention defaults, never to these support values.
      </Typography>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Prompt variables
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
            label="Total"
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
            />
          ))}
          <Button variant="contained" onClick={saveVars} disabled={savingVars} sx={{ mt: 1 }}>
            {savingVars ? 'Saving…' : 'Save variables'}
          </Button>
        </CardContent>
      </Card>

      {escalation && (
        <Card sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Escalation keyword lists
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              One entry per line; multilingual stems scan the player's raw
              message before the model call (soft hand-off, no tokens burned).
            </Typography>
            <TextField
              label="High-risk keywords (fraud / legal)"
              value={highRisk}
              onChange={(e) => setHighRisk(e.target.value)}
              fullWidth
              multiline
              minRows={6}
              margin="normal"
            />
            <TextField
              label="Human-request keywords"
              value={humanReq}
              onChange={(e) => setHumanReq(e.target.value)}
              fullWidth
              multiline
              minRows={6}
              margin="normal"
            />
            <Button variant="contained" onClick={saveKeywords} disabled={savingKw}>
              {savingKw ? 'Saving…' : 'Save keywords'}
            </Button>
          </CardContent>
        </Card>
      )}

      {profile && (
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Test player profile
            </Typography>
            {!profileActive && (
              <Alert severity="info" sx={{ mb: 1 }}>
                A handshake secret is configured — the host site supplies the
                player context, so this test profile is ignored at session
                create. To use this profile instead, clear the product's{' '}
                <Link href="#/structure">
                  Widget handshake secret in Structure
                </Link>{' '}
                (use its Clear button). A deploy-wide{' '}
                <code>WIDGET_HANDSHAKE_SECRET</code> env value can only be
                removed in Railway.
              </Alert>
            )}
            <FormControlLabel
              control={
                <Switch
                  checked={Boolean(profile.enabled)}
                  onChange={(e) => setProfile({ ...profile, enabled: e.target.checked })}
                />
              }
              label="Enabled (used when no handshake secret is set)"
            />
            {PROFILE_FIELDS.map((f) => (
              <TextField
                key={f}
                label={f}
                value={profile[f] ?? ''}
                onChange={(e) => setProfile({ ...profile, [f]: e.target.value })}
                fullWidth
                margin="dense"
              />
            ))}
            <Button
              variant="contained"
              onClick={saveProfile}
              disabled={savingProfile}
              sx={{ mt: 1 }}
            >
              {savingProfile ? 'Saving…' : 'Save test profile'}
            </Button>
          </CardContent>
        </Card>
      )}
    </Box>
  );
};

export default PromptVariables;
