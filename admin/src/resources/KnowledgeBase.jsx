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
} from 'react-admin';
import Alert from '@mui/material/Alert';
import Link from '@mui/material/Link';
import RouteTabs from '../components/RouteTabs';
import RequireProduct from '../components/RequireProduct';
import { KB_TABS } from './kbTabs';

/**
 * Topic titles are single-sourced: the per-language names live in the
 * Translations → Topic names tab (the player-facing copy registry), NOT here.
 * The KB form keeps only the canonical English title (the fallback the model
 * and other languages inherit) so the two surfaces can't drift apart — the KB
 * form is for the knowledge-base TEXT, which is what actually feeds the prompt.
 */
const TopicForm = ({ isCreate = false }) => (
  <SimpleForm>
    <TextInput
      source="slug"
      validate={required()}
      disabled={!isCreate}
      helperText="Stable topic identifier (e.g. deposits). Cannot change after create."
    />
    <TextInput
      source="title.en"
      label="Topic title (English)"
      validate={required()}
      helperText="The canonical title and the fallback for every language."
    />
    <Alert severity="info" sx={{ mb: 1 }}>
      Translate the title into other languages in{' '}
      <Link href="#/translations">Translations → Topic names</Link>. The prompt
      itself is English-only, so only this English title feeds the model.
    </Alert>
    <NumberInput source="order" label="Display order" defaultValue={0} />
    <BooleanInput source="active" defaultValue />
    <TextInput
      source="content"
      label="KB content"
      multiline
      minRows={16}
      fullWidth
      helperText="The topic's knowledge base text (Layer 2). {placeholders} are substituted from KB variables. Clearing the field removes the entry."
    />
  </SimpleForm>
);

export const KbList = () => (
  <RequireProduct title="Knowledge base">
    <RouteTabs tabs={KB_TABS} />
    <List perPage={25} exporter={false} title="Knowledge base">
      <Datagrid rowClick="edit" bulkActionButtons={false}>
        <NumberField source="id" />
        <TextField source="slug" />
        <TextField source="title.en" label="Title (en)" />
        <NumberField source="order" label="Order" />
        <BooleanField source="active" />
        <NumberField source="entry_count" label="Has KB" />
      </Datagrid>
    </List>
  </RequireProduct>
);

export const KbEdit = () => (
  <RequireProduct title="Knowledge base">
    <Edit mutationMode="pessimistic" title="Edit topic + KB">
      <TopicForm />
    </Edit>
  </RequireProduct>
);

export const KbCreate = () => (
  <RequireProduct title="Knowledge base">
    <Create redirect="list" title="New topic">
      <TopicForm isCreate />
    </Create>
  </RequireProduct>
);
