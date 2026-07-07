import {
  Datagrid,
  DateField,
  Edit,
  List,
  SimpleForm,
  TextField,
  TextInput,
} from 'react-admin';

/** The admin-managed {placeholder} registry substituted into KB texts. */
export const KbVariableList = () => (
  <List perPage={50} exporter={false} title="KB variables">
    <Datagrid rowClick="edit" bulkActionButtons={false}>
      <TextField source="key" />
      <TextField source="value" />
      <TextField source="description" />
      <DateField source="updated_at" showTime />
      <TextField source="updated_by" />
    </Datagrid>
  </List>
);

export const KbVariableEdit = () => (
  <Edit mutationMode="pessimistic" title="Edit KB variable">
    <SimpleForm>
      <TextInput source="key" disabled />
      <TextInput source="description" fullWidth />
      <TextInput source="value" fullWidth multiline />
    </SimpleForm>
  </Edit>
);
