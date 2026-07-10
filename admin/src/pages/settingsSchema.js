/**
 * Field metadata for the Settings groups so the editor can render typed,
 * labelled inputs with explanations instead of a raw JSON blob. Mirrors the
 * validation in settings.py (types + bounds). The `language` group is special
 * (its own editor) and `escalation` is edited on the Prompt page, so neither
 * appears here.
 *
 * Field shapes:
 *   { name, label, type, help, min?, max?, step?, options? }
 *   type ∈ int | float | bool | string | select | intlist | strlist | intmap
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

export const GROUP_FIELDS = {
  antispam: [
    { name: 'rate_limit_max_per_ip', label: 'Rate limit (max / IP)', type: 'int', min: 1, max: 100000, help: 'Maximum requests from one IP within the window (widget/API).' },
    { name: 'tg_rate_limit_max_per_user', label: 'Telegram rate limit (max / user)', type: 'int', min: 1, max: 100000, help: 'Maximum Telegram messages from one player within the same window — a live chat needs more headroom than the widget.' },
    { name: 'window_sec', label: 'Rate-limit window (sec)', type: 'int', min: 1, max: 86400, help: 'Length of the rate-limit window in seconds.' },
    { name: 'cooldown_sec', label: 'Message cooldown (sec)', type: 'int', min: 0, max: 3600, help: 'Minimum seconds between two messages in one session.' },
    { name: 'max_input_chars', label: 'Max input characters', type: 'int', min: 1, max: 100000, help: 'Longest single message the API accepts.' },
    { name: 'injection_hard_block', label: 'Hard-block injection attempts', type: 'bool', help: 'Reject prompt-injection with HTTP 400 (off = audit only, still answered).' },
    { name: 'low_content_block', label: 'Block low-content messages', type: 'bool', help: 'Nudge instead of calling the model on empty/one-character spam.' },
    { name: 'min_meaningful_chars', label: 'Min meaningful characters', type: 'int', min: 1, max: 100, help: 'Distinct letters/digits a message must carry to reach the model.' },
  ],
  model: [
    { name: 'model', label: 'Model id', type: 'string', help: 'OpenAI model, e.g. gpt-5-mini (the GPT-5 mini reasoning family).' },
    { name: 'reasoning_effort', label: 'Reasoning effort', type: 'select', options: ['', 'minimal', 'low', 'medium', 'high'], help: 'Hidden-reasoning depth. Empty = the model default (parameter omitted).' },
    { name: 'verbosity', label: 'Verbosity', type: 'select', options: ['', 'low', 'medium', 'high'], help: 'Answer length. Empty = the model default (parameter omitted).' },
    { name: 'max_output_tokens', label: 'Max output tokens', type: 'int', min: 1, max: 128000, help: 'Output budget — INCLUDES hidden reasoning tokens, so keep it generous (≈2000).' },
    { name: 'request_timeout_sec', label: 'Request timeout (sec)', type: 'int', min: 1, max: 600, help: 'Per-request timeout before a retry/failover.' },
    { name: 'key_switch_timeout_sec', label: 'Key-switch timeout (sec)', type: 'int', min: 1, max: 600, help: 'Silence on the primary key before the fallback key is raced.' },
    { name: 'max_attempts', label: 'Max attempts / key', type: 'int', min: 1, max: 10, help: 'Retries per key on transient (429/timeout) errors.' },
    { name: 'max_concurrent_per_key', label: 'Max concurrent / key', type: 'int', min: 1, max: 1000, help: 'Concurrent in-flight requests allowed per API key.' },
  ],
  general: [
    { name: 'session_ttl_hours', label: 'Session TTL (hours)', type: 'int', min: 1, max: 8760, help: 'How long a chat session stays valid.' },
    { name: 'admin_token_ttl_min', label: 'Admin token TTL (min)', type: 'int', min: 5, max: 10080, help: 'Admin inactivity window (5 min … 1 week). The session slides: daily use auto-renews it; an account untouched for this long is logged out. Default 1 week (10080).' },
    { name: 'max_messages_per_session', label: 'Max messages / session', type: 'int', min: 1, max: 10000, help: 'Message cap before the session hands off to a human.' },
    { name: 'history_max_turns', label: 'History turns to model', type: 'int', min: 1, max: 200, help: 'Recent turns fed into the prompt history (full transcript is always stored).' },
    { name: 'body_max_bytes', label: 'Max request body (bytes)', type: 'int', min: 1024, max: 104857600, help: 'Largest accepted request body (1 KiB … 100 MiB).' },
  ],
  retention: [
    { name: 'daily_photo_cap', label: 'Daily photo cap', type: 'int', min: 0, max: 10000, help: 'Max photos sent to one player per day (hard, incl. requested).' },
    { name: 'proactive_photo_cooldown_msgs', label: 'Proactive photo cooldown (msgs)', type: 'int', min: 1, max: 10000, help: 'Messages between UNPROMPTED photos (a direct ask bypasses it).' },
    { name: 'candidate_list_size', label: 'Photo candidate list size', type: 'int', min: 1, max: 50, help: 'How many photo candidates the model is offered to choose from.' },
    { name: 'stage_advance_min_hours', label: 'Stage advance min hours', type: 'int', min: 0, max: 8760, help: 'Minimum spacing between explicitness-stage advances.' },
    { name: 'nonce_ttl_sec', label: 'Deeplink nonce TTL (sec)', type: 'int', min: 10, max: 3600, help: 'Lifetime of a one-time deeplink nonce.' },
    { name: 'profile_pull_ttl_sec', label: 'Profile pull TTL (sec)', type: 'int', min: 0, max: 604800, help: 'How long a pulled player profile stays fresh before a re-pull.' },
    { name: 'session_idle_minutes', label: 'Session idle (min)', type: 'int', min: 0, max: 525600, help: 'Idle minutes before a Telegram chat closes; the next message starts a fresh chat (0 = never close).' },
    { name: 'carry_context_turns', label: 'Carry-over context turns', type: 'int', min: 0, max: 50, help: 'Trailing turns of the previous chat shown to the model when a returning player starts a fresh one (0 = off).' },
    { name: 'play_reminder_every_msgs', label: 'Play reminder every N replies', type: 'int', min: 0, max: 1000, help: 'Every N-th of Nika’s Telegram replies weaves in a light in-context invitation to play, with a one-tap site button picked from the Site map by intent (0 = off).' },
    { name: 'v2_enabled', label: 'Agent enabled', type: 'bool', section: 'agent', help: 'The proactive agent for this product: reacts to casino events (deposits, level-ups, losses) with a decision per event. Off = no proactive messages at all (the dialogue bot still answers).' },
    { name: 'v2_dry_run', label: 'Dry-run (shadow mode)', type: 'bool', section: 'agent', help: 'ON: the agent decides and logs to the Decisions ledger but sends nothing. Turn off only after reviewing decisions.' },
    { name: 'worker_interval_sec', label: 'Worker interval (seconds)', type: 'int', min: 5, max: 3600, section: 'agent', help: 'How often the background worker drains the event queue. Applies live on the next tick (no redeploy). 5s = near-realtime reactions.' },
    { name: 'ping_batch_size', label: 'Events per sweep', type: 'int', min: 1, max: 500, section: 'agent', help: 'Max events one worker sweep processes per product — bounds the burst on Telegram and OpenAI.' },
    { name: 'v2_daily_budget_usd', label: 'Daily AI budget (USD)', type: 'float', min: 0, max: 10000, section: 'agent', help: 'Hard stop: once the day’s decisions cost this much, the agent goes quiet until tomorrow. 0 = no budget.' },
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
  ],
};
