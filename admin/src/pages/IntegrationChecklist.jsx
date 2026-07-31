import { useCallback, useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { t } from '../i18n';
import { notifyError } from '../lib/notifyError';
import { useReadOnly } from '../lib/useReadOnly';

/**
 * Integration checklist — the living, deploy-level list of what we still
 * need from the external teams (the casino platform, email/devops, BI) to
 * wire the retention orchestrator end to end. Seeded with the known items;
 * statuses and notes are edited here as agreements land, so "what do we
 * still have to ask Anton for" is always answerable from one page.
 */

const STATUS_COLOR = {
  waiting: 'warning',
  in_progress: 'info',
  received: 'primary',
  wired: 'success',
  not_needed: 'default',
};

const IntegrationChecklist = () => {
  const notify = useNotify();
  const readOnly = useReadOnly();
  const [items, setItems] = useState([]);
  const [statuses, setStatuses] = useState([]);
  const [draft, setDraft] = useState({ item_key: '', title: '', owner: '', blocking: '', description: '' });

  const load = useCallback(() => {
    httpClient(`${API_URL}/admin/integration-checklist`)
      .then(({ json }) => {
        setItems(json.items || []);
        setStatuses(json.statuses || []);
      })
      .catch((e) => notifyError(notify, e, t('Load failed')));
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  const update = async (itemKey, patch) => {
    try {
      await httpClient(`${API_URL}/admin/integration-checklist/${itemKey}`, {
        method: 'PUT',
        body: JSON.stringify(patch),
      });
      load();
    } catch (e) {
      notifyError(notify, e, t('Save failed'));
    }
  };

  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('Integration checklist')} />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {t('What the external teams (casino platform, email, BI) still owe us to wire the retention orchestrator end to end — and what each item blocks on our side. Update the status as agreements land; add items as new dependencies appear.')}
      </Typography>
      {items.map((it) => (
        <Card key={it.item_key} sx={{ mb: 1.5 }}>
          <CardContent>
            <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}
              alignItems={{ md: 'center' }}>
              <Box sx={{ flex: 1 }}>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="subtitle1">{t(it.title)}</Typography>
                  <Chip size="small" label={t(it.status)}
                    color={STATUS_COLOR[it.status] || 'default'} />
                  {it.owner && (
                    <Chip size="small" variant="outlined" label={t(it.owner)} />
                  )}
                </Stack>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                  {t(it.description)}
                </Typography>
                {it.blocking && (
                  <Typography variant="caption" color="warning.main">
                    {t('Blocks on our side')}: {t(it.blocking)}
                  </Typography>
                )}
              </Box>
              <Stack spacing={1} sx={{ minWidth: { md: 340 } }}>
                <TextField select size="small" label={t('Status')}
                  value={it.status} disabled={readOnly}
                  onChange={(e) => update(it.item_key, { status: e.target.value })}>
                  {statuses.map((s) => (
                    <MenuItem key={s} value={s}>{t(s)}</MenuItem>
                  ))}
                </TextField>
                <NotesField item={it} readOnly={readOnly} onSave={update} />
              </Stack>
            </Stack>
          </CardContent>
        </Card>
      ))}
      {!readOnly && (
        <Card sx={{ mt: 2 }}>
          <CardContent>
            <Typography variant="subtitle1" sx={{ mb: 1 }}>{t('Add item')}</Typography>
            <Stack direction={{ xs: 'column', md: 'row' }} spacing={1}>
              <TextField size="small" label={t('Key (a-z, -)')} value={draft.item_key}
                onChange={(e) => setDraft({ ...draft, item_key: e.target.value })} />
              <TextField size="small" label={t('Title')} value={draft.title} fullWidth
                onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
              <TextField size="small" label={t('Owner')} value={draft.owner}
                onChange={(e) => setDraft({ ...draft, owner: e.target.value })} />
              <TextField size="small" label={t('Blocks on our side')} value={draft.blocking}
                onChange={(e) => setDraft({ ...draft, blocking: e.target.value })} />
              <Button variant="outlined" onClick={async () => {
                try {
                  await httpClient(`${API_URL}/admin/integration-checklist`, {
                    method: 'POST',
                    body: JSON.stringify(draft),
                  });
                  setDraft({ item_key: '', title: '', owner: '', blocking: '', description: '' });
                  load();
                } catch (e) {
                  notifyError(notify, e, t('Save failed'));
                }
              }}>{t('Add')}</Button>
            </Stack>
          </CardContent>
        </Card>
      )}
    </Box>
  );
};

const NotesField = ({ item, readOnly, onSave }) => {
  const [notes, setNotes] = useState(item.notes || '');
  useEffect(() => setNotes(item.notes || ''), [item.notes]);
  return (
    <Stack direction="row" spacing={1}>
      <TextField size="small" label={t('Notes')} value={notes} fullWidth
        multiline disabled={readOnly}
        onChange={(e) => setNotes(e.target.value)} />
      {!readOnly && notes !== (item.notes || '') && (
        <Button size="small" onClick={() => onSave(item.item_key, { notes })}>
          {t('Save')}
        </Button>
      )}
    </Stack>
  );
};

export default IntegrationChecklist;
