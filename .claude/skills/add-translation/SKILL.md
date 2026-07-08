---
name: add-translation
description: >-
  Add a new user-facing copy string (widget chrome, a server-generated turn, or
  retention/Telegram bot copy) to the translation registry so it is localized and
  admin-editable. Use whenever you introduce a player-visible string in the widget
  or a server-built reply. NOT for the model-facing prompt (English, in prompts.py)
  or the admin panel (English only).
---

# add-translation

Every string a player sees resolves through one registry: `translations.py`.
Adding a raw literal in `widget.js`/`chat_service.py`/etc. instead is the bug this
skill prevents. Resolution falls back to English, so the hard requirement is a
registered key with shipped **English** copy.

## 1. `translations.py` — register the key

Add a `(key, scope, description)` tuple to `KEYS`. Scope is:
- `widget` — rendered client-side (also served via `GET /api/chat/i18n`).
- `server` — used server-side when building a turn/payload.
- `retention` — the Telegram bot (menu/gate/handoff, `rtn_*` keys).

## 2. `translations.py` — ship the copy in `DEFAULTS`

Add the key under `"en"` (**required** — this is the ultimate fallback), then
ideally `"ru"`, `"es"`, `"tr"`, `"pt"`. Missing non-English entries resolve to
English by design, so they're optional, but fill them when you can. House style:
straight quotes only, no guillemets, no em dashes. Keep `{placeholders}` intact
(e.g. `{topic}`, `{persona}`, `{name}`, `{manager}`).

## 3. Use it

- Server: `translations.text("my_key", lang)`.
- Widget (`frontend/widget.js`): add the key to the baked-in `I18N` block (for the
  instant first paint) — `fetchI18n` then merges the server-resolved copy over it.
- Retention model-free chrome: resolve through the same `translations.text`.

## 4. Verify

`SUPPORT_CHAT_TEST_MODE=1 python scripts/check_invariants.py` must still print
`translations (… keys, English copy complete)` — it fails if a `KEYS` entry has
no `DEFAULTS['en']` copy, or if there's dead copy in `DEFAULTS` not in `KEYS`.
Then `bash scripts/preflight.sh --checks` (the translations test lives in
`tests/test_translations.py`).

The `contact_url` key is the one documented English-only exception (the escalation
button URL, http(s)-validated, empty = no button); it's exempt from the orphan
check.
