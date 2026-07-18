/**
 * Retention → How it works → Algorithm map: an interactive block diagram of
 * the WHOLE retention algorithm — the dialogue turn, the casino data feed,
 * the event-driven proactive agent and the idle re-engagement ladder.
 *
 * Hand-rolled (no diagram library — the admin bundle stays code-split and
 * small): four flow cards, each a vertical chain of typed blocks. Clicking a
 * block expands it in place with a plain-language explanation, the settings
 * that govern exactly that step (deep-linked to their editors) and the module
 * that implements it. The legend chips highlight all blocks of one kind, so
 * "where are all the gates?" / "which steps cost model calls?" is one hover.
 *
 * The content mirrors the shipped pipeline (retention.py / player_sync.py /
 * retention_v2.py / retention_idle.py / delivery.py) — when a step changes in
 * code, update its block here (this page is the operator's mental model).
 */
import { useState } from 'react';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Link from '@mui/material/Link';
import Typography from '@mui/material/Typography';
import rich from '../components/Rich';
import { t } from '../i18n';

// ---------------------------------------------------------------------------
// Block kinds: one colour per role in the pipeline. Fixed accents (not theme
// palette) with alpha backgrounds so they read in light AND dark mode.
// ---------------------------------------------------------------------------
const KINDS = {
  input: { label: t('Input / trigger'), color: '#78909c' },
  gate: { label: t('Gate / filter'), color: '#f9a825' },
  model: { label: t('AI call (costs money)'), color: '#7e57c2' },
  action: { label: t('Send / action'), color: '#2e7d32' },
  store: { label: t('Data / ledger'), color: '#0288d1' },
};

const PARAMS = '#/retention-settings?tab=params';
const AGENT = '#/retention-agent';

// ---------------------------------------------------------------------------
// The four flows. Every step: {id, kind, label, sub?, note?, details, knobs?,
// module?}. `note` is the branch outcome shown on the block itself; `knobs`
// deep-link each governing setting to the page where it is edited.
// ---------------------------------------------------------------------------
const FLOWS = [
  {
    id: 'dialogue',
    title: t('1 · Player dialogue — a message in Telegram'),
    intro: t('What happens on every message the player writes to the bot.'),
    steps: [
      {
        id: 'update',
        kind: 'input',
        label: t('Telegram update'),
        sub: t('webhook'),
        details: t(
          'Telegram delivers the player\'s message to the product\'s webhook (two-layer auth: routing token in the path + secret header). Duplicate deliveries of the same update are dropped, and one player\'s turns are processed strictly one at a time, in arrival order — two quick messages can never produce interleaved replies.'
        ),
        module: 'retention.py',
      },
      {
        id: 'antispam',
        kind: 'gate',
        label: t('Anti-spam gates'),
        note: t('junk never reaches the model'),
        details: t(
          'Per-player rate limit (the first blocked message gets a one-time in-persona "give me a second" notice), low-content filter (one-character mashing gets a canned nudge) and the injection scan (a jailbreak attempt gets an in-persona deflection). Overlong input is truncated, not rejected — chats are human.'
        ),
        knobs: [
          { label: t('Telegram rate limit / low-content / injection'), href: PARAMS },
        ],
        module: 'retention.py · antispam.py',
      },
      {
        id: 'entry',
        kind: 'gate',
        label: t('Deeplink entry + subscription gate'),
        note: t('no deeplink → refused; unsubscribed → subscribe prompt'),
        details: t(
          'Only players who entered via a one-time deeplink from the site are served (no organic entry — the bot always knows WHO it talks to). Every turn re-checks the channel subscription (positive checks are cached briefly); an unsubscribed player gets the subscribe prompt with a re-check button instead of a reply.'
        ),
        knobs: [
          { label: t('Subscription cache TTL'), href: PARAMS },
          { label: t('Channel id + URL (Telegram config)'), href: '#/retention-settings' },
        ],
        module: 'retention.py',
      },
      {
        id: 'profile',
        kind: 'input',
        label: t('Profile refresh (lazy pull)'),
        details: t(
          'If the profile snapshot is stale and the casino exposes a Player API, a fresh profile (balance, VIP, activity timestamps) is pulled before the turn — best-effort and SSRF-guarded, a failure never drops the message.'
        ),
        knobs: [{ label: t('Profile pull TTL'), href: PARAMS }],
        module: 'player_sync.py',
      },
      {
        id: 'candidates',
        kind: 'gate',
        label: t('Photo candidate selection'),
        note: t('empty set → text-only turn'),
        details: t(
          'The allowed photo set for THIS turn: unseen photos within the player\'s VIP tier × unlocked closeness stage (+1 teaser step), bounded by the daily photo cap and the proactive cooldown. The cooldown is bypassed when the player explicitly asks for a photo — and for the introduction photo a brand-new player receives in his first messages.'
        ),
        knobs: [
          { label: t('Daily photo cap / proactive cooldown / intro photo'), href: PARAMS },
          { label: t('Photo library (stages, VIP tiers)'), href: '#/retention?tab=photos' },
        ],
        module: 'retention.py',
      },
      {
        id: 'turn',
        kind: 'model',
        label: t('Nika\'s reply (model turn)'),
        details: t(
          'One model call assembles: the retention persona core, the product\'s retention KB, the player profile, appearance grounding (the persona\'s looks come from the real photo library), the REAL progression state, the current player-local time and — every N-th reply, with drift — the one permitted play invitation. The reply may carry control sentinels: photo, site-link button, hand-off, language, stage-up.'
        ),
        knobs: [
          { label: t('Retention KB (one document)'), href: '#/retention?tab=kb' },
          { label: t('Persona (prompt variables)'), href: '#/retention?tab=variables' },
          { label: t('Play-invitation cadence'), href: PARAMS },
          { label: t('Model / reasoning (System → Settings)'), href: '#/settings?module=core' },
        ],
        module: 'chat_service.py · prompts.py',
      },
      {
        id: 'sentinels',
        kind: 'action',
        label: t('Sentinel validation + routing'),
        note: t('the model proposes — the backend decides'),
        details: t(
          'Everything the model asked for is re-validated: a photo id must be in the offered candidate set, a link button must EXACTLY match a Site map page (an invented URL never becomes a button), [[HANDOFF]] replaces the reply with the manager / site-support choice card, [[LANG:xx]] drifts the sticky conversation language.'
        ),
        knobs: [
          { label: t('Site map (allowed link targets)'), href: '#/site-map' },
          { label: t('Managers (round-robin)'), href: '#/retention-settings?tab=managers' },
          { label: t('Hand-off texts (rtn_* keys)'), href: '#/translations' },
        ],
        module: 'chat_service.py · retention.py',
      },
      {
        id: 'send',
        kind: 'action',
        label: t('Delivery to the player'),
        details: t(
          'A typing indicator runs while the model thinks; a reply with blank lines goes out as a burst of short consecutive messages (human rhythm); photos send via the cached Telegram file_id (uploaded once, then free). Formatting is a light HTML subset with a plain-text fallback.'
        ),
        knobs: [{ label: t('Max messages per burst'), href: PARAMS }],
        module: 'retention.py · telegram_format.py',
      },
      {
        id: 'progress',
        kind: 'gate',
        label: t('Progression gate'),
        note: t('fully backend-decided'),
        details: t(
          'On every meaningful message the backend checks: enough engagement for the next stage, under the VIP-tier ceiling, enough hours since the last advance. The model has no say. A real advance is celebrated with a follow-up persona note, so the player knows he unlocked more daring photos.'
        ),
        knobs: [
          { label: t('Stage thresholds / tier ceilings / spacing / stage-up note'), href: PARAMS },
        ],
        module: 'retention.py',
      },
      {
        id: 'persist',
        kind: 'store',
        label: t('Transcript + session lifecycle'),
        details: t(
          'The turn persists atomically (both messages + the AI cost log + counters). A chat idle past the threshold closes lazily; the next message starts a FRESH session that carries a short continuity tail from the previous one — Nika greets back like someone who remembers.'
        ),
        knobs: [
          { label: t('Session idle timeout / continuity tail'), href: PARAMS },
          { label: t('Conversations (transcripts)'), href: '#/retention?tab=chats' },
        ],
        module: 'db.py · chat_service.py',
      },
    ],
  },
  {
    id: 'data',
    title: t('2 · Casino data in — what feeds the algorithm'),
    intro: t('Every piece of casino data enters through one seam (player_sync).'),
    steps: [
      {
        id: 'events',
        kind: 'input',
        label: t('Partner events'),
        sub: 'POST /partner/{id}/event',
        details: t(
          '22 canonical event names (deposits, bets, bonuses, KYC, levels…), idempotent by event id (a retried webhook never duplicates), batches up to 500. A future timestamp from a broken partner clock is clamped to now. Events land in the append-only event log.'
        ),
        module: 'player_sync.py',
      },
      {
        id: 'profilein',
        kind: 'input',
        label: t('Profile push / pull / handshake'),
        details: t(
          'Three ways the profile snapshot stays fresh: the casino CRM pushes partial updates, the bot lazily pulls from the Player API before a turn, and the deeplink handshake seeds the snapshot on entry. A product with none of these simply lives on the handshake snapshot — degrades, never breaks.'
        ),
        module: 'player_sync.py',
      },
      {
        id: 'bridge',
        kind: 'store',
        label: t('Activity bridge'),
        details: t(
          'Events bump the activity timestamps the state resolver and the idle ladder read: deposit → last deposit, session → last login, bet → last played. Forward-only — out-of-order delivery never rewinds a timestamp.'
        ),
        module: 'player_sync.py · db.py',
      },
      {
        id: 'losswin',
        kind: 'store',
        label: t('Event log + 24h loss window'),
        details: t(
          'The event log feeds the deterministic player state (active / at-risk / dormant, lifecycle stage) and the 24-hour net-loss window — summed per currency, worst bucket — that drives the comfort mode and the bet_settled trigger.'
        ),
        knobs: [{ label: t('High-loss threshold'), href: PARAMS }],
        module: 'retention_v2.py · db.py',
      },
    ],
  },
  {
    id: 'agent',
    title: t('3 · Proactive agent — an event becomes a touch'),
    intro: t('The one place the bot writes FIRST. Deterministic guards decide whether contact is allowed; the AI only picks among what they permit.'),
    steps: [
      {
        id: 'tick',
        kind: 'input',
        label: t('Worker tick'),
        details: t(
          'A background worker sweeps every product on a hot cadence under an advisory lock (several instances never double-process). The deploy switch RETENTION_SCHEDULER_ENABLED must be on; the agent switch and dry-run mode are per product. Dry-run ships ON: the agent decides and logs but sends nothing until you flip it.'
        ),
        knobs: [
          { label: t('Worker interval / agent on / dry-run'), href: PARAMS },
          { label: t('Agent status header'), href: AGENT },
        ],
        module: 'retention_v2.py',
      },
      {
        id: 'quiet',
        kind: 'gate',
        label: t('Quiet hours?'),
        note: t('night event → deferred till morning, not lost'),
        details: t(
          'During quiet hours the worker does not pick events up AT ALL — they stay queued and get their reaction in the morning (a night-time deposit still earns its thank-you). The admin «Process queue now» button processes regardless: you asked, it answers.'
        ),
        knobs: [{ label: t('Quiet hours start / end / UTC offset'), href: PARAMS }],
        module: 'retention_v2.py',
      },
      {
        id: 'claim',
        kind: 'action',
        label: t('Atomic claim + humanizing delay'),
        note: t('events older than 24h → state food only'),
        details: t(
          'Events are claimed atomically — the worker, the admin button and a second instance can run together and an event is still processed exactly once. Each event first waits a per-event random delay (an instant thank-you three seconds after a deposit reads as surveillance). Events whose occasion is older than 24 hours are demoted to state food — no retroactive congratulations.'
        ),
        knobs: [{ label: t('Send delay min / max'), href: PARAMS }],
        module: 'retention_v2.py · db.py',
      },
      {
        id: 'worthy',
        kind: 'gate',
        label: t('Decision-worthy?'),
        note: t('state food → marked processed silently'),
        details: t(
          'Only the decision events wake the agent (deposit confirmed/failed, withdrawal, level/class up, KYC approved, bonus completed/expired). Everything else only feeds the player state. bet_settled is special: it wakes the agent only when the 24h loss window crosses the high-loss threshold — and then the reaction is comfort, never celebration.'
        ),
        knobs: [{ label: t('High-loss threshold'), href: PARAMS }],
        module: 'retention_v2.py',
      },
      {
        id: 'guards',
        kind: 'gate',
        label: t('Guards — the hard rails'),
        note: t('every block is ledgered with its reason'),
        details: t(
          'Deterministic and never overridable by the AI: subscribed, not /stop-muted, bot not blocked, min gap since the last touch, daily touch cap, daily AI budget, same-event cooldown (one reaction per event type per window; for bet_settled even a "stay silent" verdict latches it, so a losing streak doesn\'t re-run paid decisions per bet), and the loss comfort window — after real losses the photo action is removed and a hard empathy constraint is injected.'
        ),
        knobs: [
          { label: t('Daily cap / min gap / budget / cooldown / comfort window'), href: PARAMS },
        ],
        module: 'retention_v2.py',
      },
      {
        id: 'decide',
        kind: 'model',
        label: t('Agent decision'),
        note: t('silence is first-class'),
        details: t(
          'One cheap strict-JSON model call sees the player state, the triggering event, recent events and the conversation tail — and picks among the PERMITTED actions only: silence (very often the right answer), message, or photo, plus tone and a one-line intent. Anything malformed degrades to silence. In dry-run the decision is logged and nothing is sent.'
        ),
        knobs: [{ label: t('Decisions ledger'), href: AGENT }],
        module: 'retention_v2.py · prompts.py',
      },
      {
        id: 'compose',
        kind: 'model',
        label: t('Persona writes the message'),
        details: t(
          'The normal persona stack writes the actual text: the occasion named in natural words (never a vague congratulation, never amounts in the model text), comfort wording after losses, the continuity tail of the previous chat, an optional photo from the gated candidates.'
        ),
        knobs: [
          { label: t('Header + occasion phrases (rtn_* keys)'), href: '#/translations' },
        ],
        module: 'chat_service.py · prompts.py',
      },
      {
        id: 'deliver',
        kind: 'action',
        label: t('Delivery channel'),
        details: t(
          'The message leaves through the shared delivery seam (one code path for the agent AND the idle ladder): persona header line, HTML with a plain fallback, a blocked bot flips the player to unreachable, optionally silent (no notification sound).'
        ),
        knobs: [{ label: t('Silent notifications'), href: PARAMS }],
        module: 'delivery.py',
      },
      {
        id: 'ledger',
        kind: 'store',
        label: t('Ledger + per-player counters'),
        details: t(
          'ONE ledger row per decision whatever the outcome — state snapshot, guard verdict with reasons, the agent\'s choice, cost, delivery — so "why did/didn\'t the bot write?" is always answerable. Sending also bumps the per-player counters the guards read next time.'
        ),
        knobs: [{ label: t('Decisions ledger'), href: AGENT }],
        module: 'db.py',
      },
    ],
  },
  {
    id: 'idle',
    title: t('4 · Idle re-engagement — silence becomes a ping'),
    intro: t('A quiet player produces no events, so the rules ladder writes first.'),
    steps: [
      {
        id: 'sweep',
        kind: 'input',
        label: t('Idle sweep'),
        details: t(
          'Runs from the same worker on its own pacing and its OWN switch — it keeps working even when the event agent is off. Quiet hours skip the sweep; dry-run logs what WOULD have gone out and sends nothing. The «Run now» button runs one bounded sweep immediately (under the same lock as the worker).'
        ),
        knobs: [
          { label: t('Idle pings on / sweep interval'), href: PARAMS },
          { label: t('Idle rules editor'), href: '#/retention-agent?tab=idle' },
        ],
        module: 'retention_idle.py',
      },
      {
        id: 'eligible',
        kind: 'gate',
        label: t('Player eligibility'),
        details: t(
          'Prefilter before any rule is looked at: subscribed, not /stop-muted, bot not blocked, past the min gap, under the daily touch cap — most-idle players first.'
        ),
        knobs: [{ label: t('Min gap / daily cap'), href: PARAMS }],
        module: 'db.py',
      },
      {
        id: 'ladder',
        kind: 'gate',
        label: t('Rule ladder (anti-cascade)'),
        note: t('one rung per silence stretch, not the whole ladder'),
        details: t(
          'The highest-priority matching rule fires: trigger kind (quiet in the bot / not playing / no deposit), N days of silence, VIP filter, per-rule cooldown. Anti-cascade: within ONE silence stretch only a rung ABOVE the last fired one may fire — a 60-days-quiet player gets one message, not the 45/30/21-day rungs cascading after it. The memory resets when the player writes again.'
        ),
        knobs: [{ label: t('Idle rules editor'), href: '#/retention-agent?tab=idle' }],
        module: 'retention_idle.py',
      },
      {
        id: 'compose2',
        kind: 'model',
        label: t('Generate + deliver'),
        details: t(
          'The same persona stack and the same delivery channel as an event touch: the rule\'s intent steers the message, a photo-action rule attaches a photo when the photo gates allow (otherwise it gracefully sends text only).'
        ),
        module: 'chat_service.py · delivery.py',
      },
      {
        id: 'ledger2',
        kind: 'store',
        label: t('Both ledgers'),
        details: t(
          'Every fired rule lands in the ping ledger (feeds the per-rule cooldown) AND the decisions ledger, and bumps the same per-player counters the guards read — caps and gaps hold across the agent and the ladder together.'
        ),
        knobs: [{ label: t('Idle send ledger'), href: '#/retention-agent?tab=idle' }],
        module: 'db.py',
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
const NodeBlock = ({ step, index, selected, dimmed, onClick }) => {
  const kind = KINDS[step.kind];
  return (
    <Box sx={{ opacity: dimmed ? 0.35 : 1, transition: 'opacity .15s' }}>
      <Box
        role="button"
        tabIndex={0}
        onClick={onClick}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onClick();
          }
        }}
        sx={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 1,
          p: 1.2,
          borderRadius: 1.5,
          cursor: 'pointer',
          border: '1px solid',
          borderColor: selected ? kind.color : 'divider',
          borderLeft: `4px solid ${kind.color}`,
          bgcolor: selected ? `${kind.color}1f` : `${kind.color}0d`,
          '&:hover': { bgcolor: `${kind.color}1f` },
        }}
      >
        <Typography
          variant="caption"
          sx={{ color: kind.color, fontWeight: 700, mt: '2px', minWidth: 18 }}
        >
          {index + 1}
        </Typography>
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.3 }}>
            {step.label}
            {step.sub && (
              <Typography
                component="span"
                variant="caption"
                sx={{ ml: 0.75, color: 'text.secondary', fontFamily: 'monospace' }}
              >
                {step.sub}
              </Typography>
            )}
          </Typography>
          {step.note && (
            <Typography variant="caption" color="text.secondary" sx={{ fontStyle: 'italic' }}>
              {step.note}
            </Typography>
          )}
        </Box>
      </Box>
      {selected && (
        <Box
          sx={{
            ml: 3,
            mt: 0.5,
            p: 1.5,
            borderRadius: 1.5,
            border: '1px dashed',
            borderColor: 'divider',
            bgcolor: 'background.default',
          }}
        >
          <Typography variant="body2" color="text.secondary">
            {rich(step.details)}
          </Typography>
          {step.knobs && step.knobs.length > 0 && (
            <Box sx={{ mt: 1 }}>
              <Typography variant="caption" sx={{ fontWeight: 700 }}>
                {t('Settings for this step:')}
              </Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.5 }}>
                {step.knobs.map((k) => (
                  <Chip
                    key={k.label}
                    size="small"
                    variant="outlined"
                    clickable
                    component={Link}
                    href={k.href}
                    underline="none"
                    label={k.label}
                  />
                ))}
              </Box>
            </Box>
          )}
          {step.module && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: 'block', mt: 1, fontFamily: 'monospace' }}
            >
              {step.module}
            </Typography>
          )}
        </Box>
      )}
    </Box>
  );
};

const Connector = ({ color }) => (
  <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', my: 0.25, ml: '13px', width: 4 }}>
    <Box sx={{ width: '2px', height: 10, bgcolor: 'divider' }} />
    <Typography variant="caption" sx={{ lineHeight: 0.6, color: color || 'text.disabled' }}>
      ▾
    </Typography>
  </Box>
);

const AlgorithmMapTab = () => {
  const [selected, setSelected] = useState(null); // "flowId:stepId"
  const [kindFilter, setKindFilter] = useState(null);

  return (
    <Box>
      <Card sx={{ mb: 1.5 }}>
        <CardContent sx={{ '&:last-child': { pb: 2 } }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {rich(
              t(
                'The whole retention algorithm as four flows. **Click any block** for a plain-language explanation, the settings that govern exactly that step, and the module implementing it. Click a legend chip to highlight all blocks of that kind.'
              )
            )}
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
            {Object.entries(KINDS).map(([key, k]) => (
              <Chip
                key={key}
                size="small"
                label={k.label}
                onClick={() => setKindFilter(kindFilter === key ? null : key)}
                variant={kindFilter === key ? 'filled' : 'outlined'}
                sx={{
                  borderColor: k.color,
                  color: kindFilter === key ? '#fff' : k.color,
                  bgcolor: kindFilter === key ? k.color : 'transparent',
                  '&:hover': { bgcolor: `${k.color}2a` },
                }}
              />
            ))}
          </Box>
        </CardContent>
      </Card>

      <Box
        sx={{
          display: 'grid',
          gap: 1.5,
          gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' },
          alignItems: 'start',
        }}
      >
        {FLOWS.map((flow) => (
          <Card key={flow.id}>
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                {flow.title}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                {flow.intro}
              </Typography>
              {flow.steps.map((step, i) => {
                const key = `${flow.id}:${step.id}`;
                return (
                  <Box key={step.id}>
                    {i > 0 && <Connector />}
                    <NodeBlock
                      step={step}
                      index={i}
                      selected={selected === key}
                      dimmed={kindFilter !== null && step.kind !== kindFilter}
                      onClick={() => setSelected(selected === key ? null : key)}
                    />
                  </Box>
                );
              })}
            </CardContent>
          </Card>
        ))}
      </Box>

      <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
        {rich(
          t(
            'Deeper operator material: the [Proactive agent guide](#/retention-agent) (testing checklist, guard-reason table with your current values, cost model) and the numeric knobs on [Retention → Settings → Parameters](#/retention-settings?tab=params).'
          )
        )}
      </Typography>
    </Box>
  );
};

export default AlgorithmMapTab;
