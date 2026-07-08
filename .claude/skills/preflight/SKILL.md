---
name: preflight
description: >-
  Verify the support-chat service before committing: install deps, run ruff, the
  cross-file invariant checks, and the full pytest suite. Use before any commit,
  after touching prompts/settings/translations/db, or whenever you want a clean
  green-light on the working tree.
---

# preflight

One command proves the working tree is safe to commit. It runs the same steps CI
runs, so a green preflight means a green PR.

## Run it

```bash
bash scripts/preflight.sh          # install (if needed) + ruff + invariants + tests
bash scripts/preflight.sh --checks # skip install; just ruff + invariants + tests
```

`scripts/preflight.sh` does four things, in order:

1. **Install deps** — runtime deps needed for import (fastapi, uvicorn, httpx,
   python-multipart) + dev tools (pytest, ruff). It deliberately does **not**
   install `openai`/`asyncpg`: `tests/conftest.py` stubs those, and the failover
   tests build openai error objects with the stub's lenient constructors.
2. **ruff** (`pyproject.toml`) — real-bug rules (pyflakes F, syntax E9). Line
   length and semicolons are intentionally off; don't "fix" them.
3. **Invariants** (`scripts/check_invariants.py`) — the cross-file rules from
   CLAUDE.md that break silently: every translations key has shipped English
   copy, the Layer-1 prompt core is byte-stable, and every writable settings
   group surfaces in the admin schema.
4. **pytest** — the full suite (~2.5s, 400+ tests).

## When something fails

- **ruff** → fix the flagged line (usually an unused import or undefined name).
- **invariants** → read the FAIL line; it names the exact key/group that drifted.
  See the `/add-translation` and `/add-setting` skills for the fix pattern.
- **pytest** → run the single file to iterate:
  `SUPPORT_CHAT_TEST_MODE=1 python -m pytest tests/test_x.py -q`.
  The byte-stable prompt test (`tests/test_prompt_cache_stable.py`) failing means
  per-request data leaked into `SYSTEM_CORE` / a Layer-1 static directive — move
  it to a Layer-3 (user-message) builder in `prompts.py`.

Do not commit with a red preflight unless the user explicitly accepts it.
