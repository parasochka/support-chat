import { useCallback, useEffect, useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { Title, useNotify } from 'react-admin';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { API_URL, httpClient } from '../httpClient';
import { getProductId } from '../productScope';
import { FunnelBars,
  MiniBarChart,
  SeriesLineChart,
  TelegramCostCharts, usd } from '../components/charts';
import RequireProduct from '../components/RequireProduct';
import TextStats from '../components/TextStats';
import PromptBlock from '../components/PromptBlock';
import rich from '../components/Rich';
import AlgorithmMapTab from './RetentionAlgorithmMap';
import PhotosTab from './retention/PhotosTab';
import ConversationsTab from './retention/ConversationsTab';
import AnalyticsTab from './retention/AnalyticsTab';
import { t } from '../i18n';
import { notifyError } from '../lib/notifyError';
import { useReadOnly } from '../lib/useReadOnly';

// ---------------------------------------------------------------------------
// Setup guide tab — the short "how to connect the bot" checklist that replaced
// the repo's RETENTION_SETUP.md. Everything product-level is configured right
// here in this section; only deploy env vars live outside.
// ---------------------------------------------------------------------------
const GUIDE_STEPS = [
  {
    title: t('1 · Create the bot'),
    body: rich(
      t(
        'Open [@BotFather](https://t.me/BotFather) → `/newbot`, pick a name and a username, copy the **token**. Optionally set the description, about text and avatar there too. Menu commands are not needed — players enter only via a deeplink from the site.'
      )
    ),
  },
  {
    title: t('2 · Create the channel (subscription gate)'),
    body: rich(
      t(
        'Create a Telegram **channel** and add the bot as a **channel administrator** — without admin rights the subscription check (`getChatMember`) fails and the gate never passes. Note the channel id (`@name` for public, `-100…` for private) and the channel URL (the gate\'s "open channel" button leads there).'
      )
    ),
  },
  {
    title: t('3 · Deploy env (Railway)'),
    body: rich(
      t(
        'Set on the service (not per product): `PUBLIC_BASE_URL` (public address, used to build the webhook URL), `TELEGRAM_WEBHOOK_SECRET` (random string, verified in the webhook header), `RETENTION_MEDIA_DIR` (mount path of an attached **Volume**, so photos survive redeploys) and `SECRETS_MASTER_KEY` (encrypts product secrets). The full env table is in the repo\'s README.'
      )
    ),
  },
  {
    title: t('4 · Connect this product'),
    body: rich(
      t(
        'On the [Telegram config](#/retention-settings) tab of Retention → Settings: switch on **Retention bot enabled**, fill the bot username, channel id and channel URL → **Save config**. In **Secrets** paste the bot token (and the Player API key, if the casino exposes a profile endpoint) → **Save secrets**. Then press **Register Telegram webhook** — it must report the webhook URL back.'
      )
    ),
  },
  {
    title: t('5 · Content and tuning'),
    body: rich(
      t(
        'Review the [Retention KB](#/retention?tab=kb) (one text document — what Nika may offer and talk about; a generic English starter is pre-filled, replace it with the brand\'s own), tune the Telegram persona in [Prompt variables](#/retention?tab=variables) (name/role/tone — empty fields use the built-in retention defaults), upload photos in [Media](#/retention?tab=photos) (bulk upload, then select them and press **Generate metadata** to have the AI fill the description, tags, `stage` = explicitness and `level_min` = VIP tier) and add live [Managers](#/retention-settings?tab=managers) (round-robin, sticky). Thresholds (daily photo cap, stage progression, VIP tiers, nonce TTL) are the [Parameters tab of Retention → Settings](#/retention-settings?tab=params); bot texts are the `rtn_*` keys in [Translations](#/translations).'
      )
    ),
  },
  {
    title: t('6 · Entry points'),
    body: rich(
      t(
        'Nothing extra to integrate for the main path: once the bot is enabled, the support widget\'s **escalation button** automatically routes the player into the bot (one-time deeplink, subscription gate on the way in, "go to a manager" in the menu). Optionally the site can mint its own per-player deeplink via `POST /api/retention/deeplink` — the full contract (handshake signing, profile pull/push) is documented at [/integration-telegram](/integration-telegram).'
      )
    ),
  },
];

const GuideTab = () => (
  <Box>
    {GUIDE_STEPS.map((s) => (
      <Card key={s.title} sx={{ mb: 1.5 }}>
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>
            {s.title}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {s.body}
          </Typography>
        </CardContent>
      </Card>
    ))}
    <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
      {t(
        'Quick test: open the deeplink → pass the channel gate → chat with Nika → ask for a photo → it arrives; write "my account is blocked" → she routes you out instead of answering support questions herself.'
      )}
    </Typography>
  </Box>
);

// ---------------------------------------------------------------------------
// Retention KB tab — ONE free-text document per product, edited exactly like a
// support topic's KB text: paste, change, save. New products arrive with the
// generic English starter document already seeded.
// ---------------------------------------------------------------------------
const KbTab = ({ productId }) => {
  const notify = useNotify();
  // Managers are read-only server-side (403 on write) — pre-disable saves.
  const readOnly = useReadOnly();
  const [text, setText] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    httpClient(`${API_URL}/admin/retention/kb/text?product_id=${productId}`)
      .then(({ json }) => setText(json.text ?? ''))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify]);

  const save = async () => {
    setSaving(true);
    try {
      const { json } = await httpClient(
        `${API_URL}/admin/retention/kb/text?product_id=${productId}`,
        { method: 'PUT', body: JSON.stringify({ text }) }
      );
      setText(json.text ?? '');
      notify(t('Retention KB saved'), { type: 'success' });
    } catch (e) {
      notifyError(notify, e, t('Save failed'));
    } finally {
      setSaving(false);
    }
  };

  if (text === null) return <Box sx={{ p: 2 }}>{t('Loading…')}</Box>;

  return (
    <Card>
      <CardContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          {rich(
            t(
              'The whole retention knowledge base as one text (Layer 2 of the retention prompt — what Nika may offer and talk about in Telegram). Keep it in English: it is the most token-efficient language for the model, and Nika answers in the player\'s language regardless. `{placeholders}` are substituted from KB variables.'
            )
          )}
        </Typography>
        <Alert severity="info" sx={{ mb: 1 }}>
          <b>{t('English only')}.</b>{' '}
          {t(
            'Model-facing content must be in English — the backend rejects other scripts. Player-facing copy belongs in Translations.'
          )}
        </Alert>
        <TextStats text={text} />
        <TextField
          value={text}
          onChange={(e) => setText(e.target.value)}
          multiline
          minRows={20}
          fullWidth
        />
        <Button variant="contained" onClick={save} disabled={saving || readOnly} sx={{ mt: 1.5 }}>
          {t('Save')}
        </Button>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Prompt variables tab — the Telegram-persona values (name, role, brand,
// products, tone of voice). A SEPARATE prompt with its own defaults: an empty
// field falls back to the built-in retention default, never to the support
// chat's value, so a support edit can never leak into the bot.
// ---------------------------------------------------------------------------
const VariablesTab = ({ productId }) => {
  const notify = useNotify();
  // Managers are read-only server-side (403 on write) — pre-disable saves.
  const readOnly = useReadOnly();
  const [vars, setVars] = useState(null);
  const [values, setValues] = useState({});
  const [saving, setSaving] = useState(false);

  const apply = useCallback((variables) => {
    setVars(variables || []);
    const v = {};
    (variables || []).forEach((x) => {
      v[x.key] = x.value ?? '';
    });
    setValues(v);
  }, []);

  useEffect(() => {
    httpClient(
      `${API_URL}/admin/retention/prompt-variables?product_id=${productId}`
    )
      .then(({ json }) => apply(json.variables))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify, apply]);

  const save = async () => {
    setSaving(true);
    try {
      const { json } = await httpClient(
        `${API_URL}/admin/retention/prompt-variables?product_id=${productId}`,
        { method: 'PUT', body: JSON.stringify({ value: values }) }
      );
      apply(json.variables);
      notify(t('Retention prompt variables saved'), { type: 'success' });
    } catch (e) {
      notifyError(notify, e, t('Save failed'));
    } finally {
      setSaving(false);
    }
  };

  if (vars === null) return <Box sx={{ p: 2 }}>{t('Loading…')}</Box>;

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        {rich(
          t(
            'These values uniquify the **Telegram retention persona** — a **separate prompt**, fully independent from the [support-chat prompt variables](#/prompt?tab=variables). An empty field **uses the built-in retention default** (shown as the placeholder); a support edit never leaks into the bot. Fill a field only where you want the Telegram persona to differ from that default.'
          )
        )}
      </Alert>
      <Card>
        <CardContent>
          <Alert severity="info" sx={{ mb: 1 }}>
            <b>{t('English only')}.</b>{' '}
            {t(
              'Model-facing content must be in English — the backend rejects other scripts. Player-facing copy belongs in Translations.'
            )}
          </Alert>
          <TextStats
            label={t('Total')}
            text={vars.map((v) => values[v.key] || v.default || '')}
          />
          {vars.map((v) => (
            <TextField
              key={v.key}
              label={v.key}
              helperText={`${v.description} ${t('Empty = the built-in retention default.')}`}
              value={values[v.key] ?? ''}
              onChange={(e) => setValues({ ...values, [v.key]: e.target.value })}
              placeholder={v.default}
              fullWidth
              multiline
              margin="normal"
            />
          ))}
          <Button variant="contained" onClick={save} disabled={saving || readOnly} sx={{ mt: 1 }}>
            {saving ? t('Saving…') : t('Save variables')}
          </Button>
        </CardContent>
      </Card>
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Prompt preview tab — read-only view of the assembled RETENTION prompt.
// Mirrors the support Prompt → Preview page exactly: it shows ONLY the
// assembled prompt (no variables table — those already-resolved values just
// took up space here). The variable VALUES are edited on the Prompt variables
// tab, same as the support prompt.
// ---------------------------------------------------------------------------
const PromptTab = ({ productId }) => {
  const notify = useNotify();
  const [preview, setPreview] = useState(null);

  useEffect(() => {
    httpClient(`${API_URL}/admin/retention/effective-prompt?product_id=${productId}`)
      .then(({ json }) => setPreview(json.effective_preview))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify]);

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {rich(
          t(
            'The complete retention prompt as the model receives it in the Telegram chat (read-only; language: {lang}). To change the wording, edit `prompts.py` and redeploy; the brand values are on the [Prompt variables](#/retention?tab=variables) tab.'
          ).replace('{lang}', preview?.example?.lang || '—')
        )}
      </Typography>
      <TextStats
        label={t('Total')}
        text={[preview?.system, preview?.user]}
        sx={{ mb: 1.5 }}
      />
      <PromptBlock
        title={t('System message (retention Layer 1 core + Layer 2 retention KB)')}
        text={preview?.system}
      />
      <PromptBlock
        title={t('User message (Layer 3: profile, language, photo candidates, guardrails)')}
        text={preview?.user}
      />
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Navigation between sections is the sidebar's job: each section is its own
// menu entry (How it works / KB / Prompt / Media / Conversations / Analytics),
// so there is no page-wide tab strip. The one exception: the Prompt section
// bundles its read-only preview and the editable variables as a small internal
// 2-tab strip (mirrors the Support "Prompt" page). The Telegram config,
// Managers and the `retention` settings group moved to the Retention →
// Settings page (/retention-settings); Idle pings became a tab of the
// Proactive agent page — legacy ?tab= links redirect below.
const COMPONENTS = {
  guide: GuideTab,
  algorithm: AlgorithmMapTab,
  kb: KbTab,
  prompt: PromptTab,
  variables: VariablesTab,
  photos: PhotosTab,
  chats: ConversationsTab,
  analytics: AnalyticsTab,
};

const PROMPT_SUBTABS = [
  ['prompt', t('Prompt preview')],
  ['variables', t('Prompt variables')],
];

// The "How it works" section bundles the setup checklist and the interactive
// algorithm map as a small internal 2-tab strip (same pattern as Prompt).
const GUIDE_SUBTABS = [
  ['guide', t('Setup guide')],
  ['algorithm', t('Algorithm map')],
];

// Tabs that used to live on this page and moved elsewhere (old bookmarks and
// cross-page links keep working).
const LEGACY_REDIRECTS = {
  config: '/retention-settings',
  managers: '/retention-settings?tab=managers',
  idle: '/retention-agent?tab=idle',
};

const Retention = () => {
  const [params, setParams] = useSearchParams();
  const productId = getProductId();
  const requested = params.get('tab');
  const tab = COMPONENTS[requested] ? requested : 'guide';

  if (LEGACY_REDIRECTS[requested]) {
    return <Navigate to={LEGACY_REDIRECTS[requested]} replace />;
  }

  // Retention data is strictly per-product; refuse to render without one so the
  // operator can't edit the default product by accident (same gate as KB /
  // Prompt / Translations).
  if (!productId) {
    return <RequireProduct title={t('Retention')} />;
  }

  const Component = COMPONENTS[tab];
  const subtabs =
    tab === 'prompt' || tab === 'variables'
      ? PROMPT_SUBTABS
      : tab === 'guide' || tab === 'algorithm'
        ? GUIDE_SUBTABS
        : null;

  return (
    <Box sx={{ p: 2 }}>
      <Title title={t('Retention')} />
      {subtabs && (
        <Tabs
          value={tab}
          onChange={(e, v) => setParams({ tab: v }, { replace: true })}
          variant="scrollable"
          allowScrollButtonsMobile
          sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}
        >
          {subtabs.map(([value, label]) => (
            <Tab key={value} value={value} label={label} />
          ))}
        </Tabs>
      )}
      <Component productId={productId} />
    </Box>
  );
};

export default Retention;
