---
name: docs-check
description: >-
  Reconcile the repo's docs with the current change. Use before committing/opening
  a PR, or after an architectural or public-contract change, to catch a CLAUDE.md /
  README.md / integration-*.html / test.html update that the code change implies.
  The repo does NOT auto-sync docs (the old docs-sync hook was removed on purpose),
  so this is the manual reminder that replaces it.
---

# docs-check

This repo keeps docs for **three** audiences and reconciles them by hand:

- **`CLAUDE.md`** — agent/architecture guidance: the big-picture design, the
  invariants, the conventions. Update it when you change architecture or an
  invariant.
- **`README.md`** — the human-facing overview, incl. the **Environment variables**
  table. Update it when the human overview or an env var changes.
- **`frontend/integration-*.html`** — the public partner/CMS contracts (widget
  embed, player-data + handshake, Chat API, Telegram, admin API). Update the
  matching page when a **public contract** changes, keeping the house style.
- **`frontend/test.html`** — the example/landing page served at `/`: the widget
  demo, a short feature summary, and exactly one link to each integration page.
  A new integration page needs a link here; a widget change may need the demo
  refreshed.

## Run it

```bash
python scripts/docs_check.py          # vs origin/main (auto base)
python scripts/docs_check.py main     # vs an explicit base ref
```

It lists changed files and flags the docs a change of that shape usually needs
(architecture .py / api → CLAUDE.md; config.py → README env table; a public API
file → its integration page; any integration/widget change → test.html). Exit
code is always 0 — it's advisory.

## Then decide (it's a judgment call)

The script is a heuristic, not a gate. For each reminder:

1. Open the flagged doc and check whether your change actually alters what it
   documents. A no-op edit (e.g. removing an unused import) needs no doc change —
   skip it.
2. If it does, make the smallest accurate edit. Match the existing house style;
   for an integration page, keep it self-contained and cross-linked (header +
   footer) like its siblings.
3. Do **not** mirror CLAUDE.md into README or vice-versa — write each for its own
   audience. They are intentionally not copies of each other.

## The reverse check

Also ask what the script can't infer: did you add a **behaviour** (a new
escalation trigger, a new default, a changed gate order) that a doc describes in
prose even though no "doc-shaped" file changed? If so, update the relevant
CLAUDE.md section anyway.
