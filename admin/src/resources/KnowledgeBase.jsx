import {
  BooleanField,
  BooleanInput,
  Create,
  Datagrid,
  Edit,
  List,
  NumberField,
  NumberInput,
  SimpleForm,
  TextField,
  TextInput,
  required,
  useRedirect,
} from 'react-admin';
import { useWatch } from 'react-hook-form';
import Alert from '@mui/material/Alert';
import Link from '@mui/material/Link';
import MobileList from '../components/MobileList';
import TextStats from '../components/TextStats';
import { t } from '../i18n';
import RouteTabs from '../components/RouteTabs';
import RequireProduct from '../components/RequireProduct';
import useIsMobile from '../lib/useIsMobile';
import { KB_TABS } from './kbTabs';

/**
 * Topic titles are single-sourced: the per-language names live in the
 * Translations → Topic names tab (the player-facing copy registry), NOT here.
 * The KB form keeps only the canonical English title (the fallback the model
 * and other languages inherit) so the two surfaces can't drift apart — the KB
 * form is for the knowledge-base TEXT, which is what actually feeds the prompt.
 */
// Live character / ≈token / cost line over the KB editor — the KB text is
// Layer 2 of every prompt in this topic, so its volume is prompt volume.
const KbContentStats = () => {
  const content = useWatch({ name: 'content' });
  return <TextStats text={content || ''} />;
};

const TopicForm = ({ isCreate = false }) => (
  <SimpleForm>
    <TextInput
      source="slug"
      validate={required()}
      disabled={!isCreate}
      helperText={t('Stable topic identifier (e.g. deposits). Cannot change after create.')}
    />
    <TextInput
      source="title.en"
      label={t('Topic title (English)')}
      validate={required()}
      helperText={t('The canonical title and the fallback for every language.')}
    />
    <Alert severity="info" sx={{ mb: 1 }}>
      {t('Translate the title into other languages in')}{' '}
      <Link href="#/translations">{t('Translations')} → {t('Topic names')}</Link>.{' '}
      {t('The prompt itself is English-only, so only this English title feeds the model.')}
    </Alert>
    <NumberInput source="order" label={t('Display order')} defaultValue={0} />
    <BooleanInput source="active" label={t('Active')} defaultValue />
    <Alert severity="info" sx={{ mb: 0.5, alignSelf: 'stretch' }}>
      <b>{t('English only')}.</b>{' '}
      {t(
        'Model-facing content must be in English — the backend rejects other scripts. Player-facing copy belongs in Translations.'
      )}
    </Alert>
    <KbContentStats />
    <TextInput
      source="content"
      label={t('KB content')}
      multiline
      minRows={16}
      fullWidth
      helperText={t("The topic's knowledge base text (Layer 2). {placeholders} are substituted from KB variables. Clearing the field removes the entry.")}
    />
  </SimpleForm>
);

export const KbList = () => {
  const isMobile = useIsMobile();
  const redirect = useRedirect();
  return (
    <RequireProduct title={t('Knowledge base')}>
      <RouteTabs tabs={KB_TABS} />
      <List perPage={25} exporter={false} title={t('Knowledge base')}>
        {isMobile ? (
          <MobileList
            primaryText={(r) => r.title?.en || r.slug}
            secondaryText={(r) => r.slug}
            tertiaryText={(r) =>
              `${t('order')} ${r.order ?? 0} · ${r.active ? t('active') : t('inactive')} · ${t('KB')} ${r.entry_count ?? 0}`
            }
            onRowClick={(id) => redirect('edit', 'kb', id)}
          />
        ) : (
          <Datagrid rowClick="edit" bulkActionButtons={false}>
            <NumberField source="id" />
            <TextField source="slug" />
            <TextField source="title.en" label={t('Title (en)')} />
            <NumberField source="order" label={t('Order')} />
            <BooleanField source="active" label={t('Active')} />
            <NumberField source="entry_count" label={t('Has KB')} />
          </Datagrid>
        )}
      </List>
    </RequireProduct>
  );
};

export const KbEdit = () => (
  <RequireProduct title={t('Knowledge base')}>
    <Edit mutationMode="pessimistic" title={t('Edit topic + KB')}>
      <TopicForm />
    </Edit>
  </RequireProduct>
);

export const KbCreate = () => (
  <RequireProduct title={t('Knowledge base')}>
    <Create redirect="list" title={t('New topic')}>
      <TopicForm isCreate />
    </Create>
  </RequireProduct>
);
