import {
  BooleanField,
  BooleanInput,
  BulkDeleteButton,
  Datagrid,
  DateField,
  DateInput,
  DeleteButton,
  List,
  NumberField,
  NumberInput,
  Pagination,
  SelectInput,
  Show,
  TextField,
  TextInput,
  usePermissions,
  useRecordContext,
  useRedirect,
} from 'react-admin';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { useSupportedLanguages } from '../lib/meta';
import MobileList from '../components/MobileList';
import RequireProduct from '../components/RequireProduct';
import useIsMobile from '../lib/useIsMobile';

const STATUS_CHOICES = [
  { id: 'open', name: 'Open' },
  { id: 'escalated', name: 'Escalated' },
  { id: 'resolved', name: 'Resolved' },
];

// The backend page size is fixed at 25, so the per-page selector is hidden
// (an operator-picked size would desync from what the server actually returns).
const SessionsPagination = () => <Pagination rowsPerPageOptions={[]} />;

const useFilters = () => {
  const langs = useSupportedLanguages();
  return [
    <TextInput key="q" source="q" label="Search in messages" alwaysOn />,
    <NumberInput
      key="min_messages"
      source="min_messages"
      label="Min messages"
      min={0}
      alwaysOn
    />,
    <TextInput key="topic" source="topic" label="Topic slug" />,
    <SelectInput
      key="lang"
      source="lang"
      label="Language"
      choices={langs.map((l) => ({ id: l.code, name: `${l.name} (${l.code})` }))}
    />,
    <SelectInput key="status" source="status" choices={STATUS_CHOICES} />,
    <BooleanInput key="escalated" source="escalated" label="Escalated" />,
    <DateInput key="from" source="from" label="From" />,
    <DateInput key="to" source="to" label="To" />,
  ];
};

export const ConversationList = () => {
  // Deleting a conversation is an admin-only, destructive action; managers get
  // no per-row or bulk delete controls (the server refuses them anyway).
  const { permissions } = usePermissions();
  const isAdmin = permissions === 'admin';
  const isMobile = useIsMobile();
  const redirect = useRedirect();
  return (
    <RequireProduct title="Conversations">
      <List
        filters={useFilters()}
        // Hide empty sessions (opened widget, never wrote) by default — clear the
        // "Min messages" filter to see them.
        filterDefaultValues={{ min_messages: 1 }}
        perPage={25}
        pagination={<SessionsPagination />}
        exporter={false}
        title="Conversations"
        sort={{ field: 'created_at', order: 'DESC' }}
      >
        {isMobile ? (
          <MobileList
            primaryText={(r) => `${r.topic || '—'} · ${r.status}${r.escalated ? ' · escalated' : ''}`}
            secondaryText={(r) => `${r.lang || ''} · ${r.id}`}
            tertiaryText={(r) =>
              `${r.message_count ?? 0} msgs · $${(r.cost_usd_total ?? 0).toFixed(4)} · ${
                r.created_at ? new Date(r.created_at).toLocaleString() : ''
              }`
            }
            onRowClick={(id) => redirect('show', 'sessions', id)}
          />
        ) : (
        <Datagrid
          rowClick="show"
          bulkActionButtons={
            isAdmin ? <BulkDeleteButton mutationMode="pessimistic" /> : false
          }
        >
          <TextField source="id" label="Session" sortable={false} />
          <TextField source="topic" sortable={false} />
          <TextField source="lang" label="Lang" sortable={false} />
          <TextField source="status" sortable={false} />
          <BooleanField source="escalated" sortable={false} />
          <NumberField source="message_count" label="Msgs" sortable={false} />
          <NumberField
            source="cost_usd_total"
            label="Cost $"
            options={{ maximumFractionDigits: 4 }}
            sortable={false}
          />
          <DateField source="created_at" showTime sortable={false} />
          {isAdmin && (
            <DeleteButton mutationMode="pessimistic" redirect={false} />
          )}
        </Datagrid>
        )}
      </List>
    </RequireProduct>
  );
};

const MessageThread = () => {
  const record = useRecordContext();
  if (!record) return null;
  const messages = record.messages || [];
  const events = record.events || [];
  // Interleave topic-switch markers into the transcript by timestamp.
  const timeline = [
    ...messages.map((m) => ({ ...m, kind: 'message' })),
    ...events.map((e) => ({ ...e, kind: 'event' })),
  ].sort((a, b) => new Date(a.created_at) - new Date(b.created_at));

  return (
    <Stack spacing={1} sx={{ mt: 2 }}>
      <Typography variant="h6">Message thread</Typography>
      {timeline.length === 0 && (
        <Typography color="text.secondary">No messages.</Typography>
      )}
      {timeline.map((item, i) =>
        item.kind === 'event' ? (
          <Box
            key={item.id != null ? `e${item.id}` : `i${i}`}
            sx={{ textAlign: 'center', py: 0.5 }}
          >
            <Chip
              size="small"
              label={`switched ${item.payload?.from || '?'} → ${item.payload?.to || '?'}${
                item.payload?.cost_usd ? ` · $${item.payload.cost_usd}` : ''
              }`}
            />
          </Box>
        ) : (
          <Card
            key={item.id != null ? `m${item.id}` : `i${i}`}
            variant="outlined"
            sx={{
              maxWidth: { xs: '92%', sm: '75%' },
              alignSelf: item.role === 'user' ? 'flex-start' : 'flex-end',
              bgcolor: item.role === 'user' ? 'transparent' : 'action.hover',
            }}
          >
            <CardContent sx={{ py: 1, '&:last-child': { pb: 1 } }}>
              <Typography variant="caption" color="text.secondary">
                {item.role} · {new Date(item.created_at).toLocaleString()}
                {item.model ? ` · ${item.model}` : ''}
                {item.cost_usd ? ` · $${item.cost_usd.toFixed(5)}` : ''}
                {item.ping_context ? ` · ⚡ proactive: ${item.ping_context}` : ''}
              </Typography>
              <Typography sx={{ whiteSpace: 'pre-wrap' }}>{item.content}</Typography>
            </CardContent>
          </Card>
        )
      )}
    </Stack>
  );
};

const SessionSummary = () => {
  const record = useRecordContext();
  if (!record) return null;
  const fields = [
    ['Session', record.id],
    ['Topic', record.topic],
    ['Language', record.lang],
    ['Status', record.status],
    ['Escalated', record.escalated ? 'yes' : 'no'],
    ['Messages', record.message_count],
    ['Total cost $', record.cost_usd_total],
    ['Created', record.created_at && new Date(record.created_at).toLocaleString()],
  ];
  return (
    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
      {fields
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => (
          <Chip key={k} label={`${k}: ${v}`} variant="outlined" />
        ))}
    </Stack>
  );
};

export const ConversationShow = () => (
  <RequireProduct title="Conversation">
  <Show title="Conversation">
    <Box sx={{ p: 2 }}>
      <SessionSummary />
      <MessageThread />
    </Box>
  </Show>
  </RequireProduct>
);
