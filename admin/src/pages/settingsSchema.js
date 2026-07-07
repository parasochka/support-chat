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
    'Rate limiting, cooldowns, reCaptcha and the injection / low-content guards that run before the model.',
  model:
    'OpenAI request tuning. Edits are hot — the next turn uses them (the client is rebuilt on save).',
  general:
    'Operational limits with no other home: session/token lifetimes, the message cap, prompt-history window and the request body cap.',
  retention:
    'Telegram retention-bot pacing: photo caps, cooldowns, explicitness-stage progression and profile freshness.',
  language:
    'Which languages the assistant supports and the default. Answers follow the player; the widget chrome follows the browser.',
};

export const GROUP_FIELDS = {
  antispam: [
    { name: 'rate_limit_max_per_ip', label: 'Rate limit (max / IP)', type: 'int', min: 1, max: 100000, help: 'Maximum requests from one IP within the window.' },
    { name: 'window_sec', label: 'Rate-limit window (sec)', type: 'int', min: 1, max: 86400, help: 'Length of the rate-limit window in seconds.' },
    { name: 'cooldown_sec', label: 'Message cooldown (sec)', type: 'int', min: 0, max: 3600, help: 'Minimum seconds between two messages in one session.' },
    { name: 'max_input_chars', label: 'Max input characters', type: 'int', min: 1, max: 100000, help: 'Longest single message the API accepts.' },
    { name: 'recaptcha_min_score', label: 'reCaptcha min score', type: 'float', min: 0, max: 1, step: 0.05, help: 'Minimum reCaptcha v3 score to accept a new session (0–1).' },
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
    { name: 'admin_token_ttl_min', label: 'Admin token TTL (min)', type: 'int', min: 5, max: 10080, help: 'Admin login lifetime in minutes (5 min … 1 week).' },
    { name: 'max_messages_per_session', label: 'Max messages / session', type: 'int', min: 1, max: 10000, help: 'Message cap before the session hands off to a human.' },
    { name: 'history_max_turns', label: 'History turns to model', type: 'int', min: 1, max: 200, help: 'Recent turns fed into the prompt history (full transcript is always stored).' },
    { name: 'body_max_bytes', label: 'Max request body (bytes)', type: 'int', min: 1024, max: 104857600, help: 'Largest accepted request body (1 KiB … 100 MiB).' },
  ],
  retention: [
    { name: 'daily_photo_cap', label: 'Daily photo cap', type: 'int', min: 0, max: 10000, help: 'Max photos sent to one player per day (hard, incl. requested).' },
    { name: 'proactive_photo_cooldown_msgs', label: 'Proactive photo cooldown (msgs)', type: 'int', min: 1, max: 10000, help: 'Messages between UNPROMPTED photos (a direct ask bypasses it).' },
    { name: 'candidate_list_size', label: 'Photo candidate list size', type: 'int', min: 1, max: 50, help: 'How many photo candidates the model is offered to choose from.' },
    { name: 'stage_advance_min_hours', label: 'Stage advance min hours', type: 'int', min: 0, max: 8760, help: 'Minimum spacing between explicitness-stage advances.' },
    { name: 'max_stage', label: 'Max stage', type: 'int', min: 1, max: 20, help: 'Global ceiling on the explicitness stage.' },
    { name: 'nonce_ttl_sec', label: 'Deeplink nonce TTL (sec)', type: 'int', min: 10, max: 3600, help: 'Lifetime of a one-time deeplink nonce.' },
    { name: 'profile_pull_ttl_sec', label: 'Profile pull TTL (sec)', type: 'int', min: 0, max: 604800, help: 'How long a pulled player profile stays fresh before a re-pull.' },
    { name: 'session_idle_minutes', label: 'Session idle (min)', type: 'int', min: 0, max: 525600, help: 'Idle minutes before a Telegram chat closes; the next message starts a fresh chat (0 = never close).' },
    { name: 'carry_context_turns', label: 'Carry-over context turns', type: 'int', min: 0, max: 50, help: 'Trailing turns of the previous chat shown to the model when a returning player starts a fresh one (0 = off).' },
    { name: 'pings_enabled', label: 'Proactive pings enabled', type: 'bool', help: 'Master switch for the ping matrix (Retention → Pings). Off = no proactive messages at all.' },
    { name: 'ping_daily_cap', label: 'Ping daily cap', type: 'int', min: 1, max: 24, help: 'Max proactive pings one player may receive per day.' },
    { name: 'ping_min_gap_hours', label: 'Ping min gap (hours)', type: 'int', min: 1, max: 720, help: 'Minimum hours between two pings to the same player (up to 30 days).' },
    { name: 'quiet_hours_start', label: 'Quiet hours start (0–23)', type: 'int', min: 0, max: 23, help: 'Hour when the no-ping window begins (players are not pinged at night).' },
    { name: 'quiet_hours_end', label: 'Quiet hours end (0–23)', type: 'int', min: 0, max: 23, help: 'Hour when the no-ping window ends and pings may resume.' },
    { name: 'quiet_hours_utc_offset', label: 'Quiet hours UTC offset', type: 'int', min: -12, max: 14, help: 'Timezone offset the quiet hours are evaluated in (e.g. 3 = UTC+3).' },
    { name: 'ping_batch_size', label: 'Ping batch size', type: 'int', min: 1, max: 500, help: 'Max pings sent in one sweep run — bounds the burst on Telegram and OpenAI.' },
    { name: 'stage_advance_msgs', label: 'Stage advance thresholds', type: 'intlist', help: 'Accumulated meaningful messages required for stages 2 / 3 / 4 … (one per line).' },
    { name: 'vip_tiers', label: 'VIP tiers (ordered)', type: 'strlist', help: 'Ordered tier names; a tier’s index is its ordinal (one per line).' },
    { name: 'max_stage_by_tier', label: 'Max stage by tier', type: 'intmap', help: 'Highest photo stage each VIP tier may unlock.' },
  ],
};
