import { Fragment, useEffect, useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { Title, useNotify, usePermissions } from 'react-admin';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
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
import InputAdornment from '@mui/material/InputAdornment';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import DeleteIcon from '@mui/icons-material/Delete';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import HelpOutlineIcon from '@mui/icons-material/HelpOutlined';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import PublicIcon from '@mui/icons-material/Public';
import { useMemo } from 'react';
import { API_URL, httpClient } from '../httpClient';
import { getProductId, getScopeName, withProduct } from '../productScope';
import { t } from '../i18n';
import rich from '../components/Rich';
import {
  GROUP_HELP,
  GROUP_LABELS,
  MODULES,
  fieldsForModule,
} from './settingsSchema';

// Sub-headers for grouped fields inside a settings group (schema `section`).
const SECTION_LABELS = {
  progression: 'Photo unlock progression (Stage × VIP Level)',
  agent: 'Proactive agent (event-driven)',
  guards: 'Send-frequency guards (per-player protection)',
  delivery: 'Delivery',
  media: 'Media normalization (photo storage)',
};

// Per-module tab labels where the group name alone would mislead (the
// support module shows only the CHAT slice of `general`; the retention module
// shows only the Telegram slice of `antispam`).
const TAB_LABELS = {
  'support:general': 'Chat limits',
  'retention:antispam': 'Anti-spam',
};

// The plain-language "how it works" block per module — an intro plus concrete
// bullet points (with links to the deeper guide pages), the first thing a
// non-technical manager sees on the page.
const MODULE_HOWTO = {
  support: {
    intro:
      'The support widget answers players on the site from the per-topic Knowledge base. Before the model sees a message it passes the anti-spam gates below; the chat limits bound one session. Content is edited in the Support chat section (KB texts, prompt variables) and the Common section (translations, site map).',
    points: [
      'Gate order for every message: rate limit → cooldown → length cap → low-content guard → injection scan. A message rejected by a gate never reaches the model — attacks and spam cost no tokens.',
      '**Anti-spam** ships with sensible values; raise the rate limit only if real players actually hit it. The injection hard-block and low-content guard are safe to keep on.',
      '**Chat limits** bound one session: how long it stays valid, how many messages before a forced hand-off to a human, and how many recent turns the model sees (the full transcript is always stored).',
      'The whole support pipeline, the content map ("where do I fix this text?") and a step-by-step testing checklist live on the [How it works](#/support-guide) page.',
    ],
  },
  retention: {
    intro:
      'The Telegram bot re-engages players: it chats in persona, sends photos gated by Stage × VIP level, and the proactive agent reacts to casino events (deposits, level-ups, losses). The guards below are the dials for how often one player may be written to — the agent can never exceed them.',
    points: [
      'Two regimes: the **dialogue bot** answers when the player writes; the **proactive agent** writes first in reaction to casino events. The «Send-frequency guards» section is the hard rail for the agent — daily cap, min gap, cooldowns, quiet hours, budget.',
      'Photos unlock through two gates at once: chat progression (**Stage**) × the player’s VIP tier (**Level**) — the «Photo unlock progression» section below sets both ladders; the same numbers are stamped on each photo in Media.',
      'The agent’s own switches (enabled, dry-run, worker interval, budget) are in the «Proactive agent» section. The full pipeline, guard reference with current values and a testing checklist are on the agent’s [How it works & testing](#/retention-agent?tab=guide) tab.',
    ],
  },
  core: {
    intro:
      'These settings are shared by BOTH bots: which OpenAI model answers (and its budgets/timeouts), which languages the assistant supports, and technical request limits. Change with care — they apply to every product without its own override.',
    points: [
      '**AI model** — the model id plus its budgets and timeouts. «Max output tokens» INCLUDES the model’s hidden reasoning: keep it generous (≈2000), too low and answers can come back empty.',
      '**Languages** — the supported set and the default. A newly added language starts on English copy and becomes translatable in Translations; answers always follow the player’s language.',
      '**General** — technical lifetimes and caps (sessions, admin tokens, request bodies). These rarely need changing.',
      'Every setting resolves per product: product override → global default → deploy env → built-in default. The banner above the form shows which layer you are editing right now.',
    ],
  },
};

// A label with an (i) tooltip carrying the long explanation — short label on
// the input, the full description one hover/tap away.
const LONG_HELP = 96;
const InfoAdornment = ({ help }) => (
  <InputAdornment position="end">
    <Tooltip title={help} enterTouchDelay={0} leaveTouchDelay={4000}>
      <InfoOutlinedIcon
        fontSize="small"
        sx={{ color: 'text.disabled', cursor: 'help' }}
      />
    </Tooltip>
  </InputAdornment>
);

// ---------------------------------------------------------------------------
// One typed field
// ---------------------------------------------------------------------------
const Field = ({ field, value, onChange, form, locked = false }) => {
  const { type } = field;
  const label = t(field.label);
  const help = t(field.help);
  const longHelp = (help || '').length > LONG_HELP;

  // A deploy-wide (global-only) field with a product selected: the backend
  // strips it from product-layer saves, so show it read-only with a pointer
  // to the scope where it CAN be edited.
  if (locked) {
    return (
      <Grid size={{ xs: 12, sm: 6 }}>
        <TextField
          label={`${label} — ${t('global')}`}
          value={type === 'bool' ? (value ? t('on') : t('off')) : String(value ?? '')}
          helperText={t('Deploy-wide setting: switch the header to “All products” to edit it.')}
          fullWidth
          size="small"
          disabled
          slotProps={{ input: { endAdornment: <InfoAdornment help={help} /> } }}
        />
      </Grid>
    );
  }

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
              label={`${t('Stage')} 1`}
              value="0"
              size="small"
              fullWidth
              disabled
              helperText={t('free / baseline')}
            />
          </Grid>
          {arr.map((n, i) => (
            <Grid size={{ xs: 6, sm: 3, md: 2 }} key={i}>
              <TextField
                label={`${t('Stage')} ${i + 2}`}
                type="number"
                value={n}
                onChange={(e) => setAt(i, e.target.value)}
                size="small"
                fullWidth
                helperText={t('msgs')}
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
            {t('+ Add stage')}
          </Button>
          {arr.length > 0 && (
            <Button size="small" color="error" onClick={() => onChange(arr.slice(0, -1))}>
              {t('− Remove last')}
            </Button>
          )}
        </Stack>
      </Grid>
    );
  }

  if (type === 'bool') {
    return (
      <Grid size={{ xs: 12, sm: 6 }}>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <FormControlLabel
            control={
              <Switch
                checked={Boolean(value)}
                onChange={(e) => onChange(e.target.checked)}
              />
            }
            label={label}
            sx={{ mr: 0 }}
          />
          <Tooltip title={help} enterTouchDelay={0} leaveTouchDelay={4000}>
            <InfoOutlinedIcon
              fontSize="small"
              sx={{ color: 'text.disabled', cursor: 'help' }}
            />
          </Tooltip>
        </Stack>
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
          helperText={longHelp ? undefined : help}
          fullWidth
          size="small"
          slotProps={longHelp ? { input: { endAdornment: <InfoAdornment help={help} /> } } : undefined}
        >
          {field.options.map((o) => (
            <MenuItem key={o} value={o}>
              {o === '' ? t('(model default)') : o}
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
      ? form[field.orderByField].map((x) => String(x).toLowerCase())
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
        helperText={longHelp ? undefined : help}
        fullWidth
        size="small"
        slotProps={{
          ...(type === 'string' ? {} : { htmlInput: { min: field.min, max: field.max, step: field.step } }),
          ...(longHelp ? { input: { endAdornment: <InfoAdornment help={help} /> } } : {}),
        }}
      />
    </Grid>
  );
};

// ---------------------------------------------------------------------------
// One group (typed fields) — saves the whole group object. `fields` is the
// module-filtered slice; the form still carries the FULL resolved group, so
// the save round-trips unseen fields unchanged.
// ---------------------------------------------------------------------------
const GroupEditor = ({ group, fields, resolved, onSaved, scopeLabel, productScoped }) => {
  const notify = useNotify();
  // Managers are read-only server-side (403 on PUT) — pre-disable the save,
  // matching the SiteMap/Translations pattern.
  const { permissions } = usePermissions();
  const readOnly = permissions !== 'admin';
  const [form, setForm] = useState(() => ({ ...(resolved || {}) }));
  const [saving, setSaving] = useState(false);

  const setField = (name, v) => setForm((f) => ({ ...f, [name]: v }));

  const save = async () => {
    setSaving(true);
    try {
      await httpClient(withProduct(`${API_URL}/admin/settings/${group}`), {
        method: 'PUT',
        body: JSON.stringify({ value: form }),
      });
      notify(`${t(GROUP_LABELS[group] || group)} — ${t('settings saved')}`, { type: 'success' });
      onSaved?.();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t(GROUP_HELP[group])}
        </Typography>
        {group === 'retention' && (
          <Alert severity="info" sx={{ mb: 2 }}>
            <AlertTitle>{t('How photos unlock — two gates, both must pass')}</AlertTitle>
            {t(
              'A photo carries a Stage (how explicit/hot it is, 1 = softest) and a Level (the minimum VIP tier that may see it). To receive a photo a player needs BOTH: enough chatting to reach that Stage (the thresholds below) AND a high enough VIP tier — the tier caps the top Stage they can ever reach and clears the photo’s Level. Set the same numbers on each photo in Media.'
            )}
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
                      {t(SECTION_LABELS[f.section] || f.section)}
                    </Typography>
                  </Grid>
                )}
                <Field
                  field={f}
                  value={form[f.name]}
                  form={form}
                  locked={Boolean(f.globalOnly && productScoped)}
                  onChange={(v) => setField(f.name, v)}
                />
              </Fragment>
            );
          })}
        </Grid>
        <Button variant="contained" onClick={save} disabled={saving || readOnly} sx={{ mt: 2 }}>
          {saving ? t('Saving…') : `${t('Save')} — ${t(GROUP_LABELS[group] || group)}${scopeLabel}`}
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
  const { permissions } = usePermissions();
  const readOnly = permissions !== 'admin';
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
      notify(t('Keep at least one supported language'), { type: 'warning' });
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
      notify(t('Languages saved'), { type: 'success' });
      onSaved?.();
    } catch (e) {
      notify(e.body?.detail || e.message || t('Save failed'), { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t(GROUP_HELP.language)}
        </Typography>

        <Typography variant="subtitle2" gutterBottom>
          {t('Default answer language')}
        </Typography>
        <TextField
          select
          size="small"
          value={supported.includes(def) ? def : ''}
          onChange={(e) => setDef(e.target.value)}
          sx={{ minWidth: 240, mb: 2 }}
          helperText={t("Fallback when the player's language can't be detected.")}
        >
          {supported.map((c) => (
            <MenuItem key={c} value={c}>
              {nameFor(c)} ({c})
            </MenuItem>
          ))}
        </TextField>

        <Typography variant="subtitle2" gutterBottom>
          {t('Supported languages')} ({supported.length})
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
          {t(
            'A language added here starts on English copy and becomes translatable in the Translations tab. Edit the display name (optional) to override the built-in name.'
          )}
        </Typography>
        <Stack spacing={1} sx={{ mb: 2 }}>
          {supported.map((c) => (
            <Stack key={c} direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              <Chip label={c} size="small" sx={{ minWidth: 52 }} />
              <TextField
                size="small"
                value={names[c] ?? ''}
                placeholder={nameFor(c)}
                onChange={(e) => setNames((n) => ({ ...n, [c]: e.target.value }))}
                sx={{ minWidth: { xs: 150, sm: 220 }, flex: '1 1 150px', maxWidth: 320 }}
              />
              {def === c && (
                <Chip label={t('default')} size="small" color="primary" variant="outlined" />
              )}
              <IconButton
                size="small"
                color="error"
                onClick={() => removeLanguage(c)}
                aria-label={`Remove ${c}`}
                disabled={supported.length <= 1 || readOnly}
              >
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Stack>
          ))}
        </Stack>

        <Divider sx={{ my: 2 }} />
        <Typography variant="subtitle2" gutterBottom>
          {t('Add a language')}
        </Typography>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <TextField
            select
            size="small"
            label={t('ISO 639-1 language')}
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
          <Button variant="outlined" onClick={addLanguage} disabled={!addCode || readOnly}>
            {t('Add')}
          </Button>
        </Stack>

        <Box sx={{ mt: 2 }}>
          <Button variant="contained" onClick={save} disabled={saving || readOnly}>
            {saving ? t('Saving…') : `${t('Save')} — ${t('Languages')}${scopeLabel}`}
          </Button>
        </Box>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// One settings surface (module = a slice of the groups). Exported so the
// Retention → Settings page can embed the retention module next to the
// Telegram config and Managers tabs; the group tab is local state, so an
// embedding page's own ?tab= param is never clobbered.
// ---------------------------------------------------------------------------
export const SettingsModule = ({ module }) => {
  const notify = useNotify();
  const mod = MODULES[module];
  const [settings, setSettings] = useState(null);
  const [meta, setMeta] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [tabState, setTabState] = useState(null);

  const load = () =>
    Promise.all([
      httpClient(withProduct(`${API_URL}/admin/settings`)),
      httpClient(withProduct(`${API_URL}/admin/meta`)),
    ])
      .then(([s, m]) => {
        setLoadError(null);
        setSettings(s.json);
        setMeta(m.json);
      })
      .catch((e) => {
        // A partner/product-scoped admin at the All-products scope gets a 403
        // (global settings need a global account) — show a way out instead of
        // an eternal "Loading…".
        setLoadError(e.body?.detail || e.message || t('Load failed'));
        notify(e.message || t('Load failed'), { type: 'error' });
      });

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Groups visible in this module: language is its own editor (core only);
  // other groups appear when they have at least one field tagged for the module.
  const groups = mod.groups.filter(
    (g) =>
      g === 'language' ||
      (fieldsForModule(g, module).length > 0 &&
        (settings?.keys || []).includes(g))
  );

  const tab = groups.includes(tabState) ? tabState : groups[0];
  const setTab = (v) => setTabState(v);

  if (!settings && loadError) {
    return (
      <Box sx={{ maxWidth: 720 }}>
        <Alert severity="warning">
          <AlertTitle>{t('Settings could not be loaded')}</AlertTitle>
          {String(loadError)}
        </Alert>
      </Box>
    );
  }
  if (!settings) return <Box>{t('Loading…')}</Box>;

  const productId = getProductId();
  const scopeName = getScopeName();
  // Rides in every Save button so the write scope is unmistakable even after
  // the operator scrolled the banner away.
  const scopeLabel = productId
    ? ` (${t('for')} ${scopeName || `#${productId}`})`
    : ` (${t('GLOBAL defaults')})`;

  return (
    <Box sx={{ maxWidth: 1000 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        {t(mod.help)}
      </Typography>

      <Accordion disableGutters sx={{ mb: 2 }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Stack direction="row" spacing={1} alignItems="center">
            <HelpOutlineIcon fontSize="small" color="action" />
            <Typography variant="subtitle2">{t('How it works')}</Typography>
          </Stack>
        </AccordionSummary>
        <AccordionDetails>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {t(MODULE_HOWTO[module].intro)}
          </Typography>
          <Box component="ul" sx={{ pl: 3, my: 0 }}>
            {MODULE_HOWTO[module].points.map((p) => (
              <Typography component="li" variant="body2" color="text.secondary" key={p} sx={{ mb: 0.5 }}>
                {rich(t(p))}
              </Typography>
            ))}
          </Box>
        </AccordionDetails>
      </Accordion>

      {productId ? (
        <Alert severity="success" sx={{ mb: 2 }}>
          <AlertTitle>
            {t('Product settings')} — {scopeName || `#${productId}`}
          </AlertTitle>
          {t('Values you save here override the global defaults for this product only.')}
        </Alert>
      ) : (
        <Alert severity="warning" icon={<PublicIcon />} sx={{ mb: 2 }}>
          <AlertTitle>{t('Global defaults — the fallback for EVERY product')}</AlertTitle>
          {t(
            'No product is selected, so you are editing the deploy-wide fallback layer that applies to every product without its own override. To tune a single casino, pick it in the Partner → Product switcher at the top-right.'
          )}
        </Alert>
      )}
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {t(
          'Hot-reloaded runtime settings — effective values shown (precedence product → global → env → default). The backend validates and rejects out-of-range values.'
        )}
      </Typography>

      {groups.length > 1 && (
        <Tabs
          value={tab}
          onChange={(e, v) => setTab(v)}
          sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
          variant="scrollable"
          allowScrollButtonsMobile
        >
          {groups.map((g) => (
            <Tab
              key={g}
              value={g}
              label={t(TAB_LABELS[`${module}:${g}`] || GROUP_LABELS[g] || g)}
            />
          ))}
        </Tabs>
      )}

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
            // Key on the resolved values so the form re-syncs after a save —
            // server-side clamping/normalization (e.g. worker interval 5..3600)
            // becomes visible immediately instead of after a tab switch.
            key={`${module}:${g}:${JSON.stringify(settings.resolved?.[g] || {})}`}
            group={g}
            fields={fieldsForModule(g, module)}
            resolved={settings.resolved?.[g]}
            onSaved={load}
            scopeLabel={scopeLabel}
            productScoped={Boolean(productId)}
          />
        )
      )}
    </Box>
  );
};

// ---------------------------------------------------------------------------
// page — the standalone settings surfaces (?module=support|core), each linked
// from its own sidebar section. The retention module moved into the combined
// Retention → Settings page; legacy links redirect there.
// ---------------------------------------------------------------------------
const Settings = () => {
  const [params] = useSearchParams();
  const requested = params.get('module');
  if (requested === 'retention') {
    return <Navigate to="/retention-settings?tab=params" replace />;
  }
  const module = MODULES[requested] ? requested : 'core';
  const mod = MODULES[module];

  return (
    <Box sx={{ p: 2, maxWidth: 1000 }}>
      <Title title={t(mod.title)} />
      <Typography variant="h5" sx={{ mb: 0.5 }}>
        {t(mod.title)}
      </Typography>
      <SettingsModule module={module} />
    </Box>
  );
};

export default Settings;
