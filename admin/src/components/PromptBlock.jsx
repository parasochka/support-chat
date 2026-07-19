import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Typography from '@mui/material/Typography';
import TextStats from './TextStats';

// Read-only prompt-preview card (title + token stats + monospace body),
// shared by the support Prompt → Preview page and the Retention prompt tab.
const PromptBlock = ({ title, text }) => (
  <Card sx={{ mb: 2 }}>
    <CardContent>
      <Typography variant="h6" gutterBottom>
        {title}
      </Typography>
      <TextStats text={text || ''} sx={{ mb: 1 }} />
      <Typography
        component="pre"
        sx={{
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
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

export default PromptBlock;
