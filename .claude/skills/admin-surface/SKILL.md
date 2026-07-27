---
name: admin-surface
description: >-
  Map of the admin/management surface of this service: admin auth and the roles
  and memberships model (app/api/admin_auth.py, auth.py), user management, the
  hot-reloaded settings groups and their global-only fields, the dashboard data
  API and its support-only aggregation rules, KB and KB-variable editing, the
  read-only effective-prompt preview, the translations tab, the test player
  profile, the escalation contact payload, the signed handshake, the Logs and
  audit views, the MCP server facade (mcp_server/), and the React Admin SPA in
  admin/ with its sidebar information architecture. Use when adding or changing
  an /admin/* endpoint, touching admin authorization or scoping, editing the
  admin SPA, or wiring a new admin-editable knob into the panel. NOT for the
  public chat API or the Telegram retention bot.
---

# Admin / management surface

This is the lazily-loaded companion to the root `CLAUDE.md`. The shared
architecture (multi-tenancy, settings resolution, prompt layering) and the
numbered invariants live there and still apply - authorization decisions in
particular must go through the `app/api/admin_auth.py` choke points.

## Admin / management

Map of what lives where:

- **Admin auth + roles** (`app/api/admin_auth.py`, `auth.py`): `POST /admin/login` **requires `email`
  + password** — every admin signs in as a named `admin_users` account. The password is checked
  against the salted **PBKDF2-HMAC-SHA256** hash in `admin_users`
  (`auth.hash_password`/`verify_password`, stdlib only) → the user's stored role; a missing email
  is a 400, bad credentials a non-enumerating 401. Login is rate-limited and logs
  `admin_login_failed`. The token is signed with `ADMIN_JWT_SECRET` and carries `role` + `email`.
  **`ADMIN_JWT_SECRET` signs admin sessions — set a distinct strong value in prod** (it falls back
  to `SESSION_JWT_SECRET`, flagged at startup). **There is no password-only owner login and no
  `ADMIN_PASSWORD` env var** (both removed); the legacy `owner` role is gone. **Two roles:**
  `admin` may write; `manager` is **read-only**. The dashboard is no longer gated by an env switch
  — it is always mounted and protected by named-user login (an empty `admin_users` means nobody can
  log in, so there is **no bootstrap path** — seed the first account against a live DB).
  `require_admin` guards every `/admin/*` route: it verifies the JWT **and re-checks the named
  account against `admin_users` on every request** (a JWT has no revocation, so without this a
  deactivated/deleted admin kept full access until token expiry — up to `ADMIN_TOKEN_TTL_MIN`);
  the DB `role` is authoritative over the token's role claim, so a demotion applies immediately,
  and a token without an `email` claim is rejected. **`require_admin_write`** (role in
  `WRITE_ROLES = ("admin",)`, else **403**) guards every mutating route (KB, settings, variables,
  test profile, user management); mutating writes record `updated_by` as the account **email**
  (falling back to the role for safety). PBKDF2 verify/hash run in `asyncio.to_thread` so the
  ~100ms CPU burn never blocks the event loop. `GET /admin/me` returns the caller's role/email so
  the SPA can role-gate its UI (managers lose the Settings / Users tabs and all edit controls —
  cosmetic; the server is authoritative).
- **User management** (`app/api/admin.py` `/admin/users*`, the **Users** tab, admins only):
  CRUD over `admin_users` (email + password) **plus the membership editor** — WHAT an account
  may touch is its `admin_memberships` (role `admin`/`manager` × scope global/partner/product).
  The SPA create form picks the initial role × scope (partner/product pickers fed by
  `GET /admin/structure`; the create body carries `scope_type`/`partner_id`/`product_id` —
  omitted, the backend defaults to a GLOBAL membership); the edit form hosts the
  **Access (role × scope)** panel — the memberships table with grant/revoke over
  `POST/DELETE /admin/users/{email}/memberships`. Granting the same scope again replaces its
  role (`db.add_membership` upserts). The caller may grant/revoke only scopes it holds an
  ADMIN role over (`_require_scope_admin`), may not change its OWN memberships, and manages
  only accounts whose ENTIRE membership set lies inside its reach (`_can_manage_user`; an
  account with NO memberships is manageable only globally). The SPA edit form deliberately has
  **no flat role field**: the legacy `PUT /users/{email}` `role` writes a GLOBAL membership
  and requires global write, so role changes go through the memberships panel. No email
  delivery, no reset flows — an admin sets passwords directly. A user can't
  demote/deactivate/delete **itself** (self-lockout guard). With no owner recovery path,
  **keep at least two `admin` accounts** so a forgotten password can't lock everyone out. The
  password hash never leaves `db.py` (`_row_to_admin_user` drops it).
- **Settings** (`settings.py`, `app_settings` table): hot-reloaded runtime tuning with
  precedence `app_settings` (DB) → env → default. A sync in-process cache (populated at
  startup, reloaded on write, and **re-pulled every 60s** by `main._settings_refresh_loop`
  so a write made by another instance — or directly in the DB — applies without a restart)
  is read by `antispam`/`escalation`/`openai_client`/`language`/
  `auth`/api; writes validate hard and log `setting_updated`. **GLOBAL-ONLY fields**
  (`settings.GLOBAL_ONLY_FIELDS`: `retention.worker_interval_sec`,
  `general.admin_token_ttl_min`, `general.body_max_bytes`) are read by deploy-wide
  machinery that runs OUTSIDE any product scope (the agent worker loop, admin-token
  minting, the body-cap middleware), so a product-layer override of them can never
  apply — `_group()` ignores them on the product layer, `PUT /admin/settings/{key}`
  strips them from product-layer saves (self-healing older stored junk on the next
  save), and the SPA locks the field with a "switch to All products" hint when a
  product is selected. This fixed «I changed the worker interval on a product and
  nothing happened». Groups: `escalation`
  (`high_risk_keywords`, `human_request_keywords` — content tuning, so its ONLY editor is the
  Common → Escalation keywords page; the Settings tab skips this group to avoid a duplicate
  editor. `max_messages_per_session` moved to `general`; a legacy `escalation` override is still
  read as a fallback),
  `language` (default + supported
  set **+ `names`** — custom display names for languages added beyond the built-in
  `language.LANG_NAMES`; every language read goes through `language.default_code()`/
  `supported_codes()`/`all_language_names()`. Adding a language is ISO-validated: the admin
  Language tab picks from `language.ISO_639_1` (the full ISO 639-1 catalogue), so a new
  language only enters with a correct code + name, and `settings.validate_setting` rejects any
  supported/`names` code not in that catalogue. `GET /admin/meta` exposes `languages`
  (selectable catalogue), `supported`, `default_language`, and `iso_catalog` for the picker),
  `antispam` (rate limit/window/cooldown/input cap **plus**
  `injection_hard_block`, and the low-content guard `low_content_block` /
  `min_meaningful_chars`), `model` (OpenAI tuning — see the failover section), and `general`
  (technical operational knobs with no other home: `session_ttl_hours`, `admin_token_ttl_min`
  — the admin login lifetime, env `ADMIN_TOKEN_TTL_MIN` as default —, `max_messages_per_session`,
  `history_max_turns` — how many recent turns feed the model's prompt history, env
  `HISTORY_MAX_TURNS`/20 default; the full transcript is always persisted —, and
  `body_max_bytes`. `contact_form_url` is a dead legacy field: a value stored by old builds is
  auto-migrated on boot into the default product's Translations (`db._migrate_legacy_contact_url`)
  and deleted; `settings.general()` still resolves the key, but only the `CONTACT_FORM_URL` env
  default can feed it now — used solely as the default product's contact-button fallback). Three more app_settings keys live OUTSIDE `SETTING_KEYS` (each with its
  own admin endpoint, so they never appear in the generic Settings editor): `test_profile`,
  `prompt_variables` and `translations`. **The prompt WORDING is NOT a settings group** — it
  lives in `prompts.py` (the single source of truth), not `app_settings`; only the
  prompt-variable VALUES are stored. The goal is that every non-secret *operational*
  knob lives in the admin panel and only true secrets (API keys, JWT secrets, `DATABASE_URL`,
  handshake/Turnstile secrets) — plus the network-perimeter deploy
  vars (`CORS_ALLOW_ORIGINS`, `TRUSTED_PROXY_COUNT`) — stay in Railway env. There is no seed:
  an empty `app_settings` resolves through env → default, and the owner's first write to a
  group persists that override in the DB.
- **Dashboard data API** (`app/api/admin.py` + `db.py` aggregation + `metrics.py` derived
  rates): overview/timeseries/by-topic/by-language/sessions/session/unresolved.
  `resolution_rate` is a documented PROXY (counts "not escalated", incl. abandoned →
  `sessions_open` tracked separately). **The support dashboard is SUPPORT-only: every
  aggregate excludes `consumer='telegram'`** so retention/Telegram spend and sessions
  never inflate it. Session counts filter `consumer <> 'telegram'`; the cost aggregates
  (`overview_aggregates`, `timeseries` `cost`/`cost_per_session`) **join `chat_sessions`**
  so they count only non-telegram turns AND drop the `session_id IS NULL` photo-metadata
  vision calls (those are retention); `by_topic`/`by_language` add the same exclusion. The
  Telegram module has its own home — `retention_overview`/`retention_timeseries` — whose
  cost is scoped on the LOG row's product so it INCLUDES the session-less photo-metadata
  calls, and is split into `cost_dialog_usd` (engagement turns) + `cost_photo_usd`
  (photo-metadata generation), summing to `cost_usd`. The SPA renders that split as the two
  **Telegram cost** panels (`components/charts.jsx` `TelegramCostCharts`: total-over-time +
  cost-by-source stacked bars), shown on both the dashboard Retention block and Retention →
  Analytics. The overview also carries AI-API health:
  `avg_latency_ms` (mean end-to-end latency of the SUCCESSFUL OpenAI calls — failures
  carry no meaningful latency, so they are excluded from the average), `ai_calls_total`
  and `failed_calls` (from `ai_interaction_logs`). The SPA renders the KPI tiles as two
  rows of six, grouped by meaning (sessions/engagement, then AI/cost/performance). **Cost**
  is surfaced per row: `by-topic`, `by-language`,
  and `sessions` each carry a `cost_usd_total` (summed from `ai_interaction_logs` via a join/CTE)
  rendered in the SPA tables. **Date ranges** are half-open and a date-only `to=YYYY-MM-DD` is
  made **inclusive** of that whole day (`app.api.admin._range` adds one day), so "today" isn't dropped.
  **Every admin report keys `lang` on the CONVERSATION language, not the browser
  locale** (`db._CONV_LANG_SQL` = `COALESCE(s.conv_lang, s.lang)`, shared by
  `by_language`, `list_sessions` — including its `lang` filter — and
  `unresolved_by_topic`). `chat_sessions.lang` is the browser locale fixed at session
  create and never overwritten, so grouping on it reported the BROWSER mix: a player
  on an en-US browser writing Russian was counted as English. The browser locale still
  travels alongside as `ui_lang` (and `conv_lang` on the session detail), and the SPA
  shows it in the conversation summary only when the player drifted away from it.
  The **Unresolved** queue lists engaged sessions that still need attention — both `escalated` and
  abandoned `open` chats with ≥1 user turn (resolved excluded), grouped by topic. It carries the
  **same per-session fields as the Sessions tab** (created, lang, status, msgs, cost) + the first
  message, so a triager can scan and pick (`db.unresolved_by_topic` joins lang + cost; CSV export
  mirrors them). **Timestamps render in the viewer's local timezone** — the API returns tz-aware
  ISO strings and the SPA formats them client-side via `fmtDateTime`/`toLocaleString` (a UTC `06:00`
  shows as `09:00` for a UTC+3 admin), so the dashboard always reads in the operator's own time.
- **KB Variables sub-tab** (`app/api/admin.py` `/admin/kb/variables`, the **Knowledge base →
  Variables** sub-view in the SPA): list + edit the admin-managed `{placeholder}` registry (see
  "KB variables" above). Read returns `updated_at` as an isoformat string so `JSONResponse` can
  serialize it.
- **The prompt WORDING is the file `prompts.py` (single source of truth, a dry template, NOT
  editable from admin).** The Layer-1 core (`SYSTEM_CORE` — Nika's tone-of-voice + the
  absolute/escalation/responsible-gaming/links rules), the STATIC Layer-1 directives (greeting,
  formatting, KB-grounding, escalation restraint, suggested questions, finish-chat, lead-forward),
  the DYNAMIC Layer-3 directives (language, personalization, topic routing) + the recency
  guardrails, and the forbidden-topics list/refusal are constants in that file. To change the
  wording you edit `prompts.py` and redeploy — there is no admin editor, no `prompt_versions`
  table, no A/B split, no `system_prompt`/`layer3_prompt` settings group. (This replaced an
  earlier design where the core was versioned in the DB and edited from the panel; it was removed
  so there's exactly one place the prompt comes from.) The brand-specific VALUES the template
  renders with ARE admin-editable — see "Prompt variables" above and the **Prompt → Prompt
  variables** sub-tab (`GET/PUT /admin/prompt-variables`), which also hosts the escalation
  keyword lists (over the `escalation` settings group) and the test player profile blocks.
  **Read-only effective-prompt view** (`app.api.admin._build_effective_preview` +
  `GET /admin/effective-prompt`, the **Prompt → Preview** sub-tab in the SPA): so the owner can
  always SEE the whole assembled prompt, this endpoint reuses `prompts.build_messages` with a
  sample player + a sample specialized topic's KB and returns the complete prompt split into the
  system message (Layer 1 core + static directives + Layer 2 KB) and the user message (the
  dynamic Layer-3 directives + player context + recency guardrails), prompt variables already
  substituted. The SPA renders it as read-only blocks. It is resilient — if topics/KB can't load
  it still renders Layer 1 + the Layer-3 block, never breaking the page. (Layer 2, the per-topic
  KB, is the one prompt input still edited in the admin — in the Knowledge-base tab — because
  it's answer content, not instructions.)
- **Translations tab** (`translations.py`, `app/api/admin.py` `GET/PUT /admin/translations`, public
  `GET /api/chat/i18n`): per-language editing of every user-facing widget string — chrome copy,
  server-generated service replies, the per-language escalation contact-button URL (the
  `contact_url` key, http(s)-validated; empty = no button link — only the default product
  falls back to the `CONTACT_FORM_URL` env default), and the
  per-language topic titles (via the existing
  `POST /admin/kb/topics` upsert). See "Translations" above. The SPA renders the registry in
  FOUR fixed blocks (`Translations.jsx` `SECTIONS`, keyed on scope + the client-side
  `SERVICE_KEYS` list): the general widget interface, the support bot's messages to the player,
  the Telegram retention bot's messages, and the service/error notices — so the owner tunes the
  bots' actual voice without wading through technical fallbacks. A new registry key lands in a
  bot-messages block automatically unless it is added to `SERVICE_KEYS` (do that for any new
  error/guard nudge). The admin panel itself stays English.
- **KB editing** (`db.*` helpers, `app/api/admin.py` `/admin/kb/*`): **one KB text per topic**,
  single-language. `GET /admin/kb/content?topic_id=` reads it, `PUT /admin/kb/content` sets it
  (updates the topic's active entry in place, or inserts one), `DELETE /admin/kb/content?topic_id=`
  soft-clears it (`active=false`). No versioning, no per-language entries — the Layer-3 language
  directive still makes the model answer in the player's language regardless of the KB language.
- **Escalation** (`escalation.build_payload`): returns the localized contact-button payload
  (copy AND the per-language button URL from the translations registry). No ticket snapshot,
  no Telegram notifier — the hand-off is the contact button only.
- **Signed handshake** (`auth.sign_handshake`/`verify_handshake`, `app/api/chat.create_session`):
  with `WIDGET_HANDSHAKE_SECRET` set, only a valid signed blob is trusted for
  `user_context`; raw browser context is ignored. No secret ⇒ dev behaviour. The
  injection sanitizer runs in every mode.
- **Test player profile** (`settings.test_profile`/`validate_test_profile`,
  `app_settings['test_profile']`, `app.api.admin` `GET/PUT /admin/test-profile`, the **Common →
  Test player profile** page — the old Test sandbox tab, then a block on Prompt variables,
  now its own page in the shared Common section):
  in test/dev (**no** `WIDGET_HANDSHAKE_SECRET`) there is no host
  site to sign a handshake, so this stored profile stands in for it at `create_session`. It
  drives the Layer-3 player data the model sees (`id, full_name, email, activation_status,
  country, balance, vip_level, registration_date` — the `prompts._CONTEXT_FIELDS` whitelist) so
  the owner can test name personalization. There are **no** language knobs — the session
  language always follows the browser. `enabled=false` ⇒ fall back to the widget's built-in
  context. The profile is **ignored** when a handshake secret is set (the host site is
  authoritative then). This is the single seam for "manage the test player on test, the real
  site supplies it later".
- **Admin SPA** (`admin/` at the repo root): a React Admin (marmelab) + Vite app.
  The two-stage Dockerfile builds it (node stage → `admin/dist`) and `main.py`
  serves it at `/admin` (hash router; hashed assets under `/admin/assets`,
  vite `base: '/admin/'`) — same origin as the `/admin/*` JSON API, so the
  admin needs no CORS and no `VITE_API_URL` (relative URLs). The old
  hand-rolled SPA (`frontend/admin/`, `/admin-static`) was removed. The custom
  dataProvider (`admin/src/dataProvider.js`) maps react-admin resources onto
  the real endpoints; auth is `POST /admin/login` → Bearer JWT; the header
  carries the Partner → Product switcher (selection in localStorage, sent as
  `product_id`/`partner_id` query params). Local dev: `npm run dev` in
  `admin/` (set `VITE_API_URL` + allow the dev origin in
  `CORS_ALLOW_ORIGINS`); a separate static deploy also still works.
  **Sidebar** (`App.jsx`): three collapsible sections (Support chat / Telegram ·
  Retention / System) whose open state persists in localStorage; the Retention
  sub-tabs are exposed as sub-menu entries that deep-link `/retention?tab=…`
  (the page reads `?tab=`, like the Prompt page). **Product-scoped surfaces are
  gated** by `components/RequireProduct` — KB, KB variables, Prompt, Translations,
  Retention and the Conversations / Unresolved lists (incl. the conversation
  detail view) refuse to render without a concrete product selected in the header
  (otherwise they'd silently edit/show the default product's data), showing a
  "select a product" notice instead; this applies to admins and managers alike.
  Dashboard, Structure and Users stay usable at the all/partner scope. **Settings** (`pages/Settings.jsx` + `settingsSchema.js`) is a
  typed, tabbed editor (one tab per group + a Languages tab with an ISO-picker
  add-language / default / custom-name editor) — not a raw-JSON textarea — with a
  scope banner (global defaults vs the selected product). **Settings are split into
  three MODULE surfaces**: Support chat → Chat settings (`?module=support` — widget
  anti-spam + chat limits) and System → Settings (`?module=core` — model, languages,
  technical limits) on the standalone Settings page, plus the retention module (the
  whole `retention` group + the Telegram rate-limit slice of `antispam`) embedded as
  the **Parameters tab of Retention → Settings** (`/retention-settings?tab=params`;
  the exported `SettingsModule` component — legacy `?module=retention` links
  redirect there).
  The split is presentation-only — schema fields carry a `module` tag
  (`settingsSchema.js` `GROUP_MODULE`/`fieldsForModule`) and a group is still
  SAVED whole (the form round-trips unseen fields unchanged). Each module page
  opens with a plain-language "How it works" accordion (an intro + concrete
  bullet points linking to the deeper guide pages); long field explanations
  render as an (i) tooltip instead of a helper line. **Operator guides**: the
  Support chat sidebar opens with a full "How it works" page
  (`pages/SupportGuide.jsx`, route `/support-guide`) — the support twin of the
  Proactive agent's "How it works & testing" tab: the message pipeline, the
  content map ("where do I fix this text?"), topic routing, escalation, the
  testing checklist and costs. **The admin chrome is
  bilingual (EN/RU)**: `src/i18n.js` is a gettext-style dictionary keyed by the
  English source strings, `t()` wraps render sites, and the AppBar carries an
  EN/RU toggle (persisted in localStorage; switching reloads). Long guide/help
  prose with inline markup stays ONE dictionary string via `components/Rich.jsx`
  (`rich(t('…'))` renders a tiny subset: backtick code spans, `**bold**`,
  `[label](url)` links) — EVERY user-visible admin string routes through `t()`,
  including the guide pages, tables, confirms and toasts. Only the chrome is
  translated — the CONTENT stays English (see the English-only guard below).
  **Bundle is code-split**: pages load via `React.lazy` (per-page chunks) and
  vite `manualChunks` splits recharts/mui/react-admin/vendor, so the entry chunk
  is ~55 KB instead of a 1.5 MB monolith. Sidebar custom entries all render
  through ONE `SubItem` component with an EXACT pathname matcher (an earlier
  `startsWith('/retention')` bug lit up "Telegram config" while the
  /retention-agent page was open, and the differently-rendered Menu.Item sat out
  of line with its ListItemButton siblings). **Token/cost counters**: prompt and
  KB editors (support KB, retention KB, both prompt-variable editors, both
  prompt previews) render a live `TextStats` line — characters, estimated
  tokens, and the uncached-input cost for the CURRENT model, priced from
  `GET /admin/meta`'s `model_pricing` block (`openai_client.pricing_for_model`). **Topic titles are
  single-sourced** in Translations → Topic names; the KB form keeps only the
  canonical English title (the prompt is English-only) and links there. **SET-state
  is explicit**: `components/SetBadge` shows a green check for configured secrets
  and `components/SecretField` adds a Clear button so an operator can save an empty
  value (fall back to env) — used in Structure + Retention config; the test-profile
  handshake notice links to Structure to clear the product's handshake secret.

- **Account page + slim header** (`admin/src/pages/Account.jsx`, `/account`,
  opened from the AppBar user menu): shows the caller's email, active status,
  role and access groups (memberships) + registration date (from `GET
  /admin/me`, which now returns `created_at`/`active`). The **light/dark theme
  toggle and the EN/RU admin-language switch moved here** from the AppBar
  (`useTheme()` / `setAdminLang`), so the header carries only the product
  switcher + refresh + user menu. The user menu is a custom right-anchored
  `Menu` (react-admin's default popover opened off-screen in this RA version);
  react-admin's built-in LocalesMenuButton is suppressed (`i18nProvider.getLocales
  = () => []`) so it isn't a redundant second language control.
- **Observability — System → Logs** (`admin/src/pages/Logs.jsx`, `/logs`,
  admin-only, red unread badge in the sidebar). Two tabs:
  - **System logs** = the app's own runtime logs (the "Railway logs") mirrored
    in-app. `logcapture.py` attaches a buffer handler to the **ROOT** logger with
    a denylist filter (framework noise — uvicorn access log, httpx, asyncpg, … —
    is dropped): every app module logs via `getLogger(__name__)` (sibling loggers
    under root, NOT descendants of the service logger), so attaching only to
    `config.SERVICE_NAME` captured just main.py/health.py and dropped every
    escalation / failover / retention decision / model error the view promises.
    The logging hot path only appends to an in-memory deque (thread-safe, no DB,
    no recursion); a background flush loop in `main.py` (`_log_flush_loop`)
    batch-inserts into the bounded `app_logs` table (`db.insert_app_logs`) and
    prunes to the newest 5000 (`db.prune_app_logs`). `GET /admin/logs`
    (level/text filters), `GET /admin/logs/unread` (WARNING+ since the caller's
    per-admin marker in `app_log_reads`), `POST /admin/logs/read`. **GLOBAL-scope
    only** (`_require_global_viewer` → `admin_auth.global_role`): `app_logs` is one
    deploy-wide table with no `product_id`, so a product/partner-scoped admin must
    NOT read other tenants' operational data — a global manager (read-only
    hub-wide) may, product/partner admins 403. The sidebar badge polls the unread
    count.
  - **Activity (audit)** = who changed what. An audit middleware in `main.py`
    (`audit_admin_actions`) writes one `admin_audit_log` row per SUCCESSFUL
    mutating `/admin/*` request: the actor (stashed on `request.state` by
    `require_admin`), a friendly action label, the product/partner scope (from
    `?product_id=` or a `/products/{id}` path) and time. Best-effort — never
    affects the response. `GET /admin/audit` applies **tiered visibility**
    (`db.list_audit`): SCOPE (products within the viewer's reach / the selected
    product/partner) × ROLE (a manager sees only manager-authored actions, an
    admin sees everything in reach); hub-global actions (user mgmt, system
    settings) show only to a global viewer. NB only admins can mutate today, so
    audit actors are admins — the manager/admin split is future-proofing.
- **MCP — the agent-facing facade of the admin API** (`mcp_server/`, the
  **System → MCP** page). A Model Context Protocol server (stdio, newline-
  delimited JSON-RPC 2.0) that lets an AI agent read the logs, inspect the
  assembled prompt of any product, walk conversations + the retention agent's
  decisions, and — with an `admin`-role key — edit KB, prompt variables,
  translations, site map and settings. It is a **standalone CLIENT**: it holds a
  service key (`sak_…`), calls the same `/admin/*` endpoints the SPA does over
  HTTPS, and imports NOTHING from the service (no `db`, no `config`, no
  `settings`) — so every authorization decision stays at the `require_admin`
  choke point and a write is audited as `apikey:<name>` like any other machine
  caller. Hand-rolled on stdlib + `httpx` (the `auth.py` JWT precedent): no new
  runtime dependency, and the protocol layer is a pure dict-in/dict-out
  `MCPServer.handle()` that pytest drives without a subprocess
  (`tests/test_mcp_server.py`). Structure: `catalog.py` is the ONE declarative
  tool table (name, endpoint, params + where each rides — query/path/body/local),
  `client.py` the HTTP seam (it turns 401/403/404/422 into an actionable
  sentence), `server.py` the dispatcher. **The catalogue is curated, not
  generated**: ~90 admin routes would cost more context than they are worth, so
  ~20 task-shaped tools cover the real work plus ONE `admin_get` escape hatch,
  bounded to paths under `/admin/` (an admin credential must not be pointable at
  the public chat API). **Destructive and credential surfaces are absent by
  construction** — no DELETE anywhere in the catalogue, nothing under
  `/users`, `/api-keys`, `/secrets` or `widget-key` (a test pins this). Player
  identity (`email`, `full_name`, `tg_username`, …) is masked in the payloads of
  transcript/session tools unless `redact_pii=false`; message TEXT is never
  touched (it is the thing being debugged). Env contract:
  `SUPPORT_ADMIN_URL`/`_KEY` (required), `_PRODUCT_ID` (default product for
  tools called without one), `_ALLOW_WRITES=0` (write tools are not merely
  refused — they are never listed, so the model spends no context on them),
  `_REDACT_PII`, `_MAX_RESPONSE_CHARS`, `_TIMEOUT_SEC`. `GET
  /admin/mcp/manifest` re-exports `catalog.manifest()` so the System → MCP page
  (key minting + copyable `.mcp.json` / `claude mcp add`) renders the tool list
  from the code and can never drift; the page is global-scope only, like Logs.
  Adding a tool = one `Tool(...)` row in `catalog.py` (the page and the manifest
  follow automatically).
- **Sidebar IA — flat sections, one entry per surface** (`admin/src/App.jsx`):
  four collapsible sections and NO page-wide tab strips (the earlier cascading
  hubs — Support's Content entry with its `RouteTabs` strip and the retention
  page's top section strip — were flattened; `contentTabs.js` is gone). **Support
  chat**: How it works · Conversations · Escalations · Knowledge base (with its
  KB ↔ Variables sub-strip) · Prompt (Preview/Variables in-page tabs) · Chat
  settings · Analytics. **Common** — the cross-module surfaces shared by BOTH
  bots: Translations · Site map · Escalation keywords · Test player profile.
  **Retention**: How it works · Knowledge base · Prompt · Media · Proactive
  agent (events/decisions/idle pings/logs/guide tabs) · Conversations · Settings
  (`/retention-settings`) · Analytics. **System**: Structure · Settings · Logs ·
  Users · API keys · MCP. All sidebar entries share one 40px icon column (RA's
  MenuItemLink width) so labels align.

§16 decisions: unresolved analysis = topic-grouped (no embeddings); contact form =
host-site button only; admin auth = named `admin_users` accounts only (email + password,
role-driven; no password-only owner login).
