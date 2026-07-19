import { useCallback, useEffect, useState } from 'react';
import { Title, useNotify, usePermissions } from 'react-admin';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { API_URL, httpClient } from '../httpClient';
import useIsMobile from '../lib/useIsMobile';
import { t } from '../i18n';
import rich from '../components/Rich';
import { notifyError } from '../lib/notifyError';
import { fmtDateTime } from '../lib/fmt';

const ROLE_CHOICES = [
  ['admin', t('admin (read + write)')],
  ['manager', t('manager (read-only)')],
];

const SCOPE_CHOICES = [
  ['global', t('Global (everything)')],
  ['partner', t('Partner (all its products)')],
  ['product', t('Single product')],
];

const EMPTY_FORM = {
  name: '',
  role: 'manager',
  scope_type: 'product',
  partner_id: '',
  product_id: '',
};

const mono = { fontFamily: 'monospace', fontSize: 13 };

/**
 * Service API keys (sak_…) for machine-to-machine consumers of the admin API
 * (partner back-offices, BI pulls, CI). A key acts like an admin account with
 * one membership — role × scope — but is not a login: it rides in the same
 * Authorization: Bearer header. The plaintext token is shown ONCE at creation.
 * Admins only (the server refuses managers; keys are credentials).
 */
const ApiKeys = () => {
  const isMobile = useIsMobile();
  const notify = useNotify();
  const { permissions } = usePermissions();
  const [keys, setKeys] = useState(null);
  const [structure, setStructure] = useState(null);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [minted, setMinted] = useState(null); // {key, token} after a create

  const load = useCallback(() => {
    httpClient(`${API_URL}/admin/api-keys`)
      .then(({ json }) => setKeys(json.keys || []))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
    httpClient(`${API_URL}/admin/structure`)
      .then(({ json }) => setStructure(json))
      .catch(() => setStructure(null));
  }, [notify]);

  useEffect(() => {
    if (permissions === 'admin') load();
  }, [permissions, load]);

  if (permissions !== 'admin') {
    return (
      <Box sx={{ p: 2 }}>
        <Title title={t('API keys')} />
        <Alert severity="info" sx={{ maxWidth: 640 }}>
          <AlertTitle>{t('Admins only')}</AlertTitle>
          {t('Service API keys are credentials — only admin accounts may view or manage them.')}
        </Alert>
      </Box>
    );
  }

  const partners = structure?.partners || [];
  const products = partners.flatMap((pa) =>
    (pa.products || []).map((pr) => ({ ...pr, partner_name: pa.name }))
  );

  const partnerName = (id) =>
    partners.find((pa) => pa.id === id)?.name || t('partner #{id}').replace('{id}', id);
  const productName = (id) =>
    products.find((pr) => pr.id === id)?.name || t('product #{id}').replace('{id}', id);

  const scopeLabel = (k) => {
    if (k.scope_type === 'global') return t('Global');
    if (k.scope_type === 'partner') return `${t('Partner')} · ${partnerName(k.partner_id)}`;
    return `${t('Product')} · ${productName(k.product_id)}`;
  };

  const create = async () => {
    try {
      const { json } = await httpClient(`${API_URL}/admin/api-keys`, {
        method: 'POST',
        body: JSON.stringify({
          name: form.name,
          role: form.role,
          scope_type: form.scope_type,
          partner_id: form.scope_type === 'partner' ? Number(form.partner_id) : undefined,
          product_id: form.scope_type === 'product' ? Number(form.product_id) : undefined,
        }),
      });
      setMinted(json);
      setForm({ ...EMPTY_FORM });
      load();
    } catch (e) {
      notifyError(notify, e, t('Create failed'));
    }
  };

  const patch = async (id, fields) => {
    try {
      await httpClient(`${API_URL}/admin/api-keys/${id}`, {
        method: 'PUT',
        body: JSON.stringify(fields),
      });
      load();
    } catch (e) {
      notifyError(notify, e, t('Save failed'));
    }
  };

  const remove = async (id, name) => {
    if (!window.confirm(`${t('Delete the key')} “${name}”? ${t('Consumers using it stop working immediately.')}`))
      return;
    try {
      await httpClient(`${API_URL}/admin/api-keys/${id}`, { method: 'DELETE' });
      notify(t('Key deleted'), { type: 'success' });
      load();
    } catch (e) {
      notifyError(notify, e, t('Delete failed'));
    }
  };

  const copyToken = async () => {
    await navigator.clipboard.writeText(minted.token);
    notify(t('Token copied'), { type: 'info' });
  };

  const createDisabled =
    !form.name.trim() ||
    (form.scope_type === 'partner' && !form.partner_id) ||
    (form.scope_type === 'product' && !form.product_id);

  if (keys === null) return <Box sx={{ p: 2 }}>{t('Loading…')}</Box>;

  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('API keys')} />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {rich(
          t(
            "Service keys for machine consumers of the admin API (partner back-offices, BI, CI). A key behaves like an admin account with exactly one role × scope and is sent as `Authorization: Bearer sak_…`. The token is shown once at creation — store it in the consumer's secret store."
          )
        )}
      </Typography>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('Create key')}
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            <TextField
              size="small"
              label={t('Name (what uses it)')}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              sx={{ flex: '1 1 220px', maxWidth: 360 }}
            />
            <TextField
              select
              size="small"
              label={t('Role')}
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
              sx={{ minWidth: 200 }}
            >
              {ROLE_CHOICES.map(([value, label]) => (
                <MenuItem key={value} value={value}>
                  {label}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label={t('Scope')}
              value={form.scope_type}
              onChange={(e) =>
                setForm({ ...form, scope_type: e.target.value, partner_id: '', product_id: '' })
              }
              sx={{ minWidth: 200 }}
            >
              {SCOPE_CHOICES.map(([value, label]) => (
                <MenuItem key={value} value={value}>
                  {label}
                </MenuItem>
              ))}
            </TextField>
            {form.scope_type === 'partner' && (
              <TextField
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
              </TextField>
            )}
            {form.scope_type === 'product' && (
              <TextField
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
              </TextField>
            )}
            <Button variant="contained" size="small" onClick={create} disabled={createDisabled}>
              {t('Create')}
            </Button>
          </Stack>
          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
            {t(
              'Give a key the narrowest scope that works — a read-only manager key per product for pulls, an admin key only when the consumer must write.'
            )}
          </Typography>
        </CardContent>
      </Card>

      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t('Name')}</TableCell>
              <TableCell>{t('Token')}</TableCell>
              <TableCell>{t('Role')}</TableCell>
              <TableCell>{t('Scope')}</TableCell>
              <TableCell>{t('Active')}</TableCell>
              <TableCell>{t('Last used')}</TableCell>
              <TableCell>{t('Created')}</TableCell>
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {keys.map((k) => (
              <TableRow key={k.id} hover>
                <TableCell>{k.name}</TableCell>
                <TableCell>
                  <Typography component="span" sx={mono}>
                    sak_…{k.token_hint}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Chip size="small" label={k.role} variant="outlined" />
                </TableCell>
                <TableCell>{scopeLabel(k)}</TableCell>
                <TableCell>
                  <Switch
                    size="small"
                    checked={Boolean(k.active)}
                    onChange={(e) => patch(k.id, { active: e.target.checked })}
                  />
                </TableCell>
                <TableCell>
                  {k.last_used_at ? fmtDateTime(k.last_used_at) : t('never')}
                </TableCell>
                <TableCell>
                  {fmtDateTime(k.created_at)}
                  {k.created_by ? ` · ${k.created_by}` : ''}
                </TableCell>
                <TableCell>
                  <Button size="small" color="error" onClick={() => remove(k.id, k.name)}>
                    {t('Delete')}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {keys.length === 0 && (
              <TableRow>
                <TableCell colSpan={8}>
                  <Typography color="text.secondary" sx={{ py: 2 }}>
                    {t('No API keys yet — create the first one above.')}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Box>

      <Dialog open={!!minted} onClose={() => setMinted(null)} maxWidth="sm" fullWidth fullScreen={isMobile}>
        <DialogTitle>{t('Key created — copy the token now')}</DialogTitle>
        <DialogContent dividers>
          <Alert severity="warning" sx={{ mb: 2 }}>
            {t(
              "This token is shown ONCE and cannot be recovered. Copy it into the consumer's secret store before closing; if it is lost, delete the key and mint a new one."
            )}
          </Alert>
          <TextField
            value={minted?.token || ''}
            label={`${t('Token')} · ${minted?.key?.name || ''}`}
            fullWidth
            size="small"
            slotProps={{
              input: {
                readOnly: true,
                sx: mono,
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton size="small" onClick={copyToken} aria-label={t('Copy token')}>
                      <ContentCopyIcon fontSize="small" />
                    </IconButton>
                  </InputAdornment>
                ),
              },
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button
            variant="outlined"
            startIcon={<ContentCopyIcon fontSize="small" />}
            onClick={copyToken}
          >
            {t('Copy token')}
          </Button>
          <Button onClick={() => setMinted(null)}>{t('Done')}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ApiKeys;
