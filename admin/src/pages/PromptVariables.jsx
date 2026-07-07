import { useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';

/**
 * Prompt variables — the brand-uniquification values ({persona_name},
 * {brand_name}, {tone_of_voice}, …) rendered into the prompt template.
 * GET/PUT /admin/prompt-variables. The prompt WORDING itself lives in
 * prompts.py and is not editable here by design.
 */
const PromptVariables = () => {
  const [vars, setVars] = useState([]);
  const [values, setValues] = useState({});
  const [saving, setSaving] = useState(false);
  const notify = useNotify();

  useEffect(() => {
    httpClient(`${API_URL}/admin/prompt-variables`)
      .then(({ json }) => {
        setVars(json.variables || []);
        const v = {};
        (json.variables || []).forEach((x) => {
          v[x.key] = x.value ?? '';
        });
        setValues(v);
      })
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [notify]);

  const save = async () => {
    setSaving(true);
    try {
      await httpClient(`${API_URL}/admin/prompt-variables`, {
        method: 'PUT',
        body: JSON.stringify({ value: values }),
      });
      notify('Prompt variables saved', { type: 'success' });
    } catch (e) {
      notify(e.message || 'Save failed', { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box sx={{ p: 2, maxWidth: 900 }}>
      <Title title="Prompt variables" />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Brand values substituted into the shared prompt template. Empty values
        fall back to the built-in defaults. The prompt wording itself is edited
        in <code>prompts.py</code> and redeployed.
      </Typography>
      <Card>
        <CardContent>
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
          <Button variant="contained" onClick={save} disabled={saving} sx={{ mt: 2 }}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </CardContent>
      </Card>
    </Box>
  );
};

export default PromptVariables;
