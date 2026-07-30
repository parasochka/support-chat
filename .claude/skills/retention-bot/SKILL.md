---
name: retention-bot
description: >-
  Architecture and invariants of the Telegram RETENTION subsystem of this
  service: the second front-end over the same AI core (retention.py,
  retention_v2.py, retention_idle.py, telegram_transport.py, telegram_format.py,
  delivery.py, media_normalizer.py, player_sync.py, starter_kb retention seeds).
  Covers the retention prompt mode and its sentinels, the media library and
  file_id cache, stage/VIP progression, deeplink entry and the subscription
  gate, hand-off, the event-driven proactive agent, the idle-ping ladder, the
  canonical event feed and profile sync, retention analytics and the Retention
  admin surface. Use whenever you touch any of those modules, the Telegram bot's
  behaviour or copy, proactive/idle messaging, retention photos or videos, or
  the retention KB and prompt variables. NOT for the support chat widget, its
  topic routing/escalation, or the shared prompt core - those are in CLAUDE.md.
---

# Retention bot (Telegram) subsystem

This is the lazily-loaded companion to the root `CLAUDE.md`. The shared
architecture (3-layer prompt design, multi-tenancy, settings resolution,
translations, the numbered invariants) lives there and still applies here in
full - especially invariants 3, 4, 5 and 6.

### RETENTION BOT — Telegram second facade (`retention.py`, `telegram_transport.py`)
A **second front-end over the same AI core**: from the site a player deep-links into a
Telegram bot where **Nika runs retention only** (warm, flirtatious engagement + photos under
the player's profile). She does **not** handle support — any support PROBLEM (complaint,
account block, stuck/failed deposit-withdrawal, responsible gaming, ask-for-a-human) is routed
**out** via the hand-off CHOICE message (personal manager in Telegram and/or the site's support
chat — see the `[[HANDOFF]]` bullet below). A simple **navigation** question ("how do I
deposit?", "where do I play X?") is NOT a hand-off: she answers it herself and attaches the
matching SITE MAP page as a `[[LINK:url]]` button — the problem-vs-directions split is stated
in the retention core, the SITE LINK BUTTON directive AND the Layer-3 `_RETENTION_GUARDRAILS`
(the guardrail rides last, so a blanket "money → HANDOFF" there used to override the
navigation exception — the "как задепать → саппорт" bug). This section IS the spec (the
old `RETENTION_BOT_SPEC.md`/`RETENTION_SETUP.md` files were removed); the operator's setup
checklist lives in the admin — the **Retention → How it works** page.

- **Transport vs. brain vs. AI turn are separated on purpose** so the transport can be lifted
  into its own service later: `telegram_transport.py` (HTTP to the Bot API + update parsing,
  holds no logic), `retention.py` (the orchestration: nonce exchange, subscription gate, entry
  menu, photo selection/gating, manager round-robin, progression), `chat_service.handle_retention_message`
  (the AI turn: build prompt → model → strip sentinels → persist), and **`delivery.py`** — the
  outbound delivery seam for messages the service INITIATES (the event agent + the idle ladder
  send only through its channel objects; a future email/push/on-site channel plugs into
  `channel_for_product()` without touching the proactive pipelines). The dialogue reply path
  (`retention._send_ai_text` burst delivery + typing) stays Telegram-specific by design.
- **Channel = the existing `consumer` column** (`'web'` → `'telegram'`), NOT a new `channel`; the
  mode is derived from it (telegram ⇒ retention). Support is never duplicated in Telegram.
  **Telegram chats are logged APART from support chats**: the support admin surfaces
  (`db.list_sessions`, `db.unresolved_by_topic` — the Conversations + Unresolved views) exclude
  `consumer='telegram'` entirely; the Telegram chats live in their own **Retention →
  Conversations** page (`GET /admin/retention/sessions` → `db.list_retention_sessions`, joined
  with the `retention_users` identity + summed cost; the transcript opens via the shared
  `GET /admin/session/{id}`, same scope check). The transcript interleaves the **media
  delivered in that session** (`db.session_detail`'s `photos` — the `retention_photo_views`
  rows joined to the library) with the messages by timestamp, each rendered by the SAME
  `PhotoPreview` component the Media grid uses: the query returns `media_type` +
  `storage_ref` per item, so a VIDEO shows its extracted poster frame (`?poster=1`) with a
  play badge (clicking plays it) and is labelled «video» — without those two fields the
  transcript asked for the video binary as an `<img>` and painted a broken thumbnail. Any
  new media kind must carry its type through this query for the same reason.
  **Deleting a Telegram conversation
  (`DELETE /admin/session/{id}` → `db.delete_session`) also PURGES the linked player**: after
  the transcript rows (chat_messages / session-linked ai_interaction_logs / admin_events /
  chat_sessions) go, `_purge_retention_player` deletes that player's `retention_photo_views`,
  `retention_pings` and the `retention_users` row (keyed by the session's product + `tg_user_id`,
  so it fires even for an old rolled-over session). Without this the player kept showing up in
  the retention dashboards after their chat was deleted — the analytics draw from
  `retention_users`/`retention_photo_views`/`retention_pings`, not the transcript. Product-level
  historical counters logged session-less (funnel `retention_deeplink_created`/`retention_start`,
  the photo-metadata generation cost) are NOT attributable to one player and stay. Support
  session deletes need no such extra step — every support metric is keyed to `session_id`, so the
  transcript delete already zeroes them out.
- **Telegram chat lifecycle — idle rollover + returning-player continuity.** A Telegram
  conversation has no "close the widget" moment, so a chat "ends" by INACTIVITY: on the next
  incoming message `retention._ensure_session` reuses the linked session only while it is
  `open` and not idle past the `retention.session_idle_minutes` knob (default 360; 0 = never —
  the old endless-session behaviour). An idle (or already-closed) chat with messages is closed
  lazily — `db.close_retention_session` sets `status='resolved'` + logs
  `admin_events('retention_session_closed')` — and a FRESH session is created pointing back via
  `chat_sessions.prev_session_id` (guarded ALTER; an empty open session is simply reused, no
  churn). **Continuity:** on the first turn of the fresh session,
  `chat_service.handle_retention_message` pulls the tail of the previous chat
  (`carry_context_turns` knob, default 10, 0 = off) and passes it to
  `prompts.build_retention_messages(previous_history=…)`, which renders a Layer-3
  `RETURNING PLAYER — PREVIOUS CONVERSATION (context only)` block (messages truncated to ~240
  chars, rough "N hours/days ago" recency): greet back warmly like someone she knows, never
  re-introduce, don't re-answer the old messages — and when the old chat left a concrete thread
  (his plans, his mood, a game), ask warmly how it went (the short-term memory the player
  actually FEELS). It rides ONLY on the first turn (never as
  message history — it is a new chat); durable state (stage progression, seen photos, manager,
  language, profile) lives on `retention_users` and survives rollover by construction.
  **Proactive pings get the same continuity** (`chat_service.generate_retention_ping` →
  `prompts.build_retention_ping_messages(previous_history=…)`): a ping is usually the FIRST
  message of a fresh session (the idle rollover just closed the old chat), and the ping task
  demands concrete call-backs to what the player said — which needs something to call back to.
  Tests: `tests/test_retention_lifecycle.py`.
- **Retention prompt mode (`prompts.py`)** is a SECOND Layer-1 assembly — `SYSTEM_CORE_RETENTION`
  + retention static directives (`get_retention_system_core()`), byte-stable per **product × mode**
  (a test asserts it, mirroring the support core). It shares the persona but swaps support
  behaviour for engagement + photos + route-out. **No** KB-grounding / escalation-restraint /
  topic-routing / suggestions here — and its OWN **light** Telegram formatting: retention
  replies are sent with `parse_mode=HTML`, so the retention Layer 1 carries its OWN
  `_RETENTION_FORMATTING_DIRECTIVE` (a TOUCH of `**bold**`/`*italic*` allowed, no
  lists/headings/tables/link-markup, bare URLs, and — a hard rule — NO em/en dashes or
  guillemet/angle quotes) instead of the support `_FORMATTING_DIRECTIVE`. The persona's
  emphasis is rendered by **`telegram_format.to_html`** (Markdown-subset → balanced,
  HTML-escaped Telegram HTML; bare URLs + code spans stashed so their punctuation survives),
  applied at every retention AI-text send site (`retention._send_ai_text`, photo captions,
  the ping worker) with a plain-text fallback so a bad-HTML send never silently drops. The
  "AI-tell" typography the model keeps emitting despite the rule is ALSO scrubbed
  deterministically after the model turn (`telegram_format.normalize_punctuation` in
  `chat_service` — em/en dashes → `-`, guillemet/curly quotes → straight ASCII), so the
  persisted transcript and the sent message match. **Liveliness rules (static, Layer 1)** —
  tuned after a live transcript read like a bot: emoji in ordinary TEXT messages are **banned
  outright** (a repeated 😉 was the loudest bot-tell; support Nika uses none either) — the ONLY
  two allowed emoji are chrome-level exceptions with a strict priority: a PHOTO caption may end
  with a SINGLE emoji picked from THAT photo's own content/mood, and a plain-text message
  carrying a site-link button ends with the single 👇 hand — never both (a photo with a button
  keeps only the caption's mood emoji; the 👇 is never added on a photo); replies default to
  1-2 short sentences with varied length and rhythm (longer only when asked for a
  story/details), and a reply MAY arrive as a burst of consecutive Telegram messages (blank-line
  split in the model text, delivered as separate sends with a typing pause —
  `retention._split_reply_parts`: usually one message, sometimes two, rarely three; an inline
  button rides on the LAST part); the "do you want X or Y?" two-option
  closer is explicitly banned as a template, and question-ending is rationed (at most one
  message in two-three ends with a question); the ENGAGEMENT directive **bans self-initiated
  play invitations outright** — the model may talk games/bonuses ONLY when the player raises
  the subject or the Layer-3 PLAY NUDGE block orders one invitation (so the nudge cadence knob
  is the ONE pacing control; the old "invite every so often when it flows naturally" permission
  made the model pitch slots from the first reply and was removed), orders comfort-mode with
  NO play talk after the player says he lost money (even a due nudge is skipped), and demands
  concrete call-backs to what the player said earlier instead of generic lines plus
  freshly-invented (never recycled) small "life details"; photo
  captions must be UNIQUE and grounded in the current moment + the chosen photo's description
  (stock lines like "just for you" repeated per photo are named as the failure mode). The retention core
  renders with the **retention prompt-variable set**
  (`prompts.render_retention_prompt_variables` — retention override > retention default, a
  SEPARATE prompt with NO support inheritance, incl. its OWN tone `{retention_tone_of_voice}`)
  — see "Prompt variables"; the
  bot's model-free chrome (`retention._persona_name`) resolves the same way, so the menu
  greeting matches the persona the prompt runs. Layer 2 = the **whole** retention-KB (`db.retention_kb_block`,
  NOT `kb_topics`). **The retention KB is edited as ONE free-text document per product** (like a
  support topic's KB text): stored as a single `retention_kb` row with the sentinel title
  `db.RETENTION_KB_DOC_TITLE` (its body enters the prompt verbatim, no header);
  `db.get_retention_kb_text`/`set_retention_kb_text` are the document read/write (the write
  replaces the product's whole KB in one transaction), exposed via
  `GET/PUT /admin/retention/kb/text`. Legacy structured rows (the old per-entry editor) still
  render in the prompt and are folded into the document text on the first save; the per-entry
  CRUD endpoints remain for API consumers. New products are seeded with
  `starter_kb.STARTER_RETENTION_KB`. Layer 3 (`build_retention_dynamic_prompt`) = full profile
  personalization + language directive + the **appearance block** (`prompts._appearance_directive`,
  fed by `db.retention_appearance_context`: a stable sample of the product's photo-library
  descriptions + the photo THIS player saw last — the persona's looks are grounded in the REAL
  photos even on turns where no photo is sendable, so the model can never invent contradicting
  hair/outfit; fetched best-effort in `retention._run_nika_turn`) + the **photo-candidate
  list** (whose empty-state text steers away from the "I have no photos" flat refusal and
  toward an appearance-grounded tease + a once-per-chat progression hint) + the **openness
  ladder** (`prompts._verbal_register_directive` — how bold Nika's WORDS may be: keyed to the
  boldest photo the player actually RECEIVED (falling back to his unlocked stage, capped at 2,
  when nothing was sent), so the verbal register keeps pace with the photos instead of a fixed
  "never explicit" — a player getting stage-5 photos no longer gets stage-1 prudery in text;
  the tone-of-voice default defers to this block, and the ceiling stays short of pornography
  at every level) + a lighter retention guardrail.
  **Retention personalization is its OWN directive** (`prompts._retention_personalization_directive`,
  NOT the support one): in Telegram the bot chrome has ALREADY greeted the player by name
  TWICE before the first model turn (the `rtn_menu_greeting` menu message + the
  `rtn_nika_start` opener), so where the support widget ORDERS a first-reply by-name greeting,
  retention orders the OPPOSITE — an explicit first-turn suppression imperative ("the menu
  already greeted; do NOT greet or introduce yourself"), later turns get the name-sparing
  wording, and a RETURNING player's fresh session (rollover) is the one case where a greeting
  happens: the personalization defers to the continuity block's short welcome-back. The
  `rtn_nika_start` copy is greeting-free BY CONTRACT (a conversation opener, not a hello) —
  it used to open with "Привет!", stacking a triple greeting on the player's screen.
- **Retention sentinels** (stripped like the support ones): `[[PHOTO:id]]` (send a photo from the
  candidate list the model was shown — backend re-validates the id), `[[STAGE_UP]]` (a hint the
  player is ready for the next explicitness stage — the backend gate decides), `[[HANDOFF]]`
  (route out; writes `admin_events('retention_handoff')`), `[[LINK:url]]` (a site-map CTA button —
  next bullet), `[[LANG:xx]]` (as everywhere). Strip helpers: `prompts.strip_photo_tag` /
  `strip_stage_up_tag` / `strip_handoff_tag` / `strip_link_tag`.
- **Site-map CTA button (`[[LINK:url]]`) + the periodic play reminder.** When retention Nika
  invites the player somewhere concrete on the site (come play, deposit, check the balance), she
  emits `[[LINK:url]]` with a URL copied EXACTLY from the Layer-1 SITE MAP block (static directive
  `prompts._RETENTION_LINK_DIRECTIVE`; at most one per reply, never pasted into the visible text).
  The backend re-validates it (`chat_service.resolve_site_link` — an EXACT match against
  `settings.site_map()`, so the model can never button-ify an invented address; the page `title`
  becomes the button label, falling back to the url) and the message ships with ONE inline
  url-button (`retention._run_nika_turn`; `_send_ai_text`/`_send_photo` and the transport photo
  senders all take `reply_markup`). On a plain TEXT message carrying a link button the directive
  makes Nika end the reply with a single 👇 hand pointing at the button — the ONE emoji allowed on
  an ordinary text reply, and never added on a photo (a photo caption already carries its own
  single mood emoji, so the hand would collide). A `[[HANDOFF]]` turn drops the link (the player is
  leaving for support). Play invitations are **nudge-only** (self-initiated invites are banned by the
  engagement directive — see the liveliness bullet): the **`retention.play_reminder_every_msgs`
  knob** (default 5, 0 = off; env `RETENTION_PLAY_REMINDER_EVERY_MSGS`) is the ONE pacing
  control. `chat_service.play_nudge_due` keys on the session's `message_count` (one bump per
  persisted turn; never the very first reply) and the cadence **DRIFTS ±2 around N**
  (cumulative schedule, jitter keyed on session_id + cycle via `_nudge_jitter` — stateless,
  reproducible, gaps always within N±2): a strictly periodic every-5th-message invitation
  was a pattern a player could clock. The due reply carries the Layer-3
  `prompts._PLAY_NUDGE_DIRECTIVE` — explicitly framed as "the ONE permission you get to
  invite": continue the conversation normally, weave in ONE light in-context invitation to
  play, attach the best-fitting site-map page as the button — **and ROTATE the
  destination**: the attached `[[LINK:url]]` is persisted on the message row
  (`chat_messages.link_url`) and rendered into the retention prompt history
  ("[with this message you attached a site page button: …]",
  `prompts._retention_history_content`), and the nudge directive orders a DIFFERENT
  fitting page than the previous invitation (main page / casino / slots / tournaments)
  — without the history note the model could not see which button it already sent and
  pinned the same page every time. Skip the invitation entirely in a
  complaint/money/just-lost/sensitive moment. Tests: `tests/test_retention_cta.py`,
  `tests/test_naturalness.py`.
- **Media library + file_id cache**: `retention_photos` holds PHOTOS **and short
  VIDEOS** in one catalogue/stream (`media_type` column, `'photo'`/`'video'` by upload
  extension) — the same gating, unseen-tracking, daily cap, candidate feed and file_id
  cache serve both; the delivery path (`retention._send_photo`) picks
  `sendPhoto`/`sendVideo` (+ `extract_video_file_id`) from the row's type, and the
  captionless fallback uses video-worded copy (`rtn_video_caption*`). The candidate
  list (default size **6** — `candidate_list_size`, env `RETENTION_CANDIDATE_LIST_SIZE`)
  reserves a **fixed video share** (`db._video_slot_cap` in `db.candidate_photos`):
  6 → 4 photos + 2 videos, and the share never drops below 2 while the list has room
  (4 → 2+2; only tiny lists shrink it: 3 → 2 photos + 1 video, 2 → 1+1, 1 → photo-only;
  bigger lists scale at ~⅓). The backfill is symmetric — photos fill unused video
  slots AND spare photo slots go to further videos, so the feed only shrinks when
  both kinds are exhausted; a video that has not yet
  been normalized (`storage_ref` not `.tg.mp4`) is NEVER offered or sent
  (`db._VIDEO_SENDABLE_SQL` + a backstop in `retention._send_photo` via
  `media_normalizer.is_normalized_video_ref`), so a raw
  multi-hundred-MB original can't reach Telegram before the transcode. The Layer-3
  candidate line carries the type (`id | photo-or-video | stage | …`) and the photo
  directive tells the model to word the caption for what is actually sent ("here's my
  video…"); an explicit video ask («пришли видео») bypasses the proactive cooldown via
  the same `is_photo_request` stems AND (the video-worded stems,
  `retention.is_video_request`) biases that turn's feed to videos-only, falling back
  to the mixed feed when no unseen video is sendable. Idle-ladder rules take
  **`action: message | photo | video`** — `photo` = the mixed feed, `video` = videos
  only (`select_photo_candidates(media=…)`); the v2 agent's `photo` action uses the
  mixed feed too. It gates by `level_min` (VIP-tier ordinal) ×
  `stage` (explicitness). **Both values are bounded to the product's real ranges on EVERY write**
  — `stage` to 1..`max_stage`, `level_min` to 0..(last tier ordinal) — whether the value is
  AI-generated OR hand-entered/API-posted (`app.api.retention._clamp_photo_gate`, applied in
  `create_photo` + `update_photo`; the SPA Media pickers offer only in-range choices), so a
  photo can never gate outside what the delivery gate can serve (no stage 0/6, no tier past the
  ladder). The first send uploads the binary from the media dir (Railway Volume,
  `RETENTION_MEDIA_DIR`); Telegram returns a `file_id` cached on the row so later sends skip the
  re-upload/egress. **Uploads are auto-normalized for Telegram** (`media_normalizer.py`): content
  managers upload originals as they come (multi-MB JPEGs at 8000×4000), but Telegram re-compresses
  every photo to ~2560px anyway, so a periodic sweep (hourly by default; own asyncio task from
  `main.py` lifespan under the same `RETENTION_SCHEDULER_ENABLED` switch, own advisory lock)
  re-encodes every .jpg/.png (and any oversized .webp) to WebP at
  `RETENTION_MEDIA_MAX_SIDE_PX` (default 2560) × `RETENTION_MEDIA_WEBP_QUALITY` (90), re-points the row
  (`db.set_retention_photo_storage_ref`) and **deletes the heavy original** — GIFs are left alone
  (possibly animated), the cached `telegram_file_id` is KEPT **for photos** (the already-uploaded
  copy stays valid; a VIDEO re-point clears it — see below), and the row is re-pointed BEFORE the
  delete so a crash can orphan a file but never break
  a photo. **Videos** normalize through the same module via **ffmpeg** (installed in the Docker
  image): re-encoded ONCE to Telegram-friendly `<base>.tg.mp4` (H.264 + AAC, faststart, longest
  side `RETENTION_MEDIA_VIDEO_MAX_SIDE_PX`/1920 (a vertical 1080×1920 reel keeps native
  resolution; the CRF re-encode still shrinks a bloated source ~6-10×), CRF
  `RETENTION_MEDIA_VIDEO_CRF`/26, preset `RETENTION_MEDIA_VIDEO_PRESET`/medium — deploy env
  constants, deliberately no admin knobs). The scale filter works in **DISPLAY terms and forces
  square pixels** (`iw*sar` + `setsar=1`): an anamorphic source (SAR≠1) would otherwise pass its
  SAR through — browsers honor it (the admin preview looked fine) but Telegram renders raw storage
  pixels, so players got a horizontally squished video. After each encode the file is **ffprobe'd**
  (`media_normalizer.probe_video_meta`) and width/height/duration land on the row
  (`tg_width`/`tg_height`/`tg_duration_sec`, `db.set_retention_video_normalized` — which also
  **clears `telegram_file_id`**: a video file_id pins the exact uploaded binary, so keeping it
  served the pre-normalization copy forever); `retention._send_photo` passes those attrs plus a
  ≤320px JPEG thumbnail (from the poster, `media_normalizer.make_video_thumbnail`) to `sendVideo` —
  without explicit attrs Telegram may fail to detect them and deliver the video as a
  download-first file with a squished 00:00 bubble — and after a first upload it caches the
  returned file_id ONLY if the row is unchanged since it was read (`updated_at` compare): an
  upload racing the normalizer must not pin the pre-normalization binary. The sweep
  **self-heals** older rows: an
  already-normalized `.tg.mp4` that probes with a non-square SAR (the pre-fix output) is
  re-encoded in place with poster/attrs refresh + file_id drop (the file_id is cleared BEFORE
  the on-disk swap — `db.clear_photo_file_id` — so a crash mid-repair converges instead of
  pinning the squished Telegram copy), and a square one missing its attrs gets them backfilled
  with the file_id cleared as well (a pre-attrs upload may be pinned in the broken
  download-first presentation, and a file_id send cannot attach attrs — one re-upload fixes
  it). Plus a `<base>.poster.webp` frame (the admin grid
  preview via `GET …/photos/{id}/file?poster=1`, and the AI-metadata vision call rates the video
  by that frame — `build_photo_meta_messages(is_video=True)`); the `.tg.mp4` suffix is the
  done-marker, and a normalized video still over Telegram's 50 MB bot cap is loudly logged,
  DROPPED from the candidate feed (`retention._sendable_media`; a cached `telegram_file_id`
  keeps it offerable — id-sends don't re-upload) and, if a send is still attempted, delivered
  as the caption-text fallback instead of silently failing the upload.
  Encodes run one at a time PER PRODUCT at low OS priority (`nice`, `-threads 2`) so a bulk
  upload never starves the serving process: the advisory lock is the two-int
  `(key, product_id)` form — one product's minutes-long sweep never delays another product's
  instant post-upload run — and it rides a DEDICATED connection
  (`db.dedicated_connection`), never a pool slot — a minutes-long encode must not eat one of
  the 10 request connections (and the pool's `command_timeout` would kill a blocking
  `pg_advisory_lock` wait). The hourly sweep also removes **orphaned media-dir files**
  (`media_normalizer._cleanup_orphans`: anything no row references — a crash-surviving raw
  original, a deleted row's encode output, a failed repair's `.fix.mp4` tmp — once older than
  a day; the age guard is what makes it safe to run unlocked next to in-flight encodes).
  **Normalization also runs IMMEDIATELY after an upload**: the
  upload endpoint fires `media_normalizer.schedule_product_normalization` (a background task
  under the SAME per-product advisory lock as the sweep, deduped per product; an upload landing
  while a run is in flight marks a RE-RUN — a pass that already listed the library can't see
  rows created after it — and the fire-and-forget task is strongly referenced, the documented
  create_task GC gotcha), so new media is
  delivery-ready in moments — the periodic sweep stays as the catch-up (the Media tab also
  re-polls the list briefly after a video upload so posters/«optimized» marks appear without a
  manual refresh). **Normalization is
  ALWAYS ON and fully code-owned — there is deliberately NO admin knob and NO on/off switch**
  (the whole sweep loop is still gated by the deploy-wide `RETENTION_SCHEDULER_ENABLED`, which
  governs every background worker). Every parameter is a deploy-level constant in `config.py`:
  `RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC` (sweep cadence), the photo target
  (`RETENTION_MEDIA_MAX_SIDE_PX` / `RETENTION_MEDIA_WEBP_QUALITY`) and the video target
  (`RETENTION_MEDIA_VIDEO_*`). The `retention` settings group and the admin Settings tab no
  longer carry any `media_*` normalization field. `POST /admin/retention/photos/normalize`
  runs one product's sweep immediately (API-only — no UI button; the always-on sweep + the
  post-upload run make it unnecessary), advisory-locked like every other pass — calling it
  mid-sweep waits instead of double-encoding. **Upload limits.** The whole request body is capped by
  `RETENTION_MAX_UPLOAD_BYTES` (deploy env, default 512 MiB — sized for raw video originals),
  and each file is bounded by type: `RETENTION_MAX_PHOTO_BYTES` (10 MiB) +
  `RETENTION_MAX_PHOTO_SIDE_PX` (8000 px longest side) for photos, `RETENTION_MAX_VIDEO_BYTES`
  (100 MiB) + `RETENTION_MAX_VIDEO_DURATION_SEC` (60 s) for videos. `/admin/meta` exposes all of
  them (`retention_max_upload_bytes`, `retention_max_photo_bytes`, `retention_max_photo_side_px`,
  `retention_max_video_bytes`, `retention_max_video_duration_sec`); the SPA Media tab lists them
  in an info-(i) tooltip beside «Upload» and validates every selected file BEFORE uploading —
  size by type + photo resolution (`Image`) + video duration (`<video>` metadata), read in the
  browser — blocking the upload with a per-file error. The server ALSO enforces the per-file
  byte caps in `create_photo` (before any file is written, so one over-cap file rejects the
  batch); the resolution/duration caps stay a client-side guard (verifying them server-side
  would mean decoding every upload, and the Media tab is admin-only). The batch cap matters
  because an over-cap 413 aborts the connection mid-upload and the browser reports only an
  opaque «failed to fetch». Requires `Pillow` (requirements.txt). Tests:
  `tests/test_media_normalizer.py`. **Upload is bulk-friendly** (`POST /admin/retention/photos` takes any number
  of `files` — photos AND videos, `media_type` set by extension — in one request; the single `file` field stays for older consumers) and metadata is
  **AI-generated on demand**: `POST /admin/retention/photos/generate-metadata` (`{ids: […]}`,
  ≤20/request — the SPA chunks bigger selections) runs one vision call per photo through the
  product's OWN OpenAI client (`client_for_product`) + the product-resolved `model` settings
  group, using the prompt in `prompts.build_photo_meta_messages` (wording in `prompts.py`, the
  single source of truth), and fills `description`/`tags`/`stage`/`level_min`; the reply is
  strict JSON, parsed + **clamped against the product's real `vip_tiers`/`max_stage`**
  (`app.api.retention._parse_photo_meta`, sharing the same bounds as the write-time
  `_clamp_photo_gate`) so a hallucinated number can never unlock a photo beyond
  the delivery gate, every call lands in `ai_interaction_logs` (invariant §4, `session_id=NULL`),
  and one failed photo never kills the batch. Descriptions are demanded in plain everyday words
  (hair = colour + length, simple clothing terms — no haircut names / fashion-catalogue jargon:
  the persona voices this text in chat). The batch runs in **waves of 5** with the library's
  current stage/level distribution injected into the prompt (`prompts._PHOTO_META_BALANCE`,
  counts refreshed between waves from the fresh ratings) so borderline calls land on the
  under-filled levels and the library spreads evenly across the whole ladder instead of
  clustering on one-two values. The SPA Media tab adds checkbox selection +
  "Generate metadata" and client-side filters (search/stage/level/status).
  **Candidate selection is pre-model** (`retention.select_photo_candidates`):
  unseen, tier×stage-gated (current stage + 1 teaser, capped by the tier ceiling), bounded by the
  **daily cap** (hard, reactive included) and the **proactive cooldown** (bypassed when the player
  explicitly asks — `is_photo_request`). Empty candidate set ⇒ the model is told to keep chatting
  with text and not promise a photo. The model's reply text becomes the photo **caption**, grounded
  on the candidate descriptions it was shown (one call — no separate caption round-trip).
  **Introduction photo (`retention.intro_photo_due`)**: a BRAND-NEW player — never received a
  photo (`db.has_photo_views`), within his first `intro_photo_within_msgs` meaningful messages
  (default 3) — gets one proactively: the selection bypasses the proactive cooldown for that turn
  (daily cap + tier×stage still gate) and, when candidates exist, Layer 3 carries the IMPERATIVE
  `_INTRO_PHOTO_DIRECTIVE` ("you MUST send one photo from the candidates this turn" — imperative
  on purpose, the greeting-hygiene lesson: a conditional permission loses to the static restraint
  rules) with a model-written "this is me — let's get to know each other" caption (localized,
  grounded in the chosen photo's description — never a canned string), so the player learns from
  the very start that chatting comes with photos. Knobs `intro_photo_enabled` (ships ON) /
  `intro_photo_within_msgs` in the hot `retention` group (Retention → Settings → Parameters; env
  defaults `RETENTION_INTRO_PHOTO_*`). The view row lands in the same transaction as the send, so
  the rule can never refire after a delivery.
- **Progression is FULLY backend-decided** (`retention.maybe_advance_stage`, evaluated on every
  meaningful message): the `unlocked_stage` advance needs the engagement threshold
  (`stage_advance_msgs`) **and**
  the tier ceiling (`max_stage_by_tier`) **and** spacing (`stage_advance_min_hours`). The model's
  `[[STAGE_UP]]` sentinel is stripped defensively but has NO say in the gate (an earlier version
  took it as a parameter and ignored it, which read as if the model decided). VIP tier is
  mapped from the free-text `vip_level` via the ordered `vip_tiers` list. All knobs are in the
  **`retention` settings group** (`settings.retention()`, in `SETTING_KEYS` — per-product tunable).
  **Progression is player-visible now, on two sides.** (1) A REAL advance is **celebrated**:
  after `maybe_advance_stage` unlocks a stage, `retention._send_stage_up_note` (gated by the
  `stage_up_notify` knob, default on) generates a persona follow-up via the ping stack
  (`chat_service.generate_retention_ping(stage_up=…)` → `prompts._RETENTION_STAGE_UP_TASK`: "we
  just got closer — more daring photos from now on", plus a keep-chatting hint unless the new
  stage is the player's current ceiling), sends it right after the turn's reply and persists it
  via `db.persist_ping_turn` with `ping_context="stage_up: …"` — so the prompt history renders it
  with its trigger (the player asking «что это было?» gets a real answer) and the admin
  transcript shows the ⚡ proactive marker; an `admin_events('retention_stage_up')` row is logged.
  Best-effort: any failure only skips the note (the advance is already committed). (2) Nika can
  **explain the system**: every dialogue turn's Layer 3 carries a `=== PROGRESSION ===` block
  (`retention.progression_context` → `prompts._progression_directive`: unlocked stage, tier
  ceiling, VIP level, meaningful-message count and the next threshold — the same maths the gate
  enforces, so what she says matches what the backend does), and the static
  `_RETENTION_STAGE_DIRECTIVE` now states the WAY progression works is not a secret (chat more →
  closer → more daring photos; VIP raises the ceiling) — only the machinery (tags, counters,
  "stage" as a system term) stays internal. Tests: `tests/test_stage_progression.py`.
- **Entry = deeplink + one-time nonce** (`retention_nonces`): the site posts a handshake to
  `POST /api/retention/deeplink` → `{nonce, deep_link}`; `/start <nonce>` redeems it (single-use,
  TTL-bounded, **product-scoped** — a nonce minted for brand B's bot never redeems on brand A's,
  so a cross-tenant profile leak is impossible), fixes the **`tg_user_id ↔ player_id` link** + a
  `_CONTEXT_FIELDS` profile snapshot
  in `retention_users`, and sets `entry_type` (`retention` | `escalation`). **The nonce also
  carries the conversation LANGUAGE**: `retention.create_deeplink(..., lang=)` stores a supported
  code in the nonce payload (the widget escalation passes the turn's answer language automatically;
  the site endpoint takes an optional `lang` body field, code or locale) and `/start` adopts it as
  the retention user's `conv_lang` — so a player who chatted in Russian lands in a Russian bot
  (greeting, menu, buttons AND Nika's replies), not the Telegram-client/default language. Without
  it the language falls back to the client `language_code` → default (`resolve_user_lang`); after
  every AI turn `_run_nika_turn` syncs the answer-language drift back onto the `retention_users`
  row so the model-free chrome follows the conversation. No valid nonce ⇒ the
  bot refuses (no organic entry). Then the **channel subscription gate** (`getChatMember`, the bot
  must be a channel admin) before any menu; a product with no channel configured skips the gate.
  After the gate, the entry menu opens with a **personalized persona greeting**
  (`retention._menu_text`: `rtn_menu_greeting`/`_noname` — the persona name from the product's
  `persona_name` prompt variable + the player's first name from the profile snapshot) above the
  `rtn_menu_prompt` line; all `rtn_*` copy supports a `{persona}` placeholder
  (`retention._rtn_text`), and the default button labels carry emoji icons (📢/✅/👤/💬) so the
  buttons read at a glance. The menu ships **structured**: `retention._menu_html` sends the
  greeting as a bold HTML line above the plain prompt (both HTML-escaped — the copy is
  admin-edited text and the name is player data), with an automatic plain-text resend if
  Telegram rejects the HTML.
  Two things mint that deeplink: (1) the **support-chat widget's escalation button** — when the
  product runs retention, every escalation hand-off routes the player INTO the bot on the
  **escalation entry** (`escalation=True` → the manager option in the menu), via
  `escalation.build_payload_for_session` (see the Escalation section). This is the PRIMARY path —
  the widget is the main channel. (2) the optional site buttons below (secondary integration).
- **Profile freshness degrades softly** — all three levels ship: snapshot + re-handshake;
  **lazy pull** (`retention.maybe_pull_profile`, gated by `profile_pull_ttl_sec`) — before a turn,
  if the snapshot is stale and the product has a `player_api_url` + encrypted key, GET the fresh
  profile and update the snapshot (best-effort: a failure leaves the snapshot untouched; the
  outbound connection is **DNS-pinned** — `player_sync.resolve_pinned_outbound` vets the
  resolution once and connects to that literal IP with the original Host/SNI, so a low-TTL
  rebinding domain can't pass the SSRF guard and then reconnect to an internal address); and
  **push webhook** `POST /partner/{product_id}/player-update` (authorized with the product's
  handshake secret as the shared partner secret). Partial updates only. A product with no Player
  API just lives on the snapshot — the schema degrades, never breaks. Both pull and push now
  also accept the **casino activity timestamps** `last_login_at` / `last_played_at` /
  `last_deposit_at` (ISO-8601, parsed + validated in `db.update_retention_profile`; unparsable
  values are dropped) — the agent's state resolver keys on them.
- **Proactive contact is the RETENTION AGENT** (see the "RETENTION AGENT" section
  below) — the one place the bot ever writes FIRST, with TWO triggers: casino
  EVENTS (`retention_v2.py`) and player INACTIVITY (`retention_idle.py` — the
  admin-managed idle rules ladder in `retention_rules`, the successor of the old
  v1 "ping matrix"; see the agent section). The shared send
  machinery: a proactive message goes out with the localized italic
  `rtn_ping_header` line ("✨ Hey, it's {persona}", translations registry)
  above the generated text — an EVENT reaction merges its localized occasion
  phrase into that same line ("✨ Привет, это Ника! Спасибо за депозит 10 USD",
  the `rtn_trig_*` registry keys) — the header is chrome, only the model text is
  persisted (`db.persist_ping_turn`, assistant-only atomic variant) — a validated
  `[[LINK:url]]` site-map page rides under it as ONE inline button (and is
  recorded on the message row, `chat_messages.link_url`), every attempt
  lands in the `retention_pings` ledger (+ per-player counters via
  `db.record_retention_ping`), the `/stop` opt-out (`pings_muted`; `/resume`
  re-enables) and the blocked-bot flag (`unreachable`, set on a Telegram 403,
  cleared when the player writes again) are honoured on every send.
- **Delivery + gate knobs** (both in the hot `retention` settings group, edited in
  Retention → Settings → Parameters): `silent_notifications` (proactive sends go
  out with Telegram `disable_notification` — no sound on the player's phone;
  dialogue replies always notify normally; plumbed through
  `telegram_transport.send_*`/`retention._send_ai_text`/`_send_photo` and read in
  the agent's send site) and `subscription_cache_ttl_sec` (how long a positive
  `getChatMember` check is cached; 0 = re-check live every message — the old
  hardcoded 600s constant remains only as the fallback default).
- **Temporal naturalness at the send site (`retention.py`)**: a dialogue turn
  runs under a native Telegram **typing indicator** (`retention._typing` — a
  task re-sending `sendChatAction` every ~4.5s while the model thinks, so a
  long reasoning turn shows «печатает…» instead of dead silence; purely
  cosmetic, failures never drop the reply), and a model reply carrying BLANK
  lines is delivered as a **burst of separate messages**
  (`retention._split_reply_parts` in `_send_ai_text`, capped by the hot
  `retention.max_reply_parts` knob — default 3, 1 = never split —, extra
  chunks collapse into the last part; typing + a length-proportional pause
  between parts; an inline button always rides on the LAST part; photo
  captions are never split). The persona's RESPONSE STYLE core invites the
  split: usually one message, sometimes two, rarely three.
- **Telegram anti-spam gate** (`retention._handle_message`, mirrors the widget gate): per-user
  rate limit with its OWN chat-paced allowance — `antispam.check_rate_limit("tg:{pid}:{uid}",
  cfg["tg_rate_limit_max_per_user"])` (`antispam` group knob, env `TG_RATE_LIMIT_MAX_PER_USER`,
  default 60 per shared `window_sec`; the widget's per-IP 20/10min throttled a live human
  dialogue mid-flow — a real player's messages silently vanished). A block is no longer fully
  silent: the FIRST blocked message of a streak gets a localized in-persona `rtn_rate_limited`
  notice (in-memory `_rl_notified`, cleared when a message passes — one notice per window, so a
  hammering bot can't amplify into Telegram sends), and every gate drop logs a Railway line
  (`retention_rate_limited`/`retention_injection` WARNING; `retention_low_content`/
  `retention_need_deeplink`/`retention_subscription_gate` INFO) — the gates used to drop with
  no log line, making "my messages stopped arriving" undiagnosable from Railway logs. Then:
  an inbound ATTACHMENT (photo/video/file/voice/sticker — `ParsedUpdate.has_media`) gets a
  localized model-free "can't open those here, tell me in words" line
  (`rtn_incoming_media_reply`; the bot has no vision pass by design, and the model used to
  improvise "I don't see it - send again" loops), overlong input truncated (not rejected),
  low-content guard → localized model-free nudge
  (`rtn_low_content_reply`), injection scan → sampled audit + (with `injection_hard_block`) a
  model-free in-persona deflection (`rtn_injection_reply`). The other `antispam` settings
  knobs are shared with the widget. The **subscription check is cached** (positive results only, 10 min,
  `retention._sub_cache`; the explicit "I subscribed" button re-checks live with
  `use_cache=False`). `is_photo_request` matches stems at word START (regex `\b`), so "epic"
  can't bypass the photo cooldown. A photo turn never sends a bare image — an empty caption
  falls back to `rtn_photo_caption`.
- **Hand-off is a CHOICE message (`retention._send_handoff_choice`)**: on `[[HANDOFF]]` —
  regardless of the entry type — the bot sends **only** the structured choice message (bold
  `rtn_handoff_title` + `rtn_handoff_choice` body, HTML with a plain fallback). The model's own
  route-out line is **suppressed** (persisted to the transcript, not sent): it duplicated the
  choice card's intro, so the player used to see two messages. The card carries up to TWO
  url-buttons: the player's personal manager (`assign_round_robin_manager`, sticky; a pool/DB
  failure degrades gracefully instead of killing the hand-off) and **support on the site**
  (`retention._site_support_url(lang, product)`: the product's own `site_url` (its public main
  page — the dedicated Structure field) when set, else the per-language `contact_url`, else the
  site's MAIN PAGE derived as the origin of the first site-map entry — the widget lives on the
  site, so the origin is a safe landing. `site_url` is first on purpose: the "support on the
  site" button must land on the site, not a Telegram/contact link an operator set as
  `contact_url`). With only one destination configured it falls back to
  the matching single-option copy (`rtn_manager_intro` / `rtn_handoff_support` + button); with
  neither, the plain `rtn_handoff_support` line — a hand-off never dead-ends. The
  `retention_handoff` admin event records the offered target
  (`manager+site`/`manager`/`site`/`none`). Tests: `tests/test_retention_cta.py`.
- **Managers** (`retention_managers`): round-robin, **sticky** (a returning player keeps their
  manager); the hand-off is a `t.me/<username>` link; only the fact is logged
  (`retention_manager_handoff`).
- **Per-product Telegram config** lives on the `products` row: `telegram_bot_token_enc` /
  `player_api_key_enc` (secretbox-encrypted, like the OpenAI keys — `has_*` flags only out),
  `telegram_bot_username`, `telegram_webhook_secret` (non-secret webhook routing token, the
  Telegram analogue of `widget_key` — resolves an update to its product), `telegram_channel_id`,
  `telegram_channel_url`, `player_api_url`, `site_url` (public main-site URL / home page, edited in
  Structure; the hand-off's "support on the site" button lands here), `retention_enabled`. Webhook
  auth is two-layer: the
  routing token in the path + the deploy-wide `TELEGRAM_WEBHOOK_SECRET` in the
  `X-Telegram-Bot-Api-Secret-Token` header (NOT in the URL). **Update processing
  is hardened in `retention.handle_update`** (in-memory, single-instance Phase-1
  state like the rate-limit caches): a bounded per-product `update_id` dedup
  (Telegram redelivers when it thinks the webhook failed — without it the
  player got the whole turn, model reply included, twice) and a per-(product,
  player) asyncio lock that serializes turns in arrival order (updates run as
  BackgroundTasks, so two quick messages used to process concurrently — the
  second model turn didn't see the first in history and the replies
  interleaved). Tests: `tests/test_webhook_hardening.py`.
- **Retention analytics** (`db.retention_overview` / `retention_funnel` /
  `retention_timeseries`): the overview separates LIFETIME player-base numbers (`users` block:
  total/subscribed/muted/unreachable/avg stage) from RANGE activity (`range` block: active/new
  players, player messages, photos, handoffs, pings sent/failed, **ping reply rate** — a sent
  ping answered by a player message within 48h —, **telegram AI cost** `cost_usd` split by the
  AI log rows' own attribution labels into `cost_dialog_usd` + `cost_agent_usd` +
  `cost_photo_usd` + `cost_review_usd` (+ `cost_legacy_usd` for rows written before
  attribution shipped), so the whole Telegram spend the support dashboard excludes lands here
  AND each driver is nameable — the split used to be "has a session or not", which charged the
  proactive agent's and the AI judge's calls to photo metadata) plus a per-stage
  `stage_distribution`; the **funnel** (deeplinks → starts → linked → subscribed → engaged →
  photo receivers → handoffs) is backed by durable `retention_deeplink_created` /
  `retention_start` admin events (the nonce table is reaped on expiry, so it can never be the
  denominator); the **timeseries** is daily messages/actives/photos/pings/cost (cost also split
  the same four ways per day — the `TelegramCostCharts` panels). Endpoints
  `GET /admin/retention/overview|funnel|timeseries` take `from`/`to` + an OPTIONAL
  `product_id`/`partner_id` — omitted, they aggregate the caller's whole accessible scope
  (the global dashboard's retention block), following the support dashboard's
  `resolve_scope_filter` convention.
- **OUTCOME ATTRIBUTION (`outcomes.py`, `retention_outcomes`)** — the measured
  feedback loop: one row per DELIVERED touch (agent event reaction, idle ping,
  a photo/video actually sent — a caption-only fallback is NOT media —, or a
  dialogue reply carrying a CTA button), dimensions denormalized onto the row
  (event / rule_id / tone / photo_id + media_type / link_url / cost /
  decision_id), then a self-paced sweep from the worker tick fills
  `replied_at` + `reply_latency_sec` + `player_msgs` (the player's messages in
  ANY session of his Telegram identity — the idle rollover opens a new one, so
  keying on the session would lose the reply), `returned_at` and `deposit_at`
  from the canonical event feed, and `closed` once both deploy-constant windows
  elapse (`RETENTION_OUTCOME_REPLY_WINDOW_HOURS` / `..._CONVERSION_WINDOW_HOURS`
  — they define what the numbers MEAN, hence not per-product). Recording is
  best-effort by contract (`outcomes.record` swallows everything: analytics
  never breaks a send). Feeds four things: the agent's decision prompt +
  the ping writer's hint (`prompts._touch_history_block` /
  `_touch_feedback_hint` — Layer 3, so the cores stay byte-stable), the photo
  candidate ordering (`db._PHOTO_OUTCOME_SCORE_SQL`, smoothed reply rate as a
  tiebreak AFTER stage and freshness), the nudge's link hint
  (`db.top_links_by_outcome` → `prompts._proven_links_line`, a HINT the rotation
  rule still outranks), and the admin cuts
  (`GET /admin/retention/effectiveness`: summary with cost per reply/return/
  deposit + per media / per CTA page / per idle rung / per trigger, shown in
  Retention → Analytics, as a chip in the Media grid, as a column on the Idle
  rules table and as the «Result» column of the Decisions ledger). A deleted
  Telegram conversation purges the player's outcome rows with the rest of his
  footprint. Tests: `tests/test_outcomes.py`.
- **Admin**: the sidebar **Retention** section — one menu entry per surface, no
  page-wide tab strip: **How it works** (the section's landing page, an in-page
  2-tab strip: **Setup guide** — the checklist that replaced
  `RETENTION_SETUP.md` — and the **Algorithm map**,
  `admin/src/pages/RetentionAlgorithmMap.jsx` at `/retention?tab=algorithm` —
  an interactive, hand-rolled block diagram of the WHOLE algorithm as four
  flows (dialogue turn / casino data in / proactive agent / idle ladder):
  clicking a block expands a plain-language explanation, the settings
  governing exactly that step (deep-linked to their editors) and the
  implementing module; the legend chips highlight all blocks of one kind
  (gates / AI calls / sends / data). No diagram library on purpose — the
  bundle stays code-split. The content mirrors the shipped pipeline, so a
  pipeline change should update its block there), **Knowledge base** — the
  one-document text editor —, **Prompt** (Prompt preview + **Prompt variables**
  — the Telegram-persona editor, `GET/PUT /admin/retention/prompt-variables`;
  empty = the retention default — a SEPARATE prompt, no support inheritance,
  see "Prompt variables" — as an in-page 2-tab strip), **Media** — bulk upload
  + AI metadata + filters —, the **Proactive agent** page (its own route — see
  the "RETENTION AGENT" section; idle pings are a tab there), **Conversations**
  — the Telegram chat list + transcript dialog, see the lifecycle bullet above
  —, **Settings** (`/retention-settings`: Telegram config · Managers · the
  `retention` settings group as its Parameters tab; legacy
  `/settings?module=retention` and `/retention?tab=config|managers` links
  redirect there), and **Analytics**;
  API under `/admin/retention/*` (`app/api/retention.py`, guarded per
  product) + the `retention` group via the generic `/admin/settings/retention`. Retention copy
  (menu/gate/handoff strings, `rtn_*` keys) is in the translations registry (scope `retention`).
  **Prompt preview** (`GET /admin/retention/effective-prompt`, the SPA's Retention → Prompt
  preview tab) mirrors the support `GET /admin/effective-prompt`: the whole assembled retention
  prompt (retention Layer 1 + the KB document as Layer 2 in the system message; the Layer-3
  user message with the Test-sandbox player, an illustrative photo-candidate row and the
  guardrails), read-only, per product. It also returns the retention prompt variables
  (`prompts.RETENTION_PROMPT_VARIABLES` — raw override + retention default + resolved value per key);
  the SPA shows them read-only with a link to their ONE editor, the Retention → Prompt
  variables tab (no duplicate editor).
- **All existing invariants hold**: retention turns persist atomically as normal
  `chat_messages` + `ai_interaction_logs`, carry the session's `product_id`, use the product's own
  (encrypted) OpenAI keys with the same failover, and DB access stays behind `db.*` helpers.

### RETENTION AGENT — the event-driven proactive loop (`retention_v2.py`, `player_sync.py`)
The ONE proactive regime (the old v1 "ping matrix" — `retention_pings.py`, the
`retention_rules` CRUD, the Pings tab, `pings_enabled`, the starter rule ladder
— was removed; the historic **`v2_` prefix survives only in internal
identifiers** — settings keys, `/admin/retention/v2/*` endpoint paths, the
`retention_v2_*` tables/admin-event types and the module name — for stored-data
compatibility. Every user-visible surface says "agent"). Per-product switch:
`retention.v2_enabled` (hot, ships **ON**) — off means no EVENT reactions (the
dialogue bot still answers, and the idle ladder keeps running on its OWN
`idle_pings_enabled` switch — the two toggles are independent, matching what
the admin sees). `retention.v2_dry_run` ships **ON**: the
agent decides and logs but sends nothing until the owner flips it. The worker
starts from `main.py` lifespan under the `RETENTION_SCHEDULER_ENABLED` deploy
switch and wakes every **`retention.worker_interval_sec`** (hot, global-layer,
default 5s, clamped 5..3600 — read live each tick, so the cadence is tuned from
Settings without a redeploy; env default `RETENTION_WORKER_INTERVAL_SEC`), under
an advisory lock. **Event pickup is an atomic claim**
(`db.claim_retention_events`: UPDATE … FOR UPDATE SKIP LOCKED stamps
`processed_at` in the same statement that selects the batch) — the worker
sweep, the admin «Process queue now» button and a second instance can run
concurrently and an event still reaches the pipeline exactly once. This fixed
the duplicate-send bug where one `deposit_confirmed` produced two thank-you
messages: the old plain-SELECT + mark-after-processing let two concurrent
drainers pick up the same event.

- **Data sync is ONE module now (`player_sync.py`)** — the rewritten seam every
  piece of casino data enters through: the profile push webhook, the lazy
  Player-API pull (moved from `retention.py`; thin delegating wrappers +
  `is_safe_outbound_url` re-export keep the old names/tests working), the
  handshake snapshot, and the NEW canonical-event feed. **Events**:
  `POST /partner/{product_id}/event` (same partner-secret Bearer auth as
  player-update; single event or `{events:[…]}` batch ≤500), validated against
  the fixed taxonomy (`player_sync.CANONICAL_EVENTS`, 22 names:
  `deposit_confirmed`, `bet_settled`, `session_started`, `level_up`, …),
  idempotent by `(product_id, event_id)` (`retention_events`, append-only;
  duplicates counted, not stored). An event may carry an optional
  **`tg_user_id`** (top-level or in payload; validated + normalized into the
  payload — no extra column): the explicit Telegram recipient for when one
  `player_id` is linked to several Telegram accounts (multi-tester setups).
  Without it the v2 send resolves the player's most recently updated link
  (`db.get_retention_user_by_player`, `ORDER BY updated_at DESC`); with it the
  exact account is targeted, and an unknown target SKIPS with a ledgered
  reason — never a silent fallback to another account. The admin simulator
  exposes it as the «Telegram recipient» picker (fed by
  `GET /admin/retention/users`; picking an account also fills its player id)
  and the Decisions ledger shows the actual recipient's `@username` under the
  player name. Every stored event also bumps the **activity timestamps** the
  state resolver reads: `deposit_confirmed`→`last_deposit_at`,
  `session_started/ended`→`last_login_at`, `bet_settled`→`last_played_at`
  (forward-only via GREATEST — out-of-order delivery never rewinds a
  timestamp; a FUTURE partner timestamp is clamped to now at validation,
  since forward-only would otherwise pin the activity fields ahead of
  reality forever), plus profile-ish payload fields into the snapshot. The
  24h loss window (`db.player_net_loss_24h`) sums bets **per currency** and
  takes the worst bucket — a blind cross-currency sum compared apples with
  the USD-denominated `v2_loss_high_usd` threshold.
- **The pipeline** (`retention_v2._process_event`): event → deterministic
  **state resolver** (`resolve_player_state`: user_status / risk_state /
  lifecycle_stage + the 24h net-loss window from `bet_settled` payloads) →
  deterministic **guards** (`guard_check`: the per-player anti-annoyance state
  on `retention_users` — daily cap `ping_daily_cap` (default 3) / min gap
  `ping_min_gap_hours` (default 2h, 0 = off) / `/stop` /
  unreachable / subscription — plus the per-product **daily AI budget**
  (`v2_daily_budget_usd`, read from
  the decision ledger), the **same-event cooldown** (one reaction per event
  TYPE per player per window — the hot `v2_same_event_cooldown_hours` knob,
  default 5h, **0 = off**: the repeat-testing mode; it counts REAL reactions
  only, except `bet_settled`, where a SILENCE decision also latches the
  window — above the loss threshold every settled bet is decision-worthy, so
  without the latch a losing streak re-ran a paid decision call per bet), and
  the **loss comfort
  window** (`v2_loss_comfort_hours` after a loss signal or `v2_loss_high_usd`
  net loss in 24h: photo removed from the permitted actions, a hard comfort
  constraint injected)). **Quiet hours are NOT a guard**: the worker DEFERS
  claiming during the window (`run_product_events`), so a night-time deposit
  gets its warm note in the morning instead of being consumed as 'blocked' —
  in a casino the night is peak deposit time; the admin «Process queue now»
  button claims regardless. A **freshness cap** bounds the deferral
  (`retention_v2._MAX_REACTION_AGE_HOURS`, 24h on the event's own ts): older
  events — a days-long backlog after the agent was off — demote to state
  food, never a retroactive congratulation. Then → **agent decision** (one cheap strict-JSON call,
  `prompts.build_retention_v2_decision_messages`; urgency tactics banned,
  silence explicitly first-class; `parse_decision` clamps — anything malformed
  or non-permitted degrades to silence, the guard verdict always wins) →
  **send** via the normal persona stack (`chat_service.generate_retention_ping`
  with `occasion=`/`comfort=`: the `_RETENTION_V2_TOUCH_TASK` event-reaction
  wording + `_RETENTION_COMFORT_BLOCK`; delivery goes through the **outbound
  delivery seam `delivery.py`** — the ONE place a proactive message leaves the
  service, shared with the idle ladder: `channel_for_product()` returns the
  product's channel (today `TelegramChannel`; a future email/push/on-site
  channel plugs in there), whose `send_text`/`send_photo` own the
  `rtn_ping_header` chrome, HTML + plain fallback, 403 ⇒ unreachable and the
  photo caption-fallback tri-state — the senders never touch a transport
  client directly — and `db.record_retention_ping` bumps the
  per-player counters the guards read). The touch task demands the message
  NAME the occasion in natural words (never a vague congratulation, still
  never amounts); `retention_v2.occasion_for` folds whitelisted non-money
  payload details into it (`level_up`→level, `class_up`→class, bonus type,
  `deposit_failed` reason — `_OCCASION_DETAIL_KEYS`). **The trigger travels
  with the turn**: (1) the sent message ALWAYS opens with the persona header
  + a localized human occasion phrase merged onto ONE line
  (`retention_v2._proactive_header`: «✨ Привет, это Ника! Спасибо за депозит
  10 USD» — the `rtn_trig_<event>` translations keys, admin-editable per
  language; `{detail}` carries the safe payload detail and, in CHROME only,
  the amount; a comfort touch gets the bare header, photo sends prepend the
  line to the caption — the old raw «⚡ Trigger: …» line and its
  `v2_show_trigger` knob were removed); (2) the trigger +
  occasion are ALWAYS persisted on the message row
  (`chat_messages.ping_context`, via `db.persist_ping_turn`), so the prompt
  history renders the proactive turn with an inline "[you sent this
  PROACTIVELY - trigger: …]" note (`prompts._retention_history_content`, also
  in the returning-player continuity block) — the persona later KNOWS why it
  wrote and can answer «это ты о чем?» instead of deflecting — and the admin
  transcripts (Conversations + Retention) show a "⚡ proactive: …" marker on
  the turn. Every retention Layer 3 (dialogue
  turns and agent touches) carries a **CURRENT TIME block**
  (`prompts._current_time_directive`, fed with
  `retention.quiet_hours_utc_offset` — the audience clock the quiet hours
  already run on): local weekday + HH:MM + part of day, with a hard "match
  the clock or drop the time-of-day wording" rule — without it the model
  guessed («наслаждайся вечером» sent at 10:00). Tuning the offset knob
  (Retention → Settings) tunes both quiet hours and this block. Only decision-worthy events wake the
  agent — the set is `retention.v2_decision_events` (`None`/unset = the built-in
  `DECISION_EVENTS`, resolved via `retention_v2.effective_decision_events`;
  `bet_settled` stays special-cased: only when the loss window crosses
  `v2_loss_high_usd`, never toggleable). The set is deliberately NOT editable
  from the panel (the agent page's old Triggers tab was removed — the defaults
  are not meant to be tuned; an API consumer can still PUT the `retention`
  group). Everything else is state food, marked
  processed silently — no model call, no ledger row (the agent's guide tab
  explains exactly this, so "why is my event not in Decisions?" is self-serve).
  **Humanizing send delay:** an event is reacted to a per-event pseudo-random
  `v2_send_delay_min_sec`..`v2_send_delay_max_sec` (defaults 300/900 — 5–15
  min, ~10 avg) AFTER it arrived — an instant thank-you three seconds after a
  deposit reads as transaction surveillance. Implemented at CLAIM time
  (`db.claim_retention_events` skips events younger than their id-keyed
  delay), so it survives restarts and instances; the admin «Process queue now»
  button bypasses it (`ignore_send_delay=True`).
- **Idle re-engagement (`retention_idle.py`)** — the agent's INACTIVITY
  trigger: the admin-managed rules ladder in `retention_rules` («player quiet
  N days → Nika writes first»; triggers `bot_inactivity` /
  `casino_inactivity` / `no_deposit`, per-rule action message|photo, English
  `intent` hint (ensure_english-guarded), VIP-tier filter, per-player
  `cooldown_days`, priority). Swept from the SAME worker loop (called at the
  end of `run_product_events` — on its OWN `idle_pings_enabled` switch, so it
  runs even with the event agent off; self-paced per product by the hot
  `retention.idle_sweep_interval_sec` knob — default 600s), bounded by the
  SAME machinery: `db.eligible_ping_users` prefilters (subscribed / not muted
  / not unreachable / `ping_min_gap_hours` / `ping_daily_cap`), quiet hours,
  the daily AI budget, and **`v2_dry_run`** (a matched rule logs a
  `trigger_kind='idle'` ledger row and sends nothing). **Anti-cascade**
  (`_match_rule` + `db.idle_rule_thresholds_fired_since`): during ONE silence
  stretch only a rung ABOVE the highest already-fired one may fire (per
  trigger kind; the memory resets when the player writes again) — per-rule
  cooldowns alone let a 60-days-quiet player receive the ENTIRE ladder in
  reverse at min-gap pace. The same rung may re-fire after its own
  `cooldown_days`. A delivered idle ping
  persists via `db.persist_ping_turn` with an `idle_reengagement: …`
  ping_context, lands in BOTH ledgers (`retention_pings` with `rule_id` — the
  per-rule cooldown reads it — and `retention_v2_decisions`), and the message
  text comes from the normal persona ping stack (`_RETENTION_PING_TASK` idle
  wording + `rtn_ping_header`). Per-product master switch
  `retention.idle_pings_enabled` (hot, ships ON; env
  `RETENTION_IDLE_PINGS_ENABLED`); NEW products are seeded with the
  production-tuned 3–60-day starter ladder (`retention_idle.seed_starter_idle_rules`, called from
  `db.create_product`, only when the product has no rules). Admin: the
  **Idle pings tab of the Proactive agent page** (`/retention-agent?tab=idle`;
  the legacy `/retention?tab=idle` link redirects — rules
  CRUD, enable switches, a «Run now» test sweep that skips quiet hours/pacing,
  and the send ledger) over `GET/POST/PUT/DELETE /admin/retention/idle/rules*`,
  `GET /admin/retention/idle/ledger`, `POST /admin/retention/idle/run` — the
  run endpoint uses `run_product_idle_pings_locked` (the worker's advisory
  lock), so the button never races the sweep into double sends (the same
  guard-race class the v2 «Process queue now» button is locked against).
  Tests: `tests/test_naturalness.py`.
- **The decision ledger (`retention_v2_decisions`)** is the audit trail: ONE
  row per decision whatever the outcome — state snapshot, guard verdict +
  reasons, the agent's action/tone/intent/reason, dry-run flag, delivery,
  summed cost (decision + generation; each model call still lands in
  `ai_interaction_logs`, invariant §4, session-less like the photo-metadata
  calls). The daily budget reads this ledger.
- **Admin**: the sidebar **Proactive agent** page
  (`admin/src/pages/RetentionAgent.jsx`, route `/retention-agent` with a
  legacy `/retention-v2` alias, RequireProduct-gated) — status header
  (enabled/dry-run/budget/queue **plus the worker-liveness row**: the deploy
  scheduler switch + sweep interval and a DB-derived activity snapshot — last
  event / last processed / last decision / today's decision mix — via
  `db.retention_v2_activity`, correct across instances because it reads the
  durable tables, not an in-process heartbeat), the **event simulator**
  (inject any canonical event as `source='simulator'` — exercise the whole
  pipeline before the partner integration exists; **per-event sample
  payloads**, several variants each (`PAYLOAD_SAMPLES` in the page),
  auto-filled on event change with field names mirroring what the pipeline
  actually reads, plus a chip saying whether the picked event wakes the agent
  or is state food), «Process queue now», the event log and the decision
  ledger — both **deletable** for live-testing cleanup (row delete + «Clear
  all»): deleting an event nulls the ledger's `event_pk` links first (NB the
  event log feeds the state resolver, so deletes rewrite the loss window);
  deleting a decision "refunds" its cost from today's budget and re-arms the
  same-event cooldown (both read the ledger); every delete logs a
  `retention_v2_*` admin event. Two more tabs: **System log**
  (`GET /admin/retention/v2/logs` → `db.list_retention_v2_logs`: the durable
  `retention_v2_*` admin events, the admin-readable mirror of the Railway
  lines — the pipeline emits one structured line per decision
  (`retention_v2_decision`), per guard block (`retention_v2_guard_blocked`)
  and per failed send (`retention_v2_send_failed`)), and **How it works &
  testing** (the operator's guide: the pipeline, the on/off + dry-run + worker
  interval knobs, where persona/tone/KB/header/photos/language come from,
  which events wake the agent — fed live from `/v2/status`'s
  `decision_events` / `photo_events` split so the guide always matches the
  code —, the guard-reason → settings-knob table **with the product's CURRENT
  effective values** (from `/v2/status`'s `guards` block), a step-by-step
  testing checklist and the cost model). API:
  `/admin/retention/v2/status|events|decisions|logs|simulate-event|run` +
  the four DELETE routes (product-scoped via the admin_auth choke points).
  The agent knobs are normal `retention`-group settings (Retention → Settings
  → Parameters → «Proactive agent» + «Send-frequency guards» sections; the
  send-frequency guards — daily cap, min gap, same-event cooldown, quiet
  hours, budget, loss window — are THE dials for how often one player may be
  written to). Tests: `tests/test_retention_v2.py`.
