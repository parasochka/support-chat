import { useEffect, useState } from 'react';
import { useNotify } from 'react-admin';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';
import TextStats from '../components/TextStats';
import rich from '../components/Rich';
import { t } from '../i18n';
import { notifyError } from '../lib/notifyError';
import { useReadOnly } from '../lib/useReadOnly';

/**
 * The Prompt → variables surface: the brand prompt variables substituted into
 * the shared template. The escalation keyword lists and the test player
 * profile that used to live here moved to their own pages in the Common
 * sidebar section (they are shared tuning, not prompt values).
 *
 * Rendered as the Variables tab of the Prompt page (pages/Prompt.jsx).
 */
const PromptVariables = () => {
  const notify = useNotify();
  // Managers are read-only server-side (403 on PUT) — pre-disable the editors
  // instead of letting them type and lose the edit, matching SiteMap.
  const readOnly = useReadOnly();

  const [vars, setVars] = useState([]);
  const [values, setValues] = useState({});
  const [savingVars, setSavingVars] = useState(false);

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
      notifyError(notify, e, t('Save failed'));
    } finally {
      setSavingVars(false);
    }
  };

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {rich(
          t(
            'Brand values substituted into the shared prompt template. Empty values fall back to the built-in defaults. The prompt wording itself is edited in `prompts.py` (see the read-only Prompt preview page). The Telegram retention persona has its own variables in [Retention → Prompt variables](#/retention?tab=variables) — a separate prompt: empty retention fields fall back to the built-in retention defaults, never to these support values.'
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
    </Box>
  );
};

export default PromptVariables;
