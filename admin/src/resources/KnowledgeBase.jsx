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
import Typography from '@mui/material/Typography';

const TITLE_LANGS = ['en', 'ru', 'es', 'tr', 'pt'];

const TopicForm = ({ isCreate = false }) => (
  <SimpleForm>
    <TextInput
      source="slug"
      validate={required()}
      disabled={!isCreate}
      helperText="Stable topic identifier (e.g. deposits). Cannot change after create."
    />
    <Typography variant="subtitle2" sx={{ mt: 1 }}>
      Topic title per language
    </Typography>
    {TITLE_LANGS.map((lang) => (
      <TextInput key={lang} source={`title.${lang}`} label={`Title (${lang})`} />
    ))}
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
  <List perPage={25} exporter={false} title="Knowledge base topics">
    <Datagrid rowClick="edit" bulkActionButtons={false}>
      <NumberField source="id" />
      <TextField source="slug" />
      <TextField source="title.en" label="Title (en)" />
      <NumberField source="order" label="Order" />
      <BooleanField source="active" />
      <NumberField source="entry_count" label="Has KB" />
    </Datagrid>
  </List>
);

export const KbEdit = () => (
  <Edit mutationMode="pessimistic" title="Edit topic + KB">
    <TopicForm />
  </Edit>
);

export const KbCreate = () => (
  <Create redirect="list" title="New topic">
    <TopicForm isCreate />
  </Create>
);
