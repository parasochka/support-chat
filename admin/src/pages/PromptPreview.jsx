import { useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { withProduct } from '../productScope';

const Block = ({ title, text }) => (
  <Card sx={{ mb: 2 }}>
    <CardContent>
      <Typography variant="h6" gutterBottom>
        {title}
      </Typography>
      <Typography
        component="pre"
        sx={{
          whiteSpace: 'pre-wrap',
          fontFamily: 'monospace',
          fontSize: 13,
          m: 0,
        }}
      >
        {text || '—'}
      </Typography>
    </CardContent>
  </Card>
);

/**
 * Read-only view of the whole assembled prompt (GET /admin/effective-prompt):
 * the system message (Layer 1 core + static directives + Layer 2 KB) and the
 * Layer-3 user message, prompt variables already substituted. The wording
 * itself lives in prompts.py and is not editable here by design.
 */
const PromptPreview = () => {
  const [preview, setPreview] = useState(null);
  const notify = useNotify();

  useEffect(() => {
    httpClient(withProduct(`${API_URL}/admin/effective-prompt`))
      .then(({ json }) => setPreview(json.effective_preview))
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [notify]);

  return (
    <Box sx={{ p: 2, maxWidth: 1000 }}>
      <Title title="Prompt preview" />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        The complete prompt as the model receives it (read-only; example topic:{' '}
        {preview?.example?.topic || '—'}, language: {preview?.example?.lang || '—'}).
        To change the wording, edit <code>prompts.py</code> and redeploy; the
        brand values are on the Prompt variables page.
      </Typography>
      <Block
        title="System message (Layer 1 core + directives + Layer 2 KB)"
        text={preview?.system}
      />
      <Block title="User message (Layer 3 dynamic directives)" text={preview?.user} />
    </Box>
  );
};

export default PromptPreview;
