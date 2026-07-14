import {
  Datagrid,
  DateField,
  Edit,
  List,
  SimpleForm,
  TextField,
  TextInput,
  usePermissions,
  useRedirect,
} from 'react-admin';
import MobileList from '../components/MobileList';
import RouteTabs from '../components/RouteTabs';
import RequireProduct from '../components/RequireProduct';
import useIsMobile from '../lib/useIsMobile';
import { KB_TABS } from './kbTabs';
import { CONTENT_TABS } from '../contentTabs';
import { t } from '../i18n';

/**
 * The admin-managed {placeholder} registry substituted into KB texts. It is
 * one surface with the Knowledge base (the values belong to the KB texts), so
 * both lists share the Topics / Variables tab strip.
 */
export const KbVariableList = () => {
  const isMobile = useIsMobile();
  const redirect = useRedirect();
  return (
    <RequireProduct title={t('Knowledge base · variables')}>
      <RouteTabs tabs={CONTENT_TABS} />
      <RouteTabs tabs={KB_TABS} />
      <List perPage={50} exporter={false} title={t('Knowledge base · variables')}>
        {isMobile ? (
          <MobileList
            primaryText={(r) => r.key}
            secondaryText={(r) => r.value || ''}
            tertiaryText={(r) => r.description || ''}
            onRowClick={(id) => redirect('edit', 'kb_variables', id)}
          />
        ) : (
          <Datagrid rowClick="edit" bulkActionButtons={false}>
            <TextField source="key" label={t('Key')} />
            <TextField source="value" label={t('Value')} />
            <TextField source="description" label={t('Description')} />
            <DateField source="updated_at" label={t('Updated')} showTime />
            <TextField source="updated_by" label={t('Updated by')} />
          </Datagrid>
        )}
      </List>
    </RequireProduct>
  );
};

// Managers are read-only server-side (403 on write) — drop the save toolbar
// for them instead of letting them edit and lose the change on Save.
export const KbVariableEdit = () => {
  const { permissions } = usePermissions();
  const readOnly = permissions !== 'admin';
  return (
    <RequireProduct title={t('Knowledge base · variables')}>
      {/* Keep the Content hub strip on the drilldown too. */}
      <RouteTabs tabs={CONTENT_TABS} />
      <Edit mutationMode="pessimistic" title={t('Edit KB variable')}>
        <SimpleForm toolbar={readOnly ? false : undefined}>
          <TextInput source="key" label={t('Key')} disabled />
          <TextInput source="description" label={t('Description')} fullWidth />
          <TextInput source="value" label={t('Value')} fullWidth multiline />
        </SimpleForm>
      </Edit>
    </RequireProduct>
  );
};
