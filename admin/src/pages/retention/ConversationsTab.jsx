import { useCallback, useEffect, useState } from 'react';
import { useNotify } from 'react-admin';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Checkbox from '@mui/material/Checkbox';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../../httpClient';
import useIsMobile from '../../lib/useIsMobile';
import { t } from '../../i18n';
import { notifyError } from '../../lib/notifyError';
import { useReadOnly } from '../../lib/useReadOnly';
import { fmtDateTime } from '../../lib/fmt';
import PhotoPreview from './PhotoPreview';
import GridPagination from '../../components/GridPagination';

// ---------------------------------------------------------------------------
// Conversations tab — the Telegram chats, logged apart from support. A chat
// "closes" when it sits idle past the `session_idle_minutes` retention knob
// (status becomes resolved); the player's next message starts a fresh chat
// that carries a short continuity tail from the previous one.
// ---------------------------------------------------------------------------
const ConversationsTab = ({ productId }) => {
  const isMobile = useIsMobile();
  const notify = useNotify();
  const isAdmin = !useReadOnly();
  const [data, setData] = useState({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState(null); // {session, messages, ...}
  const [selected, setSelected] = useState(() => new Set());
  const pageSize = 25;

  const load = useCallback(() => {
    httpClient(
      `${API_URL}/admin/retention/sessions?product_id=${productId}&page=${page}&page_size=${pageSize}`
    )
      .then(({ json }) => setData(json))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, page, notify]);

  useEffect(() => {
    load();
  }, [load]);

  const openTranscript = (id) => {
    httpClient(`${API_URL}/admin/session/${id}`)
      .then(({ json }) => setDetail(json))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  };

  const toggleSelect = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allSelected =
    data.items.length > 0 && data.items.every((s) => selected.has(s.id));
  const toggleSelectAll = () =>
    setSelected((prev) =>
      allSelected ? new Set() : new Set(data.items.map((s) => s.id))
    );

  const deleteIds = async (ids) => {
    if (!ids.length) return;
    const many = ids.length > 1;
    if (
      !window.confirm(
        many
          ? t(
              'Delete {n} Telegram chats? This permanently removes their messages and logs AND purges each linked player (identity, seen photos, pings) from analytics.'
            ).replace('{n}', ids.length)
          : t(
              'Delete this Telegram chat? This permanently removes its messages and logs AND purges the linked player (identity, seen photos, pings) from analytics.'
            )
      )
    ) {
      return;
    }
    try {
      await Promise.all(
        ids.map((id) =>
          httpClient(`${API_URL}/admin/session/${id}`, { method: 'DELETE' })
        )
      );
      notify(
        many ? t('{n} chats deleted').replace('{n}', ids.length) : t('Chat deleted'),
        { type: 'success' }
      );
      setSelected(new Set());
      setDetail(null);
      load();
    } catch (e) {
      notifyError(notify, e, t('Delete failed'));
    }
  };

  const pages = Math.max(1, Math.ceil((data.total || 0) / pageSize));
  const cols = isAdmin ? 10 : 8;

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        {t(
          'Telegram chats with Nika, kept apart from the support-widget conversations. An idle chat closes automatically (the “Session idle (min)” knob in Retention → Settings); when the player returns, a new chat starts and Nika is shown the tail of the previous one for continuity. Click a row for the transcript.'
        )}
      </Alert>
      {isAdmin && (
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
          <Button
            size="small"
            color="error"
            variant="outlined"
            disabled={!selected.size}
            onClick={() => deleteIds([...selected])}
          >
            {t('Delete selected')} ({selected.size})
          </Button>
        </Stack>
      )}
      {isMobile ? (
        // Card rows on phones — the same pattern the react-admin lists use
        // (MobileList), instead of a 10-column horizontal-scroll table.
        <Stack spacing={1} sx={{ mb: 1 }}>
          {data.items.map((s) => (
            <Card key={s.id} variant="outlined" onClick={() => openTranscript(s.id)} sx={{ cursor: 'pointer' }}>
              <CardContent sx={{ py: 1.25, '&:last-child': { pb: 1.25 } }}>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                  <Typography variant="subtitle2" sx={{ overflowWrap: 'anywhere' }}>
                    {s.full_name || s.player_id || '—'}
                    {s.tg_username ? ` · @${s.tg_username}` : s.tg_user_id ? ` · ${s.tg_user_id}` : ''}
                  </Typography>
                  <Chip
                    size="small"
                    label={s.status}
                    color={s.status === 'open' ? 'success' : 'default'}
                    variant="outlined"
                  />
                </Stack>
                <Typography variant="body2" color="text.secondary">
                  {t('Msgs')}: {s.message_count} · {t('Cost $')}
                  {s.cost_usd_total ? s.cost_usd_total.toFixed(4) : '0'}
                  {s.lang ? ` · ${s.lang}` : ''}
                </Typography>
                <Typography variant="caption" color="text.secondary" component="div">
                  {fmtDateTime(s.created_at)} → {fmtDateTime(s.updated_at)}
                </Typography>
                {isAdmin && (
                  <Button
                    size="small"
                    color="error"
                    sx={{ mt: 0.5 }}
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteIds([s.id]);
                    }}
                  >
                    {t('Delete')}
                  </Button>
                )}
              </CardContent>
            </Card>
          ))}
          {data.items.length === 0 && (
            <Typography color="text.secondary" sx={{ py: 2 }}>
              {t('No Telegram chats yet.')}
            </Typography>
          )}
        </Stack>
      ) : (
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={{ minWidth: 760 }}>
          <TableHead>
            <TableRow>
              {isAdmin && (
                <TableCell padding="checkbox">
                  <Checkbox
                    size="small"
                    checked={allSelected}
                    indeterminate={selected.size > 0 && !allSelected}
                    onChange={toggleSelectAll}
                  />
                </TableCell>
              )}
              <TableCell>{t('Player')}</TableCell>
              <TableCell>{t('TG user')}</TableCell>
              <TableCell>{t('Lang')}</TableCell>
              <TableCell>{t('Status')}</TableCell>
              <TableCell align="right">{t('Msgs')}</TableCell>
              <TableCell align="right">{t('Cost $')}</TableCell>
              <TableCell>{t('Started')}</TableCell>
              <TableCell>{t('Last activity')}</TableCell>
              {isAdmin && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {data.items.map((s) => (
              <TableRow
                key={s.id}
                hover
                sx={{ cursor: 'pointer' }}
                onClick={() => openTranscript(s.id)}
              >
                {isAdmin && (
                  <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                    <Checkbox
                      size="small"
                      checked={selected.has(s.id)}
                      onChange={() => toggleSelect(s.id)}
                    />
                  </TableCell>
                )}
                <TableCell>{s.full_name || s.player_id || '—'}</TableCell>
                <TableCell>
                  {s.tg_username ? `@${s.tg_username}` : s.tg_user_id || '—'}
                </TableCell>
                <TableCell>{s.lang || '—'}</TableCell>
                <TableCell>
                  <Chip
                    size="small"
                    label={s.status}
                    color={s.status === 'open' ? 'success' : 'default'}
                    variant="outlined"
                  />
                </TableCell>
                <TableCell align="right">{s.message_count}</TableCell>
                <TableCell align="right">
                  {s.cost_usd_total ? s.cost_usd_total.toFixed(4) : '0'}
                </TableCell>
                <TableCell>{fmtDateTime(s.created_at)}</TableCell>
                <TableCell>{fmtDateTime(s.updated_at)}</TableCell>
                {isAdmin && (
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Button
                      size="small"
                      color="error"
                      onClick={() => deleteIds([s.id])}
                    >
                      {t('Delete')}
                    </Button>
                  </TableCell>
                )}
              </TableRow>
            ))}
            {data.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={cols}>
                  <Typography color="text.secondary" sx={{ py: 2 }}>
                    {t('No Telegram chats yet.')}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Box>
      )}
      {pages > 1 && (
        <GridPagination count={data.total || 0} page={page} perPage={pageSize}
                        onPage={setPage} unit={t('chats')} />
      )}
      <Dialog open={!!detail} onClose={() => setDetail(null)} maxWidth="md" fullWidth fullScreen={isMobile}>
        <DialogTitle>
          {t('Telegram chat')} · {detail?.session?.id}
          {detail?.session?.status ? ` · ${detail.session.status}` : ''}
        </DialogTitle>
        <DialogContent dividers>
          <Stack spacing={1}>
            {[
              ...(detail?.messages || []).map((m) => ({ ...m, _kind: 'message' })),
              ...(detail?.photos || []).map((p) => ({ ...p, _kind: 'photo' })),
            ]
              .sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
              .map((item, i) =>
                item._kind === 'photo' ? (
                  <Box
                    key={`p${i}`}
                    sx={{ maxWidth: '80%', alignSelf: 'flex-end', width: { xs: 180, sm: 240 } }}
                  >
                    <Typography variant="caption" color="text.secondary">
                      {t('photo')} · {fmtDateTime(item.created_at)}
                    </Typography>
                    <PhotoPreview photoId={item.photo_id} />
                    {item.description && (
                      <Typography variant="caption" color="text.secondary" display="block">
                        {item.description}
                      </Typography>
                    )}
                  </Box>
                ) : (
                  <Card
                    key={`m${i}`}
                    variant="outlined"
                    sx={{
                      maxWidth: '80%',
                      alignSelf: item.role === 'user' ? 'flex-start' : 'flex-end',
                      bgcolor: item.role === 'user' ? 'transparent' : 'action.hover',
                    }}
                  >
                    <CardContent sx={{ py: 1, '&:last-child': { pb: 1 } }}>
                      <Typography variant="caption" color="text.secondary">
                        {item.role} · {fmtDateTime(item.created_at)}
                        {item.cost_usd ? ` · $${item.cost_usd.toFixed(5)}` : ''}
                        {item.ping_context ? ` · ⚡ ${t('proactive:')} ${item.ping_context}` : ''}
                      </Typography>
                      <Typography sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'break-word' }}>{item.content}</Typography>
                    </CardContent>
                  </Card>
                )
              )}
            {(detail?.messages || []).length === 0 && (
              <Typography color="text.secondary">{t('No messages.')}</Typography>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Typography variant="caption" color="text.secondary" sx={{ mr: 'auto', ml: 1 }}>
            {t('Total cost:')} ${detail?.cost_usd_total ?? 0}
          </Typography>
          <Button onClick={() => setDetail(null)}>{t('Close')}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ConversationsTab;
