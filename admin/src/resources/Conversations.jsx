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
import { t } from '../i18n';
import { fmtDateTime } from '../lib/fmt';

const STATUS_CHOICES = [
  { id: 'open', name: t('Open') },
  { id: 'escalated', name: t('Escalated') },
  { id: 'resolved', name: t('Resolved') },
];

// The backend page size is fixed at 25, so the per-page selector is hidden
// (an operator-picked size would desync from what the server actually returns).
const SessionsPagination = () => <Pagination rowsPerPageOptions={[]} />;

const useFilters = () => {
  const langs = useSupportedLanguages();
  return [
    <TextInput key="q" source="q" label={t('Search in messages')} alwaysOn />,
    <NumberInput
      key="min_messages"
      source="min_messages"
      label={t('Min messages')}
      min={0}
      alwaysOn
    />,
    <TextInput key="topic" source="topic" label={t('Topic slug')} />,
    <SelectInput
      key="lang"
      source="lang"
      label={t('Language')}
      choices={langs.map((l) => ({ id: l.code, name: `${l.name} (${l.code})` }))}
    />,
    <SelectInput key="status" source="status" label={t('Status')} choices={STATUS_CHOICES} />,
    <BooleanInput key="escalated" source="escalated" label={t('Escalated')} />,
    <DateInput key="from" source="from" label={t('From')} />,
    <DateInput key="to" source="to" label={t('To')} />,
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
    <RequireProduct title={t('Conversations')}>
      <List
        filters={useFilters()}
        // Hide empty sessions (opened widget, never wrote) by default — clear the
        // "Min messages" filter to see them.
        filterDefaultValues={{ min_messages: 1 }}
        perPage={25}
        pagination={<SessionsPagination />}
        exporter={false}
        title={t('Conversations')}
        sort={{ field: 'created_at', order: 'DESC' }}
      >
        {isMobile ? (
          <MobileList
            primaryText={(r) => `${r.topic || '—'} · ${r.status}${r.escalated ? ` · ${t('escalated')}` : ''}`}
            secondaryText={(r) => `${r.lang || ''} · ${r.id}`}
            tertiaryText={(r) =>
              `${r.message_count ?? 0} ${t('msgs')} · $${(r.cost_usd_total ?? 0).toFixed(4)} · ${
                fmtDateTime(r.created_at)
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
          <TextField source="id" label={t('Session')} sortable={false} />
          <TextField source="topic" label={t('Topic')} sortable={false} />
          <TextField source="lang" label={t('Lang')} sortable={false} />
          <TextField source="status" label={t('Status')} sortable={false} />
          <BooleanField source="escalated" label={t('Escalated')} sortable={false} />
          <NumberField source="message_count" label={t('Msgs')} sortable={false} />
          <NumberField
            source="cost_usd_total"
            label={t('Cost $')}
            options={{ maximumFractionDigits: 4 }}
            sortable={false}
          />
          <DateField source="created_at" label={t('Created')} showTime sortable={false} />
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
      <Typography variant="h6">{t('Message thread')}</Typography>
      {timeline.length === 0 && (
        <Typography color="text.secondary">{t('No messages.')}</Typography>
      )}
      {timeline.map((item, i) =>
        item.kind === 'event' ? (
          <Box
            key={item.id != null ? `e${item.id}` : `i${i}`}
            sx={{ textAlign: 'center', py: 0.5 }}
          >
            <Chip
              size="small"
              label={`${t('switched')} ${item.payload?.from || '?'} → ${item.payload?.to || '?'}${
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
                {item.role} · {fmtDateTime(item.created_at)}
                {item.model ? ` · ${item.model}` : ''}
                {item.cost_usd ? ` · $${item.cost_usd.toFixed(5)}` : ''}
                {item.ping_context ? ` · ⚡ ${t('proactive')}: ${item.ping_context}` : ''}
              </Typography>
              <Typography sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'break-word' }}>{item.content}</Typography>
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
    [t('Session'), record.id],
    [t('Topic'), record.topic],
    [t('Language'), record.lang],
    [t('Status'), record.status],
    [t('Escalated'), record.escalated ? t('yes') : t('no')],
    [t('Messages'), record.message_count],
    [t('Total cost $'), record.cost_usd_total],
    [t('Created'), record.created_at && fmtDateTime(record.created_at)],
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
  <RequireProduct title={t('Conversation')}>
  <Show title={t('Conversation')}>
    <Box sx={{ p: 2 }}>
      <SessionSummary />
      <MessageThread />
    </Box>
  </Show>
  </RequireProduct>
);
