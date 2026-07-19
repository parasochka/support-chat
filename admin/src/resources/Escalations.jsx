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
import { t } from '../i18n';
import { fmtDateTime } from '../lib/fmt';

const filters = [
  <TextInput key="topic" source="topic" label={t('Topic slug')} alwaysOn />,
  <DateInput key="from" source="from" label={t('From')} />,
  <DateInput key="to" source="to" label={t('To')} />,
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
    <RequireProduct title={t('Escalations / unresolved')}>
      <Alert severity="info" sx={{ mt: 2 }}>
        {t(
          'Sessions that still need attention: escalated hand-offs and abandoned open chats. Rows open the full conversation.'
        )}
      </Alert>
      <List
        resource="unresolved"
        filters={filters}
        perPage={25}
        exporter={false}
        title={t('Escalations / unresolved')}
      >
        {isMobile ? (
          <MobileList
            primaryText={(r) => `${r.topic || '—'} · ${r.status}${r.escalated ? ` · ${t('escalated')}` : ''}`}
            secondaryText={(r) => r.first_message || ''}
            tertiaryText={(r) =>
              `${r.message_count ?? 0} ${t('msgs')} · $${(r.cost_usd_total ?? 0).toFixed(4)} · ${
                fmtDateTime(r.created_at)
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
            <TextField source="topic" label={t('Topic')} />
            <TextField source="session_id" label={t('Session')} />
            <TextField source="lang" label={t('Lang')} />
            <TextField source="status" label={t('Status')} />
            <BooleanField source="escalated" label={t('Escalated')} />
            <NumberField source="message_count" label={t('Msgs')} />
            <NumberField
              source="cost_usd_total"
              label={t('Cost $')}
              options={{ maximumFractionDigits: 4 }}
            />
            <TextField source="first_message" label={t('First message')} sx={{ display: 'block', maxWidth: 320 }} />
            <DateField source="created_at" label={t('Created')} showTime />
            {isAdmin && (
              <DeleteButton mutationMode="pessimistic" redirect={false} />
            )}
          </Datagrid>
        )}
      </List>
    </RequireProduct>
  );
};
