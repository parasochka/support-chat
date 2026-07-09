import {
  Datagrid,
  DateField,
  Edit,
  List,
  SimpleForm,
  TextField,
  TextInput,
  useRedirect,
} from 'react-admin';
import MobileList from '../components/MobileList';
import RouteTabs from '../components/RouteTabs';
import RequireProduct from '../components/RequireProduct';
import useIsMobile from '../lib/useIsMobile';
import { KB_TABS } from './kbTabs';

/**
 * The admin-managed {placeholder} registry substituted into KB texts. It is
 * one surface with the Knowledge base (the values belong to the KB texts), so
 * both lists share the Topics / Variables tab strip.
 */
export const KbVariableList = () => {
  const isMobile = useIsMobile();
  const redirect = useRedirect();
  return (
    <RequireProduct title="Knowledge base · variables">
      <RouteTabs tabs={KB_TABS} />
      <List perPage={50} exporter={false} title="Knowledge base · variables">
        {isMobile ? (
          <MobileList
            primaryText={(r) => r.key}
            secondaryText={(r) => r.value || ''}
            tertiaryText={(r) => r.description || ''}
            onRowClick={(id) => redirect('edit', 'kb_variables', id)}
          />
        ) : (
          <Datagrid rowClick="edit" bulkActionButtons={false}>
            <TextField source="key" />
            <TextField source="value" />
            <TextField source="description" />
            <DateField source="updated_at" showTime />
            <TextField source="updated_by" />
          </Datagrid>
        )}
      </List>
    </RequireProduct>
  );
};

export const KbVariableEdit = () => (
  <RequireProduct title="Knowledge base · variables">
    <Edit mutationMode="pessimistic" title="Edit KB variable">
      <SimpleForm>
        <TextInput source="key" disabled />
        <TextInput source="description" fullWidth />
        <TextInput source="value" fullWidth multiline />
      </SimpleForm>
    </Edit>
  </RequireProduct>
);
