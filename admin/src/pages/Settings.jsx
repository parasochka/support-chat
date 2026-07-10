import { Fragment, useEffect, useMemo, useState } from 'react';
import { Title, useNotify } from 'react-admin';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import FormControlLabel from '@mui/material/FormControlLabel';
import Grid from '@mui/material/Grid';
import IconButton from '@mui/material/IconButton';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import DeleteIcon from '@mui/icons-material/Delete';
import PublicIcon from '@mui/icons-material/Public';
import { API_URL, httpClient } from '../httpClient';
import { getProductId, getScopeName, withProduct } from '../productScope';
import { GROUP_FIELDS, GROUP_HELP, GROUP_LABELS } from './settingsSchema';

// Content-tuning group edited from the Prompt page; kept out of this editor.
const SKIP_GROUPS = ['escalation'];
const GROUP_ORDER = ['antispam', 'model', 'general', 'retention', 'language'];

// Sub-headers for grouped fields inside a settings group (schema `section`).
const SECTION_LABELS = {
  dialogue: 'Dialogue & photos (the reactive chat)',
  agent: 'Proactive agent (event-driven)',
  inactivity: 'Inactivity ladder (write-first to silent players)',
  guards: 'Send-frequency guards (per-player protection)',
  progression: 'Photo unlock progression (Stage × VIP Level)',
  plumbing: 'Plumbing (rarely touched)',
};

// ---------------------------------------------------------------------------
// One typed field
// ---------------------------------------------------------------------------
const Field = ({ field, value, onChange, form }) => {
  const { type, label, help } = field;

  // Explicitness-stage thresholds shown as one labelled column per stage
  // (Stage 1 is always the free baseline), instead of an opaque textarea list.
  if (type === 'stagethresholds') {
    const arr = Array.isArray(value) ? value : [];
    const setAt = (i, n) => {
      const next = [...arr];
      next[i] = Math.max(0, parseInt(n, 10) || 0);
      onChange(next);
    };
    return (
      <Grid size={{ xs: 12 }}>
        <Typography variant="subtitle2">{label}</Typography>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
          {help}
        </Typography>
        <Grid container spacing={1}>
          <Grid size={{ xs: 6, sm: 3, md: 2 }}>
            <TextField
              label="Stage 1"
              value="0"
              size="small"
              fullWidth
              disabled
              helperText="free / baseline"
            />
          </Grid>
          {arr.map((n, i) => (
            <Grid size={{ xs: 6, sm: 3, md: 2 }} key={i}>
              <TextField
                label={`Stage ${i + 2}`}
                type="number"
                value={n}
                onChange={(e) => setAt(i, e.target.value)}
                size="small"
                fullWidth
                helperText="msgs"
                slotProps={{ htmlInput: { min: 0 } }}
              />
            </Grid>
          ))}
        </Grid>
        <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
          <Button
            size="small"
            onClick={() => onChange([...arr, (arr[arr.length - 1] || 10) * 2])}
          >
            + Add stage
          </Button>
          {arr.length > 0 && (
            <Button size="small" color="error" onClick={() => onChange(arr.slice(0, -1))}>
              − Remove last
            </Button>
          )}
        </Stack>
      </Grid>
    );
  }

  if (type === 'bool') {
    return (
      <Grid size={{ xs: 12, sm: 6 }}>
        <FormControlLabel
          control={
            <Switch
              checked={Boolean(value)}
              onChange={(e) => onChange(e.target.checked)}
            />
          }
          label={label}
        />
        <Typography variant="caption" color="text.secondary" display="block" sx={{ ml: 6, mt: -0.5 }}>
          {help}
        </Typography>
      </Grid>
    );
  }

  if (type === 'select') {
    return (
      <Grid size={{ xs: 12, sm: 6 }}>
        <TextField
          select
          label={label}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}
          helperText={help}
          fullWidth
          size="small"
        >
          {field.options.map((o) => (
            <MenuItem key={o} value={o}>
              {o === '' ? '(model default)' : o}
            </MenuItem>
          ))}
        </TextField>
      </Grid>
    );
  }

  if (type === 'intlist' || type === 'strlist') {
    const text = Array.isArray(value) ? value.join('\n') : '';
    return (
      <Grid size={{ xs: 12, sm: 6 }}>
        <TextField
          label={label}
          value={text}
          onChange={(e) => {
            const lines = e.target.value.split('\n').map((s) => s.trim()).filter(Boolean);
            onChange(type === 'intlist' ? lines.map(Number).filter((n) => !Number.isNaN(n)) : lines);
          }}
          helperText={help}
          fullWidth
          size="small"
          multiline
          minRows={3}
        />
      </Grid>
    );
  }

  if (type === 'intmap') {
    const obj = value && typeof value === 'object' ? value : {};
    // Order the columns by a sibling ordered list (e.g. vip_tiers) so the
    // ceilings read lowest → highest tier instead of hash order.
    const order = field.orderByField && Array.isArray(form?.[field.orderByField])
      ? form[field.orderByField].map((t) => String(t).toLowerCase())
      : [];
    const keys = [...new Set([...order, ...Object.keys(obj)])].filter((k) => k in obj);
    const lo = field.min ?? 0;
    return (
      <Grid size={{ xs: 12 }}>
        <Typography variant="subtitle2">{label}</Typography>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
          {help}
        </Typography>
        <Grid container spacing={1}>
          {keys.map((k, i) => (
            <Grid size={{ xs: 6, sm: 3 }} key={k}>
              <TextField
                label={order.length ? `Level ${i} · ${k}` : k}
                type="number"
                value={obj[k]}
                onChange={(e) =>
                  onChange({ ...obj, [k]: Math.max(lo, Number(e.target.value) || lo) })
                }
                fullWidth
                size="small"
                slotProps={{ htmlInput: { min: lo } }}
              />
            </Grid>
          ))}
        </Grid>
      </Grid>
    );
  }

  // int / float / string
  return (
    <Grid size={{ xs: 12, sm: 6 }}>
      <TextField
        label={label}
        type={type === 'string' ? 'text' : 'number'}
        value={value ?? ''}
        onChange={(e) => {
          const raw = e.target.value;
          if (type === 'string') return onChange(raw);
          if (raw === '') return onChange('');
          onChange(type === 'float' ? Number(raw) : parseInt(raw, 10));
        }}
        helperText={help}
        fullWidth
        size="small"
        slotProps={
          type === 'string'
            ? undefined
            : { htmlInput: { min: field.min, max: field.max, step: field.step } }
        }
      />
    </Grid>
  );
};

// ---------------------------------------------------------------------------
// One group (typed fields) — saves the whole group object
// ---------------------------------------------------------------------------
const GroupEditor = ({ group, resolved, onSaved, scopeLabel }) => {
  const notify = useNotify();
  const [form, setForm] = useState(() => ({ ...(resolved || {}) }));
  const [saving, setSaving] = useState(false);
  const fields = GROUP_FIELDS[group] || [];

  const setField = (name, v) => setForm((f) => ({ ...f, [name]: v }));

  const save = async () => {
    setSaving(true);
    try {
      await httpClient(withProduct(`${API_URL}/admin/settings/${group}`), {
        method: 'PUT',
        body: JSON.stringify({ value: form }),
      });
      notify(`${GROUP_LABELS[group] || group} settings saved`, { type: 'success' });
      onSaved?.();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {GROUP_HELP[group]}
        </Typography>
        {group === 'retention' && (
          <Alert severity="info" sx={{ mb: 2 }}>
            <AlertTitle>How photos unlock — two gates, both must pass</AlertTitle>
            A photo carries a <b>Stage</b> (how explicit/hot it is, 1 = softest) and a{' '}
            <b>Level</b> (the minimum VIP tier that may see it). To receive a photo a
            player needs BOTH: enough <b>chatting</b> to reach that Stage (the
            thresholds below) AND a high enough <b>VIP tier</b> — the tier caps the
            top Stage they can ever reach (its ceiling below) and clears the photo’s
            Level. Chatting alone never beats the tier ceiling; a high tier alone
            never skips the chatting. Set the same numbers on each photo in{' '}
            <b>Media</b>.
          </Alert>
        )}
        <Grid container spacing={2}>
          {fields.map((f, i) => {
            const startsSection = f.section && f.section !== fields[i - 1]?.section;
            return (
              <Fragment key={f.name}>
                {startsSection && (
                  <Grid size={{ xs: 12 }}>
                    <Divider sx={{ mb: 1 }} />
                    <Typography variant="subtitle1">
                      {SECTION_LABELS[f.section] || f.section}
                    </Typography>
                  </Grid>
                )}
                <Field field={f} value={form[f.name]} form={form} onChange={(v) => setField(f.name, v)} />
              </Fragment>
            );
          })}
        </Grid>
        <Button variant="contained" onClick={save} disabled={saving} sx={{ mt: 2 }}>
          {saving ? 'Saving…' : `Save ${GROUP_LABELS[group] || group}${scopeLabel}`}
        </Button>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Languages editor (supported set + default + custom names + add-a-language)
// ---------------------------------------------------------------------------
const LanguageEditor = ({ resolved, overrides, meta, onSaved, scopeLabel }) => {
  const notify = useNotify();
  const lang = resolved?.language || {};
  const [supported, setSupported] = useState(() => [...(lang.supported || [])]);
  const [def, setDef] = useState(lang.default || 'en');
  const [names, setNames] = useState(() => ({ ...(overrides?.language?.names || {}) }));
  const [addCode, setAddCode] = useState('');
  const [saving, setSaving] = useState(false);

  const nameFor = (code) => {
    const known = (meta?.languages || []).find((l) => l.code === code);
    return names[code] || known?.name || code.toUpperCase();
  };

  const addable = useMemo(
    () => (meta?.iso_catalog || []).filter((l) => !supported.includes(l.code)),
    [meta, supported]
  );

  const addLanguage = () => {
    if (!addCode || supported.includes(addCode)) return;
    setSupported((s) => [...s, addCode]);
    setAddCode('');
  };

  const removeLanguage = (code) => {
    const next = supported.filter((c) => c !== code);
    setSupported(next);
    if (def === code) setDef(next[0] || 'en');
    setNames((n) => {
      const { [code]: _drop, ...rest } = n;
      return rest;
    });
  };

  const save = async () => {
    if (!supported.length) {
      notify('Keep at least one supported language', { type: 'warning' });
      return;
    }
    // Only persist non-empty custom names for still-supported codes.
    const cleanNames = {};
    supported.forEach((c) => {
      if (names[c] && names[c].trim()) cleanNames[c] = names[c].trim();
    });
    setSaving(true);
    try {
      await httpClient(withProduct(`${API_URL}/admin/settings/language`), {
        method: 'PUT',
        body: JSON.stringify({
          value: { default: supported.includes(def) ? def : supported[0], supported, names: cleanNames },
        }),
      });
      notify('Languages saved', { type: 'success' });
      onSaved?.();
    } catch (e) {
      notify(e.body?.detail || e.message || 'Save failed', { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {GROUP_HELP.language}
        </Typography>

        <Typography variant="subtitle2" gutterBottom>
          Default answer language
        </Typography>
        <TextField
          select
          size="small"
          value={supported.includes(def) ? def : ''}
          onChange={(e) => setDef(e.target.value)}
          sx={{ minWidth: 240, mb: 2 }}
          helperText="Fallback when the player's language can't be detected."
        >
          {supported.map((c) => (
            <MenuItem key={c} value={c}>
              {nameFor(c)} ({c})
            </MenuItem>
          ))}
        </TextField>

        <Typography variant="subtitle2" gutterBottom>
          Supported languages ({supported.length})
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
          A language added here starts on English copy and becomes translatable in the Translations tab.
          Edit the display name (optional) to override the built-in name.
        </Typography>
        <Stack spacing={1} sx={{ mb: 2 }}>
          {supported.map((c) => (
            <Stack key={c} direction="row" spacing={1} alignItems="center">
              <Chip label={c} size="small" sx={{ minWidth: 52 }} />
              <TextField
                size="small"
                value={names[c] ?? ''}
                placeholder={nameFor(c)}
                onChange={(e) => setNames((n) => ({ ...n, [c]: e.target.value }))}
                sx={{ minWidth: 220 }}
              />
              {def === c && <Chip label="default" size="small" color="primary" variant="outlined" />}
              <IconButton
                size="small"
                color="error"
                onClick={() => removeLanguage(c)}
                aria-label={`Remove ${c}`}
                disabled={supported.length <= 1}
              >
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Stack>
          ))}
        </Stack>

        <Divider sx={{ my: 2 }} />
        <Typography variant="subtitle2" gutterBottom>
          Add a language
        </Typography>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <TextField
            select
            size="small"
            label="ISO 639-1 language"
            value={addCode}
            onChange={(e) => setAddCode(e.target.value)}
            sx={{ minWidth: 280 }}
          >
            {addable.map((l) => (
              <MenuItem key={l.code} value={l.code}>
                {l.name} ({l.code})
              </MenuItem>
            ))}
          </TextField>
          <Button variant="outlined" onClick={addLanguage} disabled={!addCode}>
            Add
          </Button>
        </Stack>

        <Box sx={{ mt: 2 }}>
          <Button variant="contained" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : `Save languages${scopeLabel}`}
          </Button>
        </Box>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------
const Settings = () => {
  const notify = useNotify();
  const [settings, setSettings] = useState(null);
  const [meta, setMeta] = useState(null);
  const [tab, setTab] = useState('antispam');

  const load = () =>
    Promise.all([
      httpClient(withProduct(`${API_URL}/admin/settings`)),
      httpClient(withProduct(`${API_URL}/admin/meta`)),
    ])
      .then(([s, m]) => {
        setSettings(s.json);
        setMeta(m.json);
      })
      .catch((e) => notify(e.message || 'Load failed', { type: 'error' }));

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!settings) return <Box sx={{ p: 2 }}>Loading…</Box>;

  const groups = GROUP_ORDER.filter(
    (k) => k === 'language' || ((settings.keys || []).includes(k) && !SKIP_GROUPS.includes(k))
  );
  const productId = getProductId();
  const scopeName = getScopeName();
  // Rides in every Save button so the write scope is unmistakable even after
  // the operator scrolled the banner away.
  const scopeLabel = productId
    ? ` for ${scopeName || `product #${productId}`}`
    : ' — GLOBAL defaults';

  return (
    <Box sx={{ p: 2, maxWidth: 1000 }}>
      <Title title="Settings" />
      {productId ? (
        <Alert severity="success" sx={{ mb: 2 }}>
          <AlertTitle>
            Product settings — {scopeName || `product #${productId}`}
          </AlertTitle>
          Values you save here override the global defaults for this product
          only.
        </Alert>
      ) : (
        <Alert severity="warning" icon={<PublicIcon />} sx={{ mb: 2 }}>
          <AlertTitle>Global defaults — the fallback for EVERY product</AlertTitle>
          No product is selected, so you are editing the deploy-wide fallback
          layer (model tuning, limits, languages) that applies to every product
          without its own override. Writing here needs a global admin account.
          To tune a single casino, pick it in the Partner → Product switcher at
          the top-right. Per-product secrets (OpenAI/API keys) live on the
          Structure page, not here.
        </Alert>
      )}
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Hot-reloaded runtime settings — effective values shown (precedence
        product&nbsp;→ global&nbsp;→ env&nbsp;→ default). The backend validates and
        rejects out-of-range values.
      </Typography>

      <Tabs
        value={tab}
        onChange={(e, v) => setTab(v)}
        sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
        variant="scrollable"
        allowScrollButtonsMobile
      >
        {groups.map((g) => (
          <Tab key={g} value={g} label={GROUP_LABELS[g] || g} />
        ))}
      </Tabs>

      {groups.map((g) =>
        g !== tab ? null : g === 'language' ? (
          <LanguageEditor
            key={g}
            resolved={settings.resolved}
            overrides={settings.overrides}
            meta={meta}
            onSaved={load}
            scopeLabel={scopeLabel}
          />
        ) : (
          <GroupEditor
            key={g}
            group={g}
            resolved={settings.resolved?.[g]}
            onSaved={load}
            scopeLabel={scopeLabel}
          />
        )
      )}
    </Box>
  );
};

export default Settings;
