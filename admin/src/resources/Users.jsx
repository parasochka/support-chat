import { useCallback, useEffect, useState } from 'react';
import {
  BooleanField,
  BooleanInput,
  Create,
  Datagrid,
  DateField,
  Edit,
  FormDataConsumer,
  FunctionField,
  List,
  SelectInput,
  SimpleForm,
  TextField,
  TextInput,
  email,
  required,
  useNotify,
  useRecordContext,
  useRedirect,
  useRefresh,
} from 'react-admin';
import { useFormContext } from 'react-hook-form';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField_ from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import MobileList from '../components/MobileList';
import { API_URL, httpClient } from '../httpClient';
import { generatePassword } from '../lib/secrets';
import useIsMobile from '../lib/useIsMobile';
import { t } from '../i18n';
import { notifyError } from '../lib/notifyError';
import { fmtDateTime } from '../lib/fmt';

const ROLE_CHOICES = [
  { id: 'admin', name: t('admin (read + write)') },
  { id: 'manager', name: t('manager (read-only)') },
];

const SCOPE_CHOICES = [
  { id: 'global', name: t('Global (everything)') },
  { id: 'partner', name: t('Partner (all its products)') },
  { id: 'product', name: t('Single product') },
];

// One membership = role × scope. A user may hold several (e.g. manager on one
// product + admin on another); the effective role over a product is the best
// of global > owning partner > the product itself.
const membershipLabel = (m) => {
  if (m.scope_type === 'global') return `${t('Global')} · ${m.role}`;
  if (m.scope_type === 'partner')
    return `${t('Partner')} ${m.partner_name || `#${m.partner_id}`} · ${m.role}`;
  return `${t('Product')} ${m.product_name || `#${m.product_id}`} · ${m.role}`;
};

const membershipsSummary = (record) => {
  const ms = record?.memberships || [];
  if (!ms.length) return t('no access (no memberships)');
  return ms.map(membershipLabel).join(', ');
};

// Partner → Product structure for the scope pickers (same source as the header
// switcher). Scoped to what the caller may see.
const useStructure = () => {
  const [structure, setStructure] = useState(null);
  useEffect(() => {
    httpClient(`${API_URL}/admin/structure`)
      .then(({ json }) => setStructure(json))
      .catch(() => setStructure({ partners: [] }));
  }, []);
  const partners = structure?.partners || [];
  const products = partners.flatMap((pa) =>
    (pa.products || []).map((pr) => ({ ...pr, partner_name: pa.name }))
  );
  return { partners, products };
};

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
          secondaryText={(r) =>
            `${membershipsSummary(r)} · ${r.active ? t('active') : t('inactive')}`
          }
          tertiaryText={(r) =>
            fmtDateTime(r.created_at)
          }
          onRowClick={(id) => redirect('edit', 'users', id)}
        />
      ) : (
        <Datagrid rowClick="edit">
          <TextField source="email" label={t('Email')} />
          <FunctionField
            label={t('Access (role × scope)')}
            render={(r) => (
              <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                {(r.memberships || []).length === 0 && (
                  <Chip size="small" variant="outlined" color="warning"
                        label={t('no access (no memberships)')} />
                )}
                {(r.memberships || []).map((m) => (
                  <Chip
                    key={m.id}
                    size="small"
                    variant="outlined"
                    color={m.role === 'admin' ? 'primary' : 'default'}
                    label={membershipLabel(m)}
                  />
                ))}
              </Stack>
            )}
          />
          <BooleanField source="active" label={t('Active')} />
          <DateField source="created_at" label={t('Created')} showTime />
        </Datagrid>
      )}
    </List>
  );
};

// Grant/revoke panel inside the edit view. Talks to the dedicated membership
// endpoints directly (POST/DELETE /admin/users/{email}/memberships) — the
// server enforces that the caller holds an ADMIN role over the scope being
// granted or revoked, so a product admin can only hand out its own products.
const MembershipsPanel = () => {
  const record = useRecordContext();
  const notify = useNotify();
  const refresh = useRefresh();
  const { partners, products } = useStructure();
  const [form, setForm] = useState({ scope_type: 'product', partner_id: '', product_id: '', role: 'manager' });

  const memberships = record?.memberships || [];

  const grant = async () => {
    try {
      await httpClient(
        `${API_URL}/admin/users/${encodeURIComponent(record.email)}/memberships`,
        {
          method: 'POST',
          body: JSON.stringify({
            scope_type: form.scope_type,
            partner_id: form.scope_type === 'partner' ? Number(form.partner_id) : undefined,
            product_id: form.scope_type === 'product' ? Number(form.product_id) : undefined,
            role: form.role,
          }),
        }
      );
      notify(t('Access granted'), { type: 'success' });
      setForm({ scope_type: 'product', partner_id: '', product_id: '', role: 'manager' });
      refresh();
    } catch (e) {
      notifyError(notify, e, t('Save failed'));
    }
  };

  const revoke = async (m) => {
    if (!window.confirm(`${t('Revoke access')} “${membershipLabel(m)}”?`)) return;
    try {
      await httpClient(
        `${API_URL}/admin/users/${encodeURIComponent(record.email)}/memberships/${m.id}`,
        { method: 'DELETE' }
      );
      notify(t('Access revoked'), { type: 'success' });
      refresh();
    } catch (e) {
      notifyError(notify, e, t('Delete failed'));
    }
  };

  const grantDisabled =
    (form.scope_type === 'partner' && !form.partner_id) ||
    (form.scope_type === 'product' && !form.product_id);

  if (!record) return null;

  return (
    <Stack spacing={1.5} sx={{ width: '100%', mt: 2 }}>
      <Typography variant="h6">{t('Access (role × scope)')}</Typography>
      <Typography variant="body2" color="text.secondary">
        {t(
          'What this account may see and edit. Each row is one role over one scope: the whole hub (global), one partner (all its products), or a single product. Granting the same scope again replaces its role.'
        )}
      </Typography>
      {memberships.length === 0 && (
        <Alert severity="warning">
          {t('This account has no memberships — it can log in but sees no data. Grant it a scope below.')}
        </Alert>
      )}
      {memberships.length > 0 && (
        <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={{ maxWidth: 720, minWidth: 420 }}>
          <TableHead>
            <TableRow>
              <TableCell>{t('Scope')}</TableCell>
              <TableCell>{t('Role')}</TableCell>
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {memberships.map((m) => (
              <TableRow key={m.id} hover>
                <TableCell>
                  {m.scope_type === 'global' && t('Global (everything)')}
                  {m.scope_type === 'partner' &&
                    `${t('Partner')} · ${m.partner_name || `#${m.partner_id}`}`}
                  {m.scope_type === 'product' &&
                    `${t('Product')} · ${m.product_name || `#${m.product_id}`}`}
                </TableCell>
                <TableCell>
                  <Chip size="small" variant="outlined"
                        color={m.role === 'admin' ? 'primary' : 'default'} label={m.role} />
                </TableCell>
                <TableCell align="right">
                  <Button size="small" color="error" onClick={() => revoke(m)}>
                    {t('Revoke')}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        </Box>
      )}
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <TextField_
          select
          size="small"
          label={t('Scope')}
          value={form.scope_type}
          onChange={(e) =>
            setForm({ ...form, scope_type: e.target.value, partner_id: '', product_id: '' })
          }
          sx={{ minWidth: 200 }}
        >
          {SCOPE_CHOICES.map((c) => (
            <MenuItem key={c.id} value={c.id}>
              {c.name}
            </MenuItem>
          ))}
        </TextField_>
        {form.scope_type === 'partner' && (
          <TextField_
            select
            size="small"
            label={t('Partner')}
            value={form.partner_id}
            onChange={(e) => setForm({ ...form, partner_id: e.target.value })}
            sx={{ minWidth: 220 }}
          >
            {partners.map((pa) => (
              <MenuItem key={pa.id} value={pa.id}>
                {pa.name}
              </MenuItem>
            ))}
          </TextField_>
        )}
        {form.scope_type === 'product' && (
          <TextField_
            select
            size="small"
            label={t('Product')}
            value={form.product_id}
            onChange={(e) => setForm({ ...form, product_id: e.target.value })}
            sx={{ minWidth: 220 }}
          >
            {products.map((pr) => (
              <MenuItem key={pr.id} value={pr.id}>
                {pr.partner_name} · {pr.name}
              </MenuItem>
            ))}
          </TextField_>
        )}
        <TextField_
          select
          size="small"
          label={t('Role')}
          value={form.role}
          onChange={(e) => setForm({ ...form, role: e.target.value })}
          sx={{ minWidth: 200 }}
        >
          {ROLE_CHOICES.map((c) => (
            <MenuItem key={c.id} value={c.id}>
              {c.name}
            </MenuItem>
          ))}
        </TextField_>
        <Button variant="contained" size="small" onClick={grant} disabled={grantDisabled}>
          {t('Grant access')}
        </Button>
      </Stack>
      <Typography variant="caption" color="text.secondary">
        {t('You may grant or revoke only scopes you hold an admin role over. You cannot change your own memberships.')}
      </Typography>
    </Stack>
  );
};

export const UserEdit = () => (
  <Edit mutationMode="pessimistic" title={t('Edit admin user')}>
    <SimpleForm>
      <TextInput source="email" label={t('Email')} disabled />
      <BooleanInput source="active" label={t('Active')} />
      <PasswordWithGenerate
        label={t('New password (leave empty to keep)')}
        helperText={t('Minimum 8 characters. Set directly — there is no email reset flow.')}
      />
      <MembershipsPanel />
    </SimpleForm>
  </Edit>
);

export const UserCreate = () => {
  const { partners, products } = useStructure();
  return (
    <Create redirect="list" title={t('New admin user')}>
      <SimpleForm defaultValues={{ role: 'manager', scope_type: 'product' }}>
        <TextInput source="email" label={t('Email')} validate={[required(), email()]} />
        <PasswordWithGenerate label={t('Password')} validate={required()} />
        <SelectInput
          source="scope_type"
          label={t('Scope')}
          choices={SCOPE_CHOICES}
          validate={required()}
          helperText={t('What the account may access: the whole hub, one partner (all its products), or a single product. More scopes can be added after creation on the edit page.')}
        />
        <FormDataConsumer>
          {({ formData }) => (
            <>
              {formData.scope_type === 'partner' && (
                <SelectInput
                  source="partner_id"
                  label={t('Partner')}
                  choices={partners.map((pa) => ({ id: pa.id, name: pa.name }))}
                  validate={required()}
                />
              )}
              {formData.scope_type === 'product' && (
                <SelectInput
                  source="product_id"
                  label={t('Product')}
                  choices={products.map((pr) => ({
                    id: pr.id,
                    name: `${pr.partner_name} · ${pr.name}`,
                  }))}
                  validate={required()}
                />
              )}
            </>
          )}
        </FormDataConsumer>
        <SelectInput
          source="role"
          label={t('Role')}
          choices={ROLE_CHOICES}
          validate={required()}
          helperText={t('admin may edit within the scope; manager is read-only.')}
        />
      </SimpleForm>
    </Create>
  );
};
