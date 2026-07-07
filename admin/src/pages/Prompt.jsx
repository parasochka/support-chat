import { useSearchParams } from 'react-router-dom';
import { Title } from 'react-admin';
import Box from '@mui/material/Box';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import PromptPreview from './PromptPreview';
import PromptVariables from './PromptVariables';

/**
 * The Prompt surface: a read-only Preview of the whole assembled prompt and
 * the editable Prompt variables (brand values + escalation keywords + test
 * profile) as two tabs of one page — they describe the same template. The
 * active tab rides in the ?tab= query param so it survives reloads.
 */
const Prompt = () => {
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'variables' ? 'variables' : 'preview';

  return (
    <Box sx={{ p: 2 }}>
      <Title title="Prompt" />
      <Tabs
        value={tab}
        onChange={(e, v) => setParams({ tab: v }, { replace: true })}
        sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}
      >
        <Tab value="preview" label="Preview" />
        <Tab value="variables" label="Prompt variables" />
      </Tabs>
      {tab === 'preview' ? <PromptPreview /> : <PromptVariables />}
    </Box>
  );
};

export default Prompt;
