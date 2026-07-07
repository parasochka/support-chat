import {
  BooleanField,
  BooleanInput,
  Create,
  Datagrid,
  DateField,
  Edit,
  List,
  SelectInput,
  SimpleForm,
  TextField,
  TextInput,
  email,
  required,
} from 'react-admin';

const ROLE_CHOICES = [
  { id: 'admin', name: 'admin (read + write)' },
  { id: 'manager', name: 'manager (read-only)' },
];

export const UserList = () => (
  <List perPage={50} exporter={false} title="Admin users">
    <Datagrid rowClick="edit">
      <TextField source="email" />
      <TextField source="role" />
      <BooleanField source="active" />
      <DateField source="created_at" showTime />
    </Datagrid>
  </List>
);

export const UserEdit = () => (
  <Edit mutationMode="pessimistic" title="Edit admin user">
    <SimpleForm>
      <TextInput source="email" disabled />
      <SelectInput source="role" choices={ROLE_CHOICES} />
      <BooleanInput source="active" />
      <TextInput
        source="password"
        type="password"
        label="New password (leave empty to keep)"
        helperText="Minimum 8 characters. Set directly — there is no email reset flow."
      />
    </SimpleForm>
  </Edit>
);

export const UserCreate = () => (
  <Create redirect="list" title="New admin user">
    <SimpleForm>
      <TextInput source="email" validate={[required(), email()]} />
      <TextInput source="password" type="password" validate={required()} />
      <SelectInput source="role" choices={ROLE_CHOICES} defaultValue="manager" />
    </SimpleForm>
  </Create>
);
