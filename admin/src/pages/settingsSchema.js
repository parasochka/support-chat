/**
 * Field metadata for the Settings groups so the editor can render typed,
 * labelled inputs with explanations instead of a raw JSON blob. Mirrors the
 * validation in settings.py (types + bounds). The `language` group is special
 * (its own editor) and `escalation` is edited on the Prompt page, so neither
 * appears here.
 *
 * Field shapes:
 *   { name, label, type, help, min?, max?, step?, options?, module?, globalOnly? }
 *   type ∈ int | float | bool | string | select | intlist | strlist | intmap
 *
 * `globalOnly` marks a field read by deploy-wide machinery outside any product
 * scope (the worker loop, middleware, admin tokens): it can only be edited at
 * the All-products scope — with a product selected the input is locked and the
 * backend strips the field from product-layer saves (settings.GLOBAL_ONLY_FIELDS).
 *
 * `module` routes a field to one of the three settings surfaces (the sidebar
 * has one per module): 'support' (Support chat → Chat settings), 'retention'
 * (Telegram · Retention → Bot settings) and 'core' (System → Settings). A
 * field without a tag inherits its group's default module. A group is still
 * SAVED as a whole object — the module split is presentation only.
 */
export const GROUP_LABELS = {
  antispam: 'Anti-spam',
  model: 'AI model',
  general: 'General',
  retention: 'Retention bot',
  language: 'Languages',
};

export const GROUP_HELP = {
  antispam:
    'Rate limiting, cooldowns and the injection / low-content guards that run before the model.',
  model:
    'OpenAI request tuning. Edits are hot — the next turn uses them (the client is rebuilt on save).',
  general:
    'Operational limits with no other home: session/token lifetimes, the message cap, prompt-history window and the request body cap.',
  retention:
    'Telegram retention-bot pacing: photo caps, cooldowns, the photo-unlock progression and profile freshness.',
  language:
    'Which languages the assistant supports and the default. Answers follow the player; the widget chrome follows the browser.',
};

// Default module per group; individual fields may override with `module`.
export const GROUP_MODULE = {
  antispam: 'support',
  model: 'core',
  general: 'core',
  retention: 'retention',
  language: 'core',
};

// The three settings surfaces (each a sidebar entry inside its section).
export const MODULES = {
  support: {
    title: 'Support chat settings',
    help: 'Anti-spam and chat limits for the support widget. These knobs only affect the on-site support chat.',
    groups: ['antispam', 'general'],
  },
  retention: {
    title: 'Retention bot settings',
    help: 'Everything that paces the Telegram retention bot: photos, progression, the proactive agent and its per-player guards.',
    groups: ['retention', 'antispam'],
  },
  core: {
    title: 'System settings (core)',
    help: 'Deploy-wide core: the AI model, supported languages and technical limits shared by both bots.',
    groups: ['model', 'general', 'language'],
  },
};

export const GROUP_FIELDS = {
  antispam: [
    { name: 'rate_limit_max_per_ip', label: 'Rate limit (max / IP)', type: 'int', min: 1, max: 100000, help: 'Maximum requests from one IP within the window (widget/API).' },
    { name: 'tg_rate_limit_max_per_user', label: 'Telegram rate limit (max / user)', type: 'int', min: 1, max: 100000, module: 'retention', help: 'Maximum Telegram messages from one player within the same window — a live chat needs more headroom than the widget.' },
    { name: 'window_sec', label: 'Rate-limit window (sec)', type: 'int', min: 1, max: 86400, help: 'Length of the rate-limit window in seconds.' },
    { name: 'cooldown_sec', label: 'Message cooldown (sec)', type: 'int', min: 0, max: 3600, help: 'Minimum seconds between two messages in one session.' },
    { name: 'max_input_chars', label: 'Max input characters', type: 'int', min: 1, max: 100000, help: 'Longest single message the API accepts.' },
    { name: 'injection_hard_block', label: 'Hard-block injection attempts', type: 'bool', help: 'Reject prompt-injection with HTTP 400 (off = audit only, still answered).' },
    { name: 'low_content_block', label: 'Block low-content messages', type: 'bool', help: 'Nudge instead of calling the model on empty/one-character spam.' },
    { name: 'min_meaningful_chars', label: 'Min meaningful characters', type: 'int', min: 1, max: 100, help: 'Distinct letters/digits a message must carry to reach the model.' },
  ],
  model: [
    { name: 'model', label: 'Model id', type: 'string', help: 'OpenAI model, e.g. gpt-5.6-luna (the cheapest tier of the GPT-5.6 reasoning family).' },
    { name: 'reasoning_effort', label: 'Reasoning effort', type: 'select', options: ['', 'minimal', 'low', 'medium', 'high'], help: 'Hidden-reasoning depth. Empty = the model default (parameter omitted).' },
    { name: 'verbosity', label: 'Verbosity', type: 'select', options: ['', 'low', 'medium', 'high'], help: 'Answer length. Empty = the model default (parameter omitted).' },
    { name: 'max_output_tokens', label: 'Max output tokens', type: 'int', min: 1, max: 128000, help: 'Output budget — INCLUDES hidden reasoning tokens, so keep it generous (≈2000).' },
    { name: 'request_timeout_sec', label: 'Chat: request timeout (sec)', type: 'int', min: 1, max: 600, help: 'Per-request timeout for a LIVE chat turn (support widget + Telegram replies) before a retry/failover.' },
    { name: 'key_switch_timeout_sec', label: 'Chat: key-switch timeout (sec)', type: 'int', min: 0, max: 600, help: 'Silence on the primary key before the fallback key is raced, for live chat turns. 0 = never race (fail over only on a real error).' },
    // Background profiles: the same two knobs per block that talks to the model.
    // A background pass has nobody waiting, so racing the second key there only
    // pays for the same work twice — they ship with the race OFF (0).
    { name: 'agent_request_timeout_sec', label: 'Agent: request timeout (sec)', type: 'int', min: 1, max: 600, help: 'Proactive retention agent (event decisions + ping writing) — nobody is waiting, so it may take longer than a chat turn.' },
    { name: 'agent_key_switch_timeout_sec', label: 'Agent: key-switch timeout (sec)', type: 'int', min: 0, max: 600, help: 'Race the fallback key for proactive-agent calls after this much silence. 0 (default) = never race — a background retry would just bill the same work twice.' },
    { name: 'review_request_timeout_sec', label: 'Quality review: request timeout (sec)', type: 'int', min: 1, max: 600, help: 'The AI judge reads a WHOLE conversation per call, so it needs a longer timeout than a chat turn.' },
    { name: 'review_key_switch_timeout_sec', label: 'Quality review: key-switch timeout (sec)', type: 'int', min: 0, max: 600, help: 'Race the fallback key for quality-review calls after this much silence. 0 (default) = never race.' },
    { name: 'media_request_timeout_sec', label: 'Media cataloguing: request timeout (sec)', type: 'int', min: 1, max: 600, help: 'Vision pass over an uploaded photo/video (multi-MB image) — the slowest call the stack makes.' },
    { name: 'media_key_switch_timeout_sec', label: 'Media cataloguing: key-switch timeout (sec)', type: 'int', min: 0, max: 600, help: 'Race the fallback key for media-cataloguing calls after this much silence. 0 (default) = never race.' },
    { name: 'max_attempts', label: 'Max attempts / key', type: 'int', min: 1, max: 10, help: 'Retries per key on transient (429/timeout) errors.' },
    { name: 'max_concurrent_per_key', label: 'Max concurrent / key', type: 'int', min: 1, max: 1000, help: 'Concurrent in-flight requests allowed per API key.' },
  ],
  general: [
    { name: 'session_ttl_hours', label: 'Session TTL (hours)', type: 'int', min: 1, max: 8760, module: 'support', help: 'How long a chat session stays valid.' },
    { name: 'admin_token_ttl_min', label: 'Admin token TTL (min)', type: 'int', min: 5, max: 10080, globalOnly: true, help: 'Admin inactivity window (5 min … 1 week). The session slides: daily use auto-renews it; an account untouched for this long is logged out. Default 1 week (10080).' },
    { name: 'max_messages_per_session', label: 'Max messages / session', type: 'int', min: 1, max: 10000, module: 'support', help: 'Soft message cap: past it every reply carries the escalate/finish notice instead of suggestion bubbles; the session force-escalates at twice this value.' },
    { name: 'history_max_turns', label: 'History turns to model', type: 'int', min: 1, max: 200, module: 'support', help: 'Recent turns fed into the prompt history (full transcript is always stored).' },
    { name: 'body_max_bytes', label: 'Max request body (bytes)', type: 'int', min: 1024, max: 104857600, globalOnly: true, help: 'Largest accepted request body (1 KiB … 100 MiB).' },
    { name: 'quality_review_enabled', label: 'Quality review (AI judge)', type: 'bool', help: 'Score finished conversations (support + Telegram) with a cheap AI pass. Results live on the Quality page.' },
    { name: 'quality_review_daily_max', label: 'Quality reviews / day', type: 'int', min: 0, max: 5000, help: 'Cost cap: how many conversations the judge may review per day for this product (0 = pause).' },
    { name: 'quality_review_min_messages', label: 'Min messages to review', type: 'int', min: 2, max: 1000, help: 'Shorter conversations are skipped — there is nothing to judge in a one-liner.' },
  ],
  // NB: `v2_decision_events` is deliberately ABSENT here — the trigger set is
  // not meant to be edited from the panel (the agent's Triggers tab was
  // removed); the built-in defaults apply, and an API consumer can still PUT
  // the /admin/settings/retention group.
  retention: [
    { name: 'daily_photo_cap', label: 'Daily photo cap', type: 'int', min: 0, max: 10000, help: 'Max photos sent to one player per day (hard, incl. requested).' },
    { name: 'proactive_photo_cooldown_msgs', label: 'Proactive photo cooldown (msgs)', type: 'int', min: 1, max: 10000, help: 'Messages between UNPROMPTED photos (a direct ask bypasses it).' },
    { name: 'intro_photo_enabled', label: 'Introduction photo', type: 'bool', help: 'A brand-new player (never received a photo) gets one proactively in his first messages, with a "this is me — let\'s get to know each other" caption, so he learns early that chatting comes with photos.' },
    { name: 'intro_photo_within_msgs', label: 'Introduction photo window (msgs)', type: 'int', min: 1, max: 100, help: 'How many of the player\'s first meaningful messages count as the acquaintance window for the introduction photo.' },
    { name: 'candidate_list_size', label: 'Media candidate list size', type: 'int', min: 1, max: 50, help: 'How many media candidates (photos + videos) the model is offered to choose from. Videos take 2 of the slots (never fewer than 2 while the list has room: 6 → 4 photos + 2 videos, 4 → 2+2, 3 → 2 photos + 1 video).' },
    { name: 'stage_advance_min_hours', label: 'Stage advance min hours', type: 'int', min: 0, max: 8760, help: 'Minimum spacing between explicitness-stage advances.' },
    { name: 'nonce_ttl_sec', label: 'Deeplink nonce TTL (sec)', type: 'int', min: 10, max: 3600, help: 'Lifetime of a one-time deeplink nonce.' },
    { name: 'profile_pull_ttl_sec', label: 'Profile pull TTL (sec)', type: 'int', min: 0, max: 604800, help: 'How long a pulled player profile stays fresh before a re-pull.' },
    { name: 'session_idle_minutes', label: 'Session idle (min)', type: 'int', min: 0, max: 525600, help: 'Idle minutes before a Telegram chat closes; the next message starts a fresh chat (0 = never close).' },
    { name: 'carry_context_turns', label: 'Carry-over context turns', type: 'int', min: 0, max: 50, help: 'Trailing turns of the previous chat shown to the model when a returning player starts a fresh one (0 = off).' },
    { name: 'play_reminder_every_msgs', label: 'Play reminder every ~N replies', type: 'int', min: 0, max: 1000, help: 'Roughly every N-th of Nika’s Telegram replies weaves in a light in-context invitation to play, with a one-tap site button picked from the Site map (0 = off). The actual cadence drifts ±2 around N (…after 3, then 7, then 5…) so the pattern can’t be clocked.' },
    { name: 'max_reply_parts', label: 'Max messages per reply (burst)', type: 'int', min: 1, max: 5, help: 'A reply with blank lines is delivered as a burst of separate Telegram messages (with a typing pause between them). This caps the burst; longer replies collapse into the last message. 1 = always one message.' },
    // NB: media-normalizer knobs are deliberately ABSENT — normalization is
    // always-on and fully code-owned (config.RETENTION_MEDIA_*), with no admin
    // surface and no on/off switch.
    { name: 'silent_notifications', label: 'Silent notifications (proactive)', type: 'bool', section: 'delivery', help: 'Proactive messages arrive WITHOUT a sound/vibration on the player’s phone (Telegram silent delivery). Replies in a live dialogue always notify normally.' },
    { name: 'subscription_cache_ttl_sec', label: 'Subscription re-check cache (sec)', type: 'int', min: 0, max: 86400, section: 'delivery', help: 'How long a positive channel-subscription check is cached before asking Telegram again (0 = re-check on every message).' },
    { name: 'v2_enabled', label: 'Agent enabled', type: 'bool', section: 'agent', help: 'The proactive agent for this product: reacts to casino events (deposits, level-ups, losses) with a decision per event. Off = no proactive messages at all (the dialogue bot still answers).' },
    { name: 'v2_dry_run', label: 'Dry-run (shadow mode)', type: 'bool', section: 'agent', help: 'ON: the agent decides and logs to the Decisions ledger but sends nothing. Turn off only after reviewing decisions.' },
    { name: 'worker_interval_sec', label: 'Worker interval (seconds)', type: 'int', min: 5, max: 3600, section: 'agent', globalOnly: true, help: 'How often the background worker drains the event queue — ONE loop serves every product, so this is a deploy-wide (global) setting. Applies live on the next tick (no redeploy).' },
    { name: 'ping_batch_size', label: 'Events per sweep', type: 'int', min: 1, max: 500, section: 'agent', help: 'Max events one worker sweep processes per product — bounds the burst on Telegram and OpenAI.' },
    { name: 'v2_daily_budget_usd', label: 'Daily AI budget (USD)', type: 'float', min: 0, max: 10000, section: 'agent', help: 'Hard stop: once the day’s decisions cost this much, the agent goes quiet until tomorrow. 0 = no budget.' },
    { name: 'idle_pings_enabled', label: 'Idle re-engagement pings', type: 'bool', section: 'agent', help: 'The agent’s inactivity trigger: the Idle pings rules ladder («quiet N days → Nika writes first», Retention → Idle pings tab). Off = the agent reacts to casino events only; a quiet player is never written to.' },
    { name: 'idle_sweep_interval_sec', label: 'Idle rules sweep interval (sec)', type: 'int', min: 60, max: 86400, section: 'agent', help: 'How often the idle-rules ladder is re-evaluated per product. The rules move on a scale of days, so the default (600 = 10 min) is plenty; «Run now» on the Idle pings tab bypasses it.' },
    { name: 'v2_send_delay_min_sec', label: 'Send delay, min (seconds)', type: 'int', min: 0, max: 21600, section: 'agent', help: 'A proactive reaction goes out a RANDOM delay after the event, never instantly — an instant thank-you after a deposit reads as surveillance. Default 300 (5 min); each event gets its own delay between min and max. «Process queue now» bypasses the delay.' },
    { name: 'v2_send_delay_max_sec', label: 'Send delay, max (seconds)', type: 'int', min: 0, max: 21600, section: 'agent', help: 'Upper bound of the random per-event send delay. Default 900 (15 min) — so reactions land 5–15 minutes after the event, ~10 on average. Set min = max for an exact delay; both 0 = react immediately.' },
    { name: 'ping_daily_cap', label: 'Max proactive messages per player per day', type: 'int', min: 1, max: 24, section: 'guards', help: 'Hard per-player cap: the agent never sends more than this many proactive messages to one player in a day, however many events fire.' },
    { name: 'ping_min_gap_hours', label: 'Min gap between messages (hours)', type: 'int', min: 0, max: 720, section: 'guards', help: 'Minimum hours between two proactive messages to the same player (0 = off). Keep it short (1–2h) if you want the agent to react to several events per day.' },
    { name: 'v2_same_event_cooldown_hours', label: 'Same-event cooldown (hours)', type: 'int', min: 0, max: 720, section: 'guards', help: 'One reaction per event TYPE per player per window — five deposits in an evening get one warm note, not five. Set 0 to disable while testing, so a re-injected simulator event gets a fresh decision instead of a same_event_cooldown block.' },
    { name: 'quiet_hours_start', label: 'Quiet hours start (0–23)', type: 'int', min: 0, max: 23, section: 'guards', help: 'Hour when the no-contact window begins (players are not messaged at night).' },
    { name: 'quiet_hours_end', label: 'Quiet hours end (0–23)', type: 'int', min: 0, max: 23, section: 'guards', help: 'Hour when the no-contact window ends and proactive messages may resume.' },
    { name: 'quiet_hours_utc_offset', label: 'Quiet hours UTC offset', type: 'int', min: -12, max: 14, section: 'guards', help: 'Timezone offset the quiet hours (and the prompt’s current-time block) are evaluated in (e.g. 3 = UTC+3).' },
    { name: 'v2_loss_comfort_hours', label: 'Loss comfort window (hours)', type: 'int', min: 0, max: 720, section: 'guards', help: 'After a big-loss signal: no play invitations, no reward photos, empathetic tone only, for this many hours.' },
    { name: 'v2_loss_high_usd', label: 'High-loss threshold (USD / 24h)', type: 'float', min: 0, max: 1000000, section: 'guards', help: 'Net loss over 24 hours that marks the player critical and starts the comfort window.' },
    { name: 'max_stage', label: 'Max stage (top explicitness)', type: 'int', min: 1, max: 20, section: 'progression', help: 'The hottest stage that exists. Photos and tier ceilings can never go above it — there is nothing beyond this number.' },
    { name: 'stage_advance_msgs', label: 'Messages to reach each stage', type: 'stagethresholds', section: 'progression', help: 'How many meaningful messages a player must send to unlock each stage. Stage 1 is free; the more they chat, the hotter the stage they reach (still capped by their VIP tier below).' },
    { name: 'vip_tiers', label: 'VIP tiers (lowest → highest)', type: 'strlist', section: 'progression', help: 'The VIP ladder, one tier per line, from lowest to highest. Order matters: a tier’s position is its Level number a photo can require.' },
    { name: 'max_stage_by_tier', label: 'Stage ceiling per VIP tier (Level → highest Stage)', type: 'intmap', section: 'progression', orderByField: 'vip_tiers', min: 1, help: 'The highest stage each VIP tier is allowed to reach, no matter how much they chat. Higher VIP = hotter photos unlocked.' },
    { name: 'stage_up_notify', label: 'Level-up congratulation message', type: 'bool', section: 'progression', help: 'When a player actually unlocks the next photo stage, Nika follows up with a short celebratory note: you two got closer, more daring photos from now on, keep chatting to unlock even more. Persisted with its trigger, so she can later explain what the message was about.' },
    // --- Retention orchestrator (measurement / RG / frequency / scoring /
    // offers / journeys / channels). The rich editors live on the
    // Retention → Orchestrator page; these are the on/off + numeric knobs.
    { name: 'holdout_pct', label: 'Holdout control group (%)', type: 'int', min: 0, max: 50, section: 'orchestrator', help: 'Share of players deterministically held out of ALL proactive touches to measure real uplift (business decision: 15% by default). 0 = off. Change the salt on the Orchestrator → Measurement tab to start a new experiment.' },
    { name: 'rg_enabled', label: 'RG guard (responsible gaming)', type: 'bool', section: 'orchestrator', help: 'ON by default — protection, not a feature. Self-excluded players (casino-reported or manually marked) never get proactive touches or offers.' },
    { name: 'rg_require_consent', label: 'Require marketing consent', type: 'bool', section: 'orchestrator', help: 'When ON, players without marketing_consent=true get no game touches (Tier-1 markets). OFF at MVP.' },
    { name: 'rg_dialogue_suppression', label: 'RG: strip game CTAs in dialogue', type: 'bool', section: 'orchestrator', help: 'A restricted player who writes to the bot still gets warm answers, but play/deposit/bonus invitations are stripped from replies.' },
    { name: 'adaptive_frequency_enabled', label: 'Adaptive frequency caps', type: 'bool', section: 'orchestrator', help: 'Priority/cohort-aware cap matrix (P1/P2 touches are never cut; VIP rows may relax; email rides its own budget). OFF = the static guards above apply unchanged.' },
    { name: 'smart_send_time_enabled', label: 'Smart send time', type: 'bool', section: 'orchestrator', help: 'Shift non-urgent touches into the hours the player is actually active (their timezone from the casino, else the product offset). Loss reactions keep the short delay.' },
    { name: 'scoring_enabled', label: 'Player scoring (cohorts / RFM / value)', type: 'bool', section: 'orchestrator', help: 'Dormancy cohorts (d7/d10/d14/d21/d30), RFM and value tiers, recomputed in the background. Recovery journeys and VIP handling read them.' },
    { name: 'offers_enabled', label: 'Offer engine', type: 'bool', section: 'orchestrator', help: 'Master switch for granting real bonuses (by their bonus-CMS IDs) via the casino endpoint. Also needs dry-run OFF and a non-zero budget below.' },
    { name: 'offer_dry_run', label: 'Offers: dry-run', type: 'bool', section: 'orchestrator', help: 'ON: the engine decides and logs grants without calling the casino. Turn off only after the Bonus CMS contract is wired.' },
    { name: 'offer_daily_budget_usd', label: 'Offers: daily budget (USD)', type: 'float', min: 0, max: 1000000, section: 'orchestrator', help: 'Hard daily stop for granted stimulus value (cost estimates from the catalog). 0 = granting blocked (the safe default).' },
    { name: 'offer_cooldown_hours', label: 'Offers: per-player cooldown (hours)', type: 'int', min: 0, max: 8760, section: 'orchestrator', help: 'Minimum hours between two offers to the same player (separate from message cooldowns).' },
    { name: 'journeys_enabled', label: 'Journey engine', type: 'bool', section: 'orchestrator', help: 'Multi-step trajectories (recovery by cohort, FTD spine, weekly rhythm, cashier abandonment). Journeys still seed as draft + dry-run and are activated one by one.' },
    { name: 'multichannel_enabled', label: 'Multi-channel delivery', type: 'bool', section: 'orchestrator', help: 'Enable the channel router beyond Telegram (email via Customer.io, delegated push/in-app via the casino). Strict opt-in per channel. OFF = Telegram only, as before.' },
    // --- Event pipeline: the leased queue, the parallel drain, the send
    // stage. Most of it is read by the worker OUTSIDE any product scope, hence
    // globalOnly; the ingest shaping, the backpressure ladder and the outbound
    // rate limits are per product (one casino's traffic must not throttle
    // another's). Queue health is watched on the agent page, not here.
    { name: 'event_lease_sec', label: 'Event lease (sec)', type: 'int', min: 60, max: 3600, section: 'pipeline', globalOnly: true, help: 'How long a claimed event may stay in flight before another worker assumes the first one died and takes it over. It must outlast the slowest step of a decision (the agent model call, 90s) with margin — too short and the same deposit is reacted to twice.' },
    { name: 'event_max_attempts', label: 'Max attempts per event', type: 'int', min: 1, max: 50, section: 'pipeline', globalOnly: true, help: 'After this many failed passes an event is dead-lettered instead of retried forever. Too low and a brief provider outage buries an hour of reactions in the dead-letter list; too high and one poison event keeps cycling through the queue.' },
    { name: 'event_backoff_base_sec', label: 'Retry backoff base (sec)', type: 'int', min: 1, max: 3600, section: 'pipeline', globalOnly: true, help: 'Delay before the first retry of a failed event; it doubles with every further attempt. Small values hammer whatever is already failing, large ones push the retry past the moment the reaction was still worth sending.' },
    { name: 'worker_product_concurrency', label: 'Products drained in parallel', type: 'int', min: 1, max: 32, section: 'pipeline', globalOnly: true, help: 'How many products one worker drains at the same time. At 1 the tick takes the SUM over every product, so one busy casino delays reactions for all the others.' },
    { name: 'worker_player_concurrency', label: 'Players drained in parallel', type: 'int', min: 1, max: 64, section: 'pipeline', globalOnly: true, help: 'How many DIFFERENT players inside one product are handled at once. One player’s own events always stay strictly serial — two decisions in parallel for the same player race the send guards and he gets two messages.' },
    { name: 'agent_model_concurrency', label: 'Max concurrent agent model calls', type: 'int', min: 1, max: 256, section: 'pipeline', globalOnly: true, help: 'Fleet-wide ceiling on background model calls. Without it an event burst opens hundreds of completions at once, and live chat turns queue behind the agent’s thinking.' },
    { name: 'queue_bypass_state_events', label: 'Keep state food out of the queue', type: 'bool', section: 'pipeline', help: 'High-volume events (spins, session pings) are stored complete and never enter the drain. Off = queue depth and lag track raw casino traffic instead of decision work, so the backpressure ladder starts shedding lanes for no reason.' },
    { name: 'activity_debounce_sec', label: 'Activity timestamp debounce (sec)', type: 'int', min: 0, max: 86400, section: 'pipeline', help: '“Player was just active” is the busiest write in the system and re-stamping it seconds later buys nothing. 0 = write on every event, which is what makes that row the hottest lock in the database.' },
    { name: 'queue_degrade_p3_sec', label: 'Backpressure: shed normal lane after (sec)', type: 'int', min: 30, max: 86400, section: 'pipeline', help: 'First rung of the ladder: once the queue lag passes this, ordinary (P3) events stop being claimed so deposits and payouts keep moving. Set too high, a backlog delays exactly the reactions players notice.' },
    { name: 'queue_degrade_p2_sec', label: 'Backpressure: transactional only after (sec)', type: 'int', min: 30, max: 86400, section: 'pipeline', help: 'Second rung: past this lag only money/status (P1–P2) events are claimed. It must be ≥ the rung above — the save is rejected otherwise, because an out-of-order ladder sheds everything in one step.' },
    { name: 'queue_degrade_idle_sec', label: 'Backpressure: pause idle ladder after (sec)', type: 'int', min: 60, max: 604800, section: 'pipeline', help: 'Last rung: with the lag this far gone the idle re-engagement sweep pauses for this product. Re-engagement competes with live reactions for the same worker, and a quiet player can wait an hour — a deposit cannot.' },
    { name: 'send_worker_enabled', label: 'Separate send worker', type: 'bool', section: 'pipeline', globalOnly: true, help: 'ON: a decision only enqueues a delivery and a separate worker sends it under the rate limits below, so a large broadcast drains instead of collecting Telegram 429s. OFF (default): decisions send inline on the decision loop, exactly as before.' },
    { name: 'send_concurrency', label: 'Deliveries in flight', type: 'int', min: 1, max: 64, section: 'pipeline', help: 'How many queued deliveries the send worker pushes at once. The token buckets below still shape the real rate; this only bounds how many sends may sit waiting on Telegram at the same time.' },
    { name: 'send_batch_size', label: 'Deliveries claimed per pass', type: 'int', min: 1, max: 1000, section: 'pipeline', help: 'How many delivery rows one send pass leases. A big batch holds its lease longer, so a worker killed mid-batch leaves that many messages waiting for the lease to expire before anyone retries them.' },
    { name: 'telegram_rate_per_sec', label: 'Telegram: messages / sec (bot)', type: 'float', min: 0.1, max: 30, step: 0.5, section: 'pipeline', help: 'Sustained send rate for the whole bot. Telegram answers 429 above roughly 30/s, and a broadcast that trips it turns into a retry storm that delivers slower than the limit would have.' },
    { name: 'telegram_burst', label: 'Telegram: burst (messages)', type: 'float', min: 1, max: 100, step: 1, section: 'pipeline', help: 'How many messages may leave back-to-back before the sustained rate applies. A burst wider than Telegram tolerates is precisely what produces the 429s the bucket exists to prevent.' },
    { name: 'telegram_chat_rate_per_sec', label: 'Telegram: messages / sec (one chat)', type: 'float', min: 0.05, max: 30, step: 0.1, section: 'pipeline', help: 'Per-player ceiling (~1/s at Telegram). Exceeded, that one player’s messages are rejected even though the bot-wide budget is fine — this is the limit a multi-part reply burst runs into.' },
    { name: 'email_rate_per_sec', label: 'Email: sends / sec', type: 'float', min: 0.1, max: 1000, step: 1, section: 'pipeline', help: 'Per-product ceiling for the email channel (only used with multi-channel delivery on). Set above what Customer.io allows and the provider throttles the whole account, not just this campaign.' },
    { name: 'event_keep_days', label: 'Keep events (days)', type: 'int', min: 1, max: 3650, section: 'pipeline', globalOnly: true, help: 'Age at which decision-worthy events are pruned. The log is append-only and grows by millions of rows a month; with no reaper the table and every query over it degrade until the drain itself slows down.' },
    { name: 'event_keep_days_state', label: 'Keep state-food events (days)', type: 'int', min: 1, max: 3650, section: 'pipeline', globalOnly: true, help: 'Shorter retention for the high-volume lane (spins, session pings): 90%+ of the rows, and worthless once the state resolver’s loss and activity windows have passed.' },
  ],
};

/** Fields of `group` that belong to `module` (group default + per-field tags). */
export const fieldsForModule = (group, module) =>
  (GROUP_FIELDS[group] || []).filter(
    (f) => (f.module || GROUP_MODULE[group]) === module
  );
