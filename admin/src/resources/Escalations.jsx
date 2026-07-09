import {
  BooleanField,
  BulkDeleteButton,
  Datagrid,
  DateField,
  DateInput,
  DeleteButton,
  List,
  NumberField,
  TextField,
  TextInput,
  usePermissions,
  useRedirect,
} from 'react-admin';
import Alert from '@mui/material/Alert';
import MobileList from '../components/MobileList';
import RequireProduct from '../components/RequireProduct';
import useIsMobile from '../lib/useIsMobile';

const filters = [
  <TextInput key="topic" source="topic" label="Topic slug" alwaysOn />,
  <DateInput key="from" source="from" label="From" />,
  <DateInput key="to" source="to" label="To" />,
];

/**
 * The "Unresolved" queue: escalated sessions plus abandoned open chats with at
 * least one user turn (the backend has no separate escalations table — a
 * hand-off closes the session, and resolution happens outside the chat).
 * Read-only; clicking a row opens the full conversation transcript.
 */
export const EscalationList = () => {
  const redirect = useRedirect();
  // Delete is admin-only and destructive; managers keep the read-only queue.
  const { permissions } = usePermissions();
  const isAdmin = permissions === 'admin';
  const isMobile = useIsMobile();
  return (
    <RequireProduct title="Escalations / unresolved">
      <Alert severity="info" sx={{ mt: 2 }}>
        Sessions that still need attention: escalated hand-offs and abandoned
        open chats. Rows open the full conversation.
      </Alert>
      <List
        resource="unresolved"
        filters={filters}
        perPage={25}
        exporter={false}
        title="Escalations / unresolved"
      >
        {isMobile ? (
          <MobileList
            primaryText={(r) => `${r.topic || '—'} · ${r.status}${r.escalated ? ' · escalated' : ''}`}
            secondaryText={(r) => r.first_message || ''}
            tertiaryText={(r) =>
              `${r.message_count ?? 0} msgs · $${(r.cost_usd_total ?? 0).toFixed(4)} · ${
                r.created_at ? new Date(r.created_at).toLocaleString() : ''
              }`
            }
            onRowClick={(id) => redirect('show', 'sessions', id)}
          />
        ) : (
          <Datagrid
            bulkActionButtons={
              isAdmin ? <BulkDeleteButton mutationMode="pessimistic" /> : false
            }
            rowClick={(id) => {
              redirect('show', 'sessions', id);
              return false;
            }}
          >
            <TextField source="topic" />
            <TextField source="session_id" label="Session" />
            <TextField source="lang" label="Lang" />
            <TextField source="status" />
            <BooleanField source="escalated" />
            <NumberField source="message_count" label="Msgs" />
            <NumberField
              source="cost_usd_total"
              label="Cost $"
              options={{ maximumFractionDigits: 4 }}
            />
            <TextField source="first_message" label="First message" sx={{ display: 'block', maxWidth: 320 }} />
            <DateField source="created_at" showTime />
            {isAdmin && (
              <DeleteButton mutationMode="pessimistic" redirect={false} />
            )}
          </Datagrid>
        )}
      </List>
    </RequireProduct>
  );
};
