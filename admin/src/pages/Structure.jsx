import { useCallback, useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Collapse from '@mui/material/Collapse';
import Divider from '@mui/material/Divider';
import FormControlLabel from '@mui/material/FormControlLabel';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';

const embedSnippet = (widgetKey) =>
  `<link rel="stylesheet" href="${API_URL}/widget.css">\n` +
  `<script type="module" src="${API_URL}/widget.js"\n` +
  `        data-widget-key="${widgetKey}"></script>`;

const SECRET_FIELDS = [
  ['openai_key_primary', 'OpenAI key (primary)', 'has_openai_key'],
  ['openai_key_fallback', 'OpenAI key (fallback)', 'has_openai_key_fallback'],
  ['handshake_secret', 'Widget handshake secret', 'has_handshake_secret'],
  ['telegram_bot_token', 'Telegram bot token', 'has_telegram_bot_token'],
  ['player_api_key', 'Player API key', 'has_player_api_key'],
];

const ProductRow = ({ product, onChanged }) => {
  const notify = useNotify();
  const [name, setName] = useState(product.name);
  const [open, setOpen] = useState(false);
  const [secrets, setSecrets] = useState({});

  const saveProduct = async (fields) => {
    try {
      await httpClient(`${API_URL}/admin/products/${product.id}`, {
        method: 'PUT',
        body: JSON.stringify(fields),
      });
      notify('Product saved', { type: 'success' });
      onChanged();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    }
  };

  const rotateKey = async () => {
    if (!window.confirm('Rotate the widget key? Old embeds stop working immediately.'))
      return;
    try {
      await httpClient(`${API_URL}/admin/products/${product.id}/widget-key`, {
        method: 'POST',
      });
      notify('Widget key rotated', { type: 'success' });
      onChanged();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Rotate failed', { type: 'error' });
    }
  };

  const copySnippet = async () => {
    await navigator.clipboard.writeText(embedSnippet(product.widget_key));
    notify('Embed snippet copied', { type: 'info' });
  };

  const saveSecrets = async () => {
    const fields = Object.fromEntries(
      Object.entries(secrets).filter(([, v]) => v !== undefined)
    );
    if (!Object.keys(fields).length) {
      notify('Nothing to update', { type: 'info' });
      return;
    }
    try {
      await httpClient(`${API_URL}/admin/products/${product.id}/secrets`, {
        method: 'PUT',
        body: JSON.stringify(fields),
      });
      notify('Secrets saved (write-only; never shown back)', { type: 'success' });
      setSecrets({});
      onChanged();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    }
  };

  return (
    <Box sx={{ pl: 2, py: 1 }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <TextField
          size="small"
          value={name}
          onChange={(e) => setName(e.target.value)}
          label={`Product · ${product.slug}`}
        />
        <Button size="small" onClick={() => saveProduct({ name })}>
          Rename
        </Button>
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={product.active}
              onChange={(e) => saveProduct({ active: e.target.checked })}
            />
          }
          label="Active"
        />
        <Chip size="small" label={product.widget_key} />
        <Button size="small" onClick={copySnippet}>
          Copy embed snippet
        </Button>
        <Button size="small" color="warning" onClick={rotateKey}>
          Rotate key
        </Button>
        <Button size="small" onClick={() => setOpen(!open)}>
          {open ? 'Hide secrets' : 'Secrets…'}
        </Button>
      </Stack>
      <Collapse in={open}>
        <Box sx={{ pl: 1, pt: 1, maxWidth: 560 }}>
          <Typography variant="body2" color="text.secondary">
            Write-only (encrypted at rest). Leave a field untouched to keep the
            current value; save an empty field to clear it (fall back to env).
          </Typography>
          {SECRET_FIELDS.map(([field, label, flag]) => (
            <TextField
              key={field}
              label={`${label} ${product[flag] ? '· set' : '· not set'}`}
              type="password"
              value={secrets[field] ?? ''}
              onChange={(e) => setSecrets({ ...secrets, [field]: e.target.value })}
              fullWidth
              margin="dense"
              autoComplete="new-password"
            />
          ))}
          <Button variant="contained" size="small" onClick={saveSecrets} sx={{ mt: 1 }}>
            Save secrets
          </Button>
        </Box>
      </Collapse>
    </Box>
  );
};

const NewProductForm = ({ partnerId, onChanged }) => {
  const notify = useNotify();
  const [slug, setSlug] = useState('');
  const [name, setName] = useState('');

  const create = async () => {
    try {
      await httpClient(`${API_URL}/admin/products`, {
        method: 'POST',
        body: JSON.stringify({ partner_id: partnerId, slug, name }),
      });
      notify('Product created (seeded with the starter KB)', { type: 'success' });
      setSlug('');
      setName('');
      onChanged();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Create failed', { type: 'error' });
    }
  };

  return (
    <Stack direction="row" spacing={1} sx={{ pl: 2, pt: 1 }}>
      <TextField size="small" label="New product slug" value={slug} onChange={(e) => setSlug(e.target.value)} />
      <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} />
      <Button size="small" variant="outlined" onClick={create} disabled={!slug || !name}>
        Add product
      </Button>
    </Stack>
  );
};

const PartnerCard = ({ partner, onChanged }) => {
  const notify = useNotify();
  const [name, setName] = useState(partner.name);

  const savePartner = async (fields) => {
    try {
      await httpClient(`${API_URL}/admin/partners/${partner.id}`, {
        method: 'PUT',
        body: JSON.stringify(fields),
      });
      notify('Partner saved', { type: 'success' });
      onChanged();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    }
  };

  return (
    <Card sx={{ mb: 2 }}>
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="center">
          <TextField
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            label={`Partner · ${partner.slug}`}
          />
          <Button size="small" onClick={() => savePartner({ name })}>
            Rename
          </Button>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={partner.active}
                onChange={(e) => savePartner({ active: e.target.checked })}
              />
            }
            label="Active"
          />
        </Stack>
        <Divider sx={{ my: 1 }} />
        {(partner.products || []).map((p) => (
          <ProductRow key={p.id} product={p} onChanged={onChanged} />
        ))}
        <NewProductForm partnerId={partner.id} onChanged={onChanged} />
      </CardContent>
    </Card>
  );
};

/**
 * Partners → products management: rename/deactivate, widget keys (+ embed
 * snippet + rotation) and per-product write-only secrets. Partner creation is
 * global-admin only (the server enforces it).
 */
const Structure = () => {
  const notify = useNotify();
  const [structure, setStructure] = useState(null);
  const [newPartner, setNewPartner] = useState({ slug: '', name: '' });

  const load = useCallback(() => {
    httpClient(`${API_URL}/admin/structure`)
      .then(({ json }) => setStructure(json))
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));
  }, [notify]);

  useEffect(() => {
    load();
  }, [load]);

  const createPartner = async () => {
    try {
      await httpClient(`${API_URL}/admin/partners`, {
        method: 'POST',
        body: JSON.stringify(newPartner),
      });
      notify('Partner created', { type: 'success' });
      setNewPartner({ slug: '', name: '' });
      load();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Create failed', { type: 'error' });
    }
  };

  if (!structure) return <Box sx={{ p: 2 }}>Loading…</Box>;

  return (
    <Box sx={{ p: 2, maxWidth: 1000 }}>
      <Title title="Structure" />
      {structure.global_role === 'admin' && (
        <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
          <TextField
            size="small"
            label="New partner slug"
            value={newPartner.slug}
            onChange={(e) => setNewPartner({ ...newPartner, slug: e.target.value })}
          />
          <TextField
            size="small"
            label="Name"
            value={newPartner.name}
            onChange={(e) => setNewPartner({ ...newPartner, name: e.target.value })}
          />
          <Button
            variant="outlined"
            onClick={createPartner}
            disabled={!newPartner.slug || !newPartner.name}
          >
            Add partner
          </Button>
        </Stack>
      )}
      {(structure.partners || []).map((pa) => (
        <PartnerCard key={pa.id} partner={pa} onChanged={load} />
      ))}
    </Box>
  );
};

export default Structure;
