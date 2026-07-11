import { useSearchParams } from 'react-router-dom';
import { Title } from 'react-admin';
import Box from '@mui/material/Box';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import PromptPreview from './PromptPreview';
import PromptVariables from './PromptVariables';
import RequireProduct from '../components/RequireProduct';
import { t } from '../i18n';

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
    <RequireProduct title={t('Prompt')}>
    <Box sx={{ p: 2 }}>
      <Title title={t('Prompt')} />
      <Tabs
        value={tab}
        onChange={(e, v) => setParams({ tab: v }, { replace: true })}
        variant="scrollable"
        allowScrollButtonsMobile
        sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}
      >
        <Tab value="preview" label={t('Preview')} />
        <Tab value="variables" label={t('Prompt variables')} />
      </Tabs>
      {tab === 'preview' ? <PromptPreview /> : <PromptVariables />}
    </Box>
    </RequireProduct>
  );
};

export default Prompt;
