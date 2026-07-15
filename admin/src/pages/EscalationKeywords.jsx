import { useEffect, useState } from 'react';
import { Title, useNotify, usePermissions } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';
import RequireProduct from '../components/RequireProduct';
import { t } from '../i18n';

/**
 * The escalation keyword lists (the `escalation` settings group — content
 * tuning, edited here and ONLY here; the generic Settings surfaces skip the
 * group on purpose). Used to live as a block on the Prompt variables tab;
 * it is shared pre-model tuning rather than a prompt value, so it moved to
 * the Common sidebar section as its own page.
 */
const EscalationKeywordsInner = () => {
  const notify = useNotify();
  const { permissions } = usePermissions();
  const readOnly = permissions !== 'admin';

  const [highRisk, setHighRisk] = useState('');
  const [humanReq, setHumanReq] = useState('');
  const [escalation, setEscalation] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    httpClient(withProduct(`${API_URL}/admin/settings`))
      .then(({ json }) => {
        const esc = json.resolved?.escalation || {};
        setEscalation(esc);
        setHighRisk((esc.high_risk_keywords || []).join('\n'));
        setHumanReq((esc.human_request_keywords || []).join('\n'));
      })
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [notify]);

  const save = async () => {
    setSaving(true);
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
      setSaving(false);
    }
  };

  if (!escalation) return <Box sx={{ p: 2 }}>{t('Loading…')}</Box>;

  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('Escalation keywords')} />
      <Card>
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
          <Button variant="contained" onClick={save} disabled={saving || readOnly}>
            {saving ? t('Saving…') : t('Save keywords')}
          </Button>
        </CardContent>
      </Card>
    </Box>
  );
};

const EscalationKeywords = () => (
  <RequireProduct title={t('Escalation keywords')}>
    <EscalationKeywordsInner />
  </RequireProduct>
);

export default EscalationKeywords;
