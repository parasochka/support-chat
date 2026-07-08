---
name: add-setting
description: >-
  Add a new runtime-tunable settings knob to the support-chat service, wired
  end-to-end across config.py, settings.py, the admin schema, and tests. Use when
  the user wants a new operational knob (rate limit, timeout, cap, toggle, model
  tuning, retention pacing) that admins can change without a redeploy. NOT for
  brand copy (that is prompt variables / translations) or the prompt wording
  (that lives in prompts.py).
---

# add-setting

A settings knob resolves through `product_settings > app_settings > env >
default`. To add one, touch these files in order. Pick the group it belongs to:
`antispam`, `model`, `general`, `retention`, `language`, or `escalation`
(`SETTING_KEYS` in `settings.py`). Anything with no better home → `general`.

## 1. `config.py` — the env-backed default

Add a module constant using the right `_env_*` helper (they read at import so
tests can monkeypatch):

```python
MY_KNOB: int = _env_int("MY_KNOB", 30)   # or _env_float / _env_bool / _env(...)
```

## 2. `settings.py` — the getter and the validator

- In the group getter (e.g. `antispam()`, `general()`, `retention()`), add a
  field that reads the DB override with the config default as fallback:
  ```python
  "my_knob": db_v.get("my_knob", config.MY_KNOB),
  ```
- In `validate_setting()`, under the matching `elif key == "<group>":` branch,
  validate it with the right bound helper: `_require_int(value, "my_knob", lo, hi)`
  / `_require_float` / `_require_bool` / `_require_choice` / `_require_str_list`.

## 3. Read it where it's used

Read via the getter, never `config` directly, so per-product + hot-reload work:
`settings.antispam()["my_knob"]`. If it's a `model` knob bound at client build
time (timeout/concurrency), remember the admin write already calls
`openai_client.reset()`.

## 4. `admin/src/pages/settingsSchema.js` — make it visible in the panel

Add a field to the group's array so the typed editor renders it (skip this only
for `escalation`, edited on the Prompt page, and `language`, its own editor):

```js
{ name: 'my_knob', label: 'My knob', type: 'int', help: '…', min: 1, max: 100 },
```
`type` ∈ `int | float | bool | string | select | intlist | strlist | intmap`.
The invariant check only enforces the group exists; add the field so operators
can actually see the knob.

## 5. Tests

Add/extend a test in `tests/test_settings_override.py` (or the group's own test)
asserting: the default resolves from env, a DB override wins, and an out-of-bounds
value is rejected by `validate_setting`.

## Verify

`bash scripts/preflight.sh --checks` — ruff + invariants + full suite green.
Update `README.md`'s env-vars table if the knob has a new `MY_KNOB` env var, and
note the knob in `CLAUDE.md` if it changes documented behaviour.
