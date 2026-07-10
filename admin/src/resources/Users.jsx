import { useState } from 'react';
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
  useRedirect,
} from 'react-admin';
import { useFormContext } from 'react-hook-form';
import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import MobileList from '../components/MobileList';
import { generatePassword } from '../lib/secrets';
import useIsMobile from '../lib/useIsMobile';
import { t } from '../i18n';

const ROLE_CHOICES = [
  { id: 'admin', name: t('admin (read + write)') },
  { id: 'manager', name: t('manager (read-only)') },
];

// Password input with a one-click generator. A generated password renders as
// visible text (the admin must copy it to hand it over — there is no email
// reset flow); a hand-typed one stays masked.
const PasswordWithGenerate = ({ source = 'password', label, helperText, validate }) => {
  const { setValue } = useFormContext();
  const [revealed, setRevealed] = useState(false);

  const generate = () => {
    setValue(source, generatePassword(), { shouldDirty: true, shouldValidate: true });
    setRevealed(true);
  };

  return (
    <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ width: '100%' }}>
      <TextInput
        source={source}
        type={revealed ? 'text' : 'password'}
        label={label}
        helperText={helperText}
        validate={validate}
        autoComplete="new-password"
        fullWidth
        onChange={() => setRevealed(false)}
      />
      <Button variant="outlined" onClick={generate} sx={{ whiteSpace: 'nowrap', mt: 1 }}>
        {t('Generate')}
      </Button>
    </Stack>
  );
};

export const UserList = () => {
  const isMobile = useIsMobile();
  const redirect = useRedirect();
  return (
    <List perPage={50} exporter={false} title={t('Admin users')}>
      {isMobile ? (
        <MobileList
          primaryText={(r) => r.email}
          secondaryText={(r) => `${r.role} · ${r.active ? 'active' : 'inactive'}`}
          tertiaryText={(r) =>
            r.created_at ? new Date(r.created_at).toLocaleString() : ''
          }
          onRowClick={(id) => redirect('edit', 'users', id)}
        />
      ) : (
        <Datagrid rowClick="edit">
          <TextField source="email" />
          <TextField source="role" />
          <BooleanField source="active" />
          <DateField source="created_at" showTime />
        </Datagrid>
      )}
    </List>
  );
};

export const UserEdit = () => (
  <Edit mutationMode="pessimistic" title={t('Edit admin user')}>
    <SimpleForm>
      <TextInput source="email" disabled />
      <SelectInput source="role" choices={ROLE_CHOICES} />
      <BooleanInput source="active" />
      <PasswordWithGenerate
        label={t('New password (leave empty to keep)')}
        helperText={t('Minimum 8 characters. Set directly — there is no email reset flow.')}
      />
    </SimpleForm>
  </Edit>
);

export const UserCreate = () => (
  <Create redirect="list" title={t('New admin user')}>
    <SimpleForm>
      <TextInput source="email" validate={[required(), email()]} />
      <PasswordWithGenerate validate={required()} />
      <SelectInput source="role" choices={ROLE_CHOICES} defaultValue="manager" />
    </SimpleForm>
  </Create>
);
