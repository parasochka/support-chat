import { useCallback, useEffect, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Divider from '@mui/material/Divider';
import FormControlLabel from '@mui/material/FormControlLabel';
import Grid from '@mui/material/Grid';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { API_URL, httpClient } from '../httpClient';
import SecretField from '../components/SecretField';
import { t } from '../i18n';
import SetBadge from '../components/SetBadge';

const embedSnippet = (widgetKey) =>
  `<link rel="stylesheet" href="${API_URL}/widget.css">\n` +
  `<script type="module" src="${API_URL}/widget.js"\n` +
  `        data-widget-key="${widgetKey}"></script>`;

// [field, label, has-flag, we-mint-it?] — generatable secrets are the ones this
// service signs with (random keys), not externally-issued API credentials.
const SECRET_FIELDS = [
  ['openai_key_primary', 'OpenAI key (primary)', 'has_openai_key', false],
  ['openai_key_fallback', 'OpenAI key (fallback)', 'has_openai_key_fallback', false],
  ['handshake_secret', 'Widget handshake secret', 'has_handshake_secret', true],
  ['turnstile_secret', 'Turnstile secret key', 'has_turnstile_secret', false],
  ['telegram_bot_token', 'Telegram bot token', 'has_telegram_bot_token', false],
  ['player_api_key', 'Player API key', 'has_player_api_key', false],
];

const mono = { fontFamily: 'monospace', fontSize: 13 };

const ProductCard = ({ product, onChanged }) => {
  const notify = useNotify();
  const [name, setName] = useState(product.name);
  const [turnstileSiteKey, setTurnstileSiteKey] = useState(
    product.turnstile_site_key || ''
  );
  const [siteUrl, setSiteUrl] = useState(product.site_url || '');
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

  const copy = async (text, message) => {
    await navigator.clipboard.writeText(text);
    notify(message, { type: 'info' });
  };

  const saveSecrets = async () => {
    // Only persist fields the operator typed a value into; clearing is the
    // explicit Clear button (clearSecret), not an empty box.
    const fields = Object.fromEntries(
      Object.entries(secrets).filter(([, v]) => v !== undefined && v !== '')
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

  const clearSecret = async (field, label) => {
    if (!window.confirm(`Clear ${label}? It falls back to the deploy env value.`)) return;
    try {
      await httpClient(`${API_URL}/admin/products/${product.id}/secrets`, {
        method: 'PUT',
        body: JSON.stringify({ [field]: '' }),
      });
      notify(`${label} cleared`, { type: 'success' });
      setSecrets((s) => ({ ...s, [field]: '' }));
      onChanged();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Clear failed', { type: 'error' });
    }
  };

  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent>
        {/* --- identity row ------------------------------------------------ */}
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
          sx={{ mb: 2 }}
        >
          <TextField
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            label={`Product · ${product.slug}`}
            sx={{ minWidth: 220, flex: '1 1 260px' }}
          />
          <Button
            size="small"
            variant="outlined"
            onClick={() => saveProduct({ name })}
            sx={{ whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            Rename
          </Button>
          <FormControlLabel
            sx={{ flexShrink: 0 }}
            control={
              <Switch
                size="small"
                checked={product.active}
                onChange={(e) => saveProduct({ active: e.target.checked })}
              />
            }
            label={t('Active')}
          />
        </Stack>

        {/* --- widget key + embed snippet ---------------------------------- */}
        <Typography variant="subtitle2" gutterBottom>
          Widget key & embed
        </Typography>
        <TextField
          value={product.widget_key || ''}
          label={t('Widget key')}
          fullWidth
          size="small"
          margin="dense"
          slotProps={{
            input: {
              readOnly: true,
              sx: mono,
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    onClick={() => copy(product.widget_key, 'Widget key copied')}
                    aria-label="Copy widget key"
                  >
                    <ContentCopyIcon fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ),
            },
          }}
        />
        <TextField
          value={embedSnippet(product.widget_key)}
          label={t('Embed snippet')}
          fullWidth
          multiline
          size="small"
          margin="dense"
          slotProps={{ input: { readOnly: true, sx: mono } }}
        />
        <Stack direction="row" spacing={1} sx={{ mt: 1, mb: 2 }} flexWrap="wrap" useFlexGap>
          <Button
            size="small"
            variant="outlined"
            startIcon={<ContentCopyIcon fontSize="small" />}
            onClick={() => copy(embedSnippet(product.widget_key), 'Embed snippet copied')}
          >
            Copy embed snippet
          </Button>
          <Button size="small" color="warning" variant="outlined" onClick={rotateKey}>
            Rotate key
          </Button>
        </Stack>

        {/* --- Cloudflare Turnstile (per client domain) ---------------------- */}
        <Typography variant="subtitle2" gutterBottom>
          Cloudflare Turnstile
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Each client domain runs its own Turnstile widget (create it as an
          Invisible widget in the Cloudflare dashboard) — set that widget&apos;s
          site key here (the secret key goes into Secrets below). Leave empty to
          fall back to the deploy env keys. Verification is advisory: if
          Turnstile is blocked or unreachable for a player, the check is
          skipped and the other anti-spam layers still apply.
        </Typography>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
          sx={{ mb: 2 }}
        >
          <TextField
            size="small"
            label={t('Turnstile site key')}
            value={turnstileSiteKey}
            onChange={(e) => setTurnstileSiteKey(e.target.value)}
            sx={{ minWidth: 220, flex: '1 1 320px' }}
            slotProps={{
              input: {
                sx: mono,
                endAdornment: (
                  <InputAdornment position="end">
                    <SetBadge set={Boolean(product.turnstile_site_key)} />
                  </InputAdornment>
                ),
              },
            }}
          />
          <Button
            size="small"
            variant="outlined"
            onClick={() => saveProduct({ turnstile_site_key: turnstileSiteKey })}
            sx={{ whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            Save site key
          </Button>
        </Stack>

        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
          sx={{ mb: 2 }}
        >
          <TextField
            size="small"
            label={t('Site URL (home page)')}
            placeholder="https://example.com/"
            helperText="Telegram hand-off 'support on the site' button lands here"
            value={siteUrl}
            onChange={(e) => setSiteUrl(e.target.value)}
            sx={{ minWidth: 220, flex: '1 1 320px' }}
            slotProps={{
              input: {
                sx: mono,
                endAdornment: (
                  <InputAdornment position="end">
                    <SetBadge set={Boolean(product.site_url)} />
                  </InputAdornment>
                ),
              },
            }}
          />
          <Button
            size="small"
            variant="outlined"
            onClick={() => saveProduct({ site_url: siteUrl })}
            sx={{ whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            Save site URL
          </Button>
        </Stack>

        {/* --- secrets (write-only, open by default) ----------------------- */}
        <Typography variant="subtitle2" gutterBottom>
          Secrets
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Write-only (encrypted at rest). A green check means a value is
          configured. Leave a field untouched to keep it; use Clear to remove it
          (fall back to env).
        </Typography>
        <Grid container spacing={1}>
          {SECRET_FIELDS.map(([field, label, flag, minted]) => (
            <Grid size={{ xs: 12, sm: 6 }} key={field}>
              <SecretField
                label={label}
                set={Boolean(product[flag])}
                value={secrets[field]}
                onChange={(e) => setSecrets({ ...secrets, [field]: e.target.value })}
                onClear={() => clearSecret(field, label)}
                onGenerate={
                  minted
                    ? (v) => setSecrets((s) => ({ ...s, [field]: v }))
                    : undefined
                }
              />
            </Grid>
          ))}
        </Grid>
        <Button variant="contained" size="small" onClick={saveSecrets} sx={{ mt: 1.5 }}>
          Save secrets
        </Button>
      </CardContent>
    </Card>
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
    <Stack
      direction="row"
      spacing={1}
      alignItems="center"
      flexWrap="wrap"
      useFlexGap
      sx={{ pt: 1 }}
    >
      <TextField
        size="small"
        label={t('New product slug')}
        value={slug}
        onChange={(e) => setSlug(e.target.value)}
        sx={{ flex: '1 1 220px', maxWidth: 360 }}
      />
      <TextField
        size="small"
        label={t('Name')}
        value={name}
        onChange={(e) => setName(e.target.value)}
        sx={{ flex: '1 1 220px', maxWidth: 360 }}
      />
      <Button
        size="small"
        variant="outlined"
        onClick={create}
        disabled={!slug || !name}
        sx={{ whiteSpace: 'nowrap', flexShrink: 0 }}
      >
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
    <Card sx={{ mb: 3 }}>
      <CardContent>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
        >
          <TextField
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            label={`Partner · ${partner.slug}`}
            sx={{ minWidth: 220, flex: '1 1 260px' }}
          />
          <Button
            size="small"
            variant="outlined"
            onClick={() => savePartner({ name })}
            sx={{ whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            Rename
          </Button>
          <FormControlLabel
            sx={{ flexShrink: 0 }}
            control={
              <Switch
                size="small"
                checked={partner.active}
                onChange={(e) => savePartner({ active: e.target.checked })}
              />
            }
            label={t('Active')}
          />
        </Stack>
        <Divider sx={{ my: 2 }} />
        {(partner.products || []).map((p) => (
          <ProductCard key={p.id} product={p} onChanged={onChanged} />
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
      <Title title={t('Structure')} />
      {structure.global_role === 'admin' && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
          sx={{ mb: 2 }}
        >
          <TextField
            size="small"
            label={t('New partner slug')}
            value={newPartner.slug}
            onChange={(e) => setNewPartner({ ...newPartner, slug: e.target.value })}
            sx={{ flex: '1 1 220px', maxWidth: 360 }}
          />
          <TextField
            size="small"
            label={t('Name')}
            value={newPartner.name}
            onChange={(e) => setNewPartner({ ...newPartner, name: e.target.value })}
            sx={{ flex: '1 1 220px', maxWidth: 360 }}
          />
          <Button
            size="small"
            variant="outlined"
            onClick={createPartner}
            disabled={!newPartner.slug || !newPartner.name}
            sx={{ whiteSpace: 'nowrap', flexShrink: 0 }}
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
