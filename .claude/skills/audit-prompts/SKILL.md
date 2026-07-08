---
name: audit-prompts
description: >-
  Research and tidy the model-facing prompts in prompts.py — find contradictions,
  duplication and drift across the layered support and retention prompt stacks,
  then clean them up without homogenizing the two distinct personas or breaking a
  byte-stability invariant. Use when the user wants a prompt review/cleanup pass,
  reports the bot behaving off-persona or contradicting itself, or after editing
  any SYSTEM_CORE / directive / prompt-variable. NOT for KB content (answer data,
  admin-edited) or user-facing chrome copy (translations.py).
---

# audit-prompts

All model-facing prompt WORDING lives in **`prompts.py`** (the single source of
truth — not the admin panel, not the DB). This skill is the recurring "put the
prompts in order" pass: assemble what the model actually sees, hunt for
contradictions, and tidy the wording — while respecting the two hard constraints
that make this repo's prompts easy to break:

1. **There are TWO distinct personas, deliberately different.** The **support**
   widget persona and the **retention** Telegram persona share a name but are
   SEPARATE prompts with SEPARATE variable registries and NO inheritance. Tidying
   ≠ merging: never "fix" a difference by copying support wording into retention
   (or vice-versa). A support edit leaking into the bot is a regression, not a
   cleanup.
2. **The prompt is LAYERED by mutability, and Layer 1 is byte-stable.** Anything
   in the Layer-1 core (`get_system_core()` / `get_retention_system_core()`) must
   carry NO per-request data and stay byte-identical between requests within a
   product scope — a test enforces it. Per-request wording belongs in Layer 3.

## 1. Build the inventory — map every prompt surface

Before judging anything, list what the model actually receives. All of it is in
`prompts.py`:

**Support stack**
- `SYSTEM_CORE` — Layer-1 persona core (tone, absolute rules, escalation, machine tags).
- `_static_directives()` — the ordered Layer-1 behavioural directives:
  `_GREETING_DIRECTIVE`, `_FORMATTING_DIRECTIVE`, `_KB_GROUNDING_DIRECTIVE`,
  `_ESCALATION_RESTRAINT_DIRECTIVE`, `_SUGGESTIONS_DIRECTIVE`,
  `_RESOLVED_DIRECTIVE`, `_LEAD_FORWARD_DIRECTIVE`.
- Layer-3 (per-request, in `build_dynamic_prompt`): `_language_directive`,
  `_personalization_directive`, `_topic_routing_directive`,
  `_ONGOING_CONVERSATION_DIRECTIVE`, `_forbidden_topics_directive`
  (`FORBIDDEN_TOPICS` + `FORBIDDEN_TOPICS_REFUSAL`), `_GUARDRAILS`,
  `_CLOSING_GOODBYE_DIRECTIVE`.

**Retention stack** (mirror, but its own wording)
- `SYSTEM_CORE_RETENTION` — Layer-1 retention core.
- `_retention_static_directives()`: `_RETENTION_ENGAGEMENT_DIRECTIVE`,
  `_RETENTION_PHOTO_DIRECTIVE`, `_RETENTION_STAGE_DIRECTIVE`,
  `_RETENTION_LINK_DIRECTIVE`, `_RETENTION_FORMATTING_DIRECTIVE`.
- Layer-3 (in `build_retention_dynamic_prompt`):
  `_retention_personalization_directive`, `_photo_candidates_directive`,
  `_PLAY_NUDGE_DIRECTIVE`, `_previous_context_directive`, `_RETENTION_GUARDRAILS`,
  and the ping-only `build_retention_ping_messages` PROACTIVE block.
- `build_photo_meta_messages` — the vision prompt for photo metadata.

**Registries** (the brand values the templates render with, NOT wording)
- `PROMPT_VARIABLES` (support) and `RETENTION_PROMPT_VARIABLES` (retention, 4th
  field = `renders_as` base placeholder). Empty retention override falls back to
  the retention default, NEVER a support value.

Read the assembled result, not just the constants — a contradiction usually only
shows up once the directives are concatenated in order:

```bash
SUPPORT_CHAT_TEST_MODE=1 python - <<'PY'
import importlib.util as ilu
# db.py imports asyncpg at load; reuse the test bootstrap to stub it (same trick
# scripts/check_invariants.py uses) so prompts is importable outside pytest.
ilu.spec_from_file_location("_cf", "tests/conftest.py").loader.exec_module(
    ilu.module_from_spec(ilu.spec_from_file_location("_cf", "tests/conftest.py")))
import prompts
print("========== SUPPORT LAYER-1 ==========\n" + prompts.get_system_core())
print("\n========== RETENTION LAYER-1 ==========\n" + prompts.get_retention_system_core())
PY
```

For the full picture including Layer 3 as sent, the admin previews assemble it:
`GET /admin/effective-prompt` (support) and `GET /admin/retention/effective-prompt`
(retention) — same builders the model uses (`api.admin._build_effective_preview`).

## 2. Hunt for contradictions (the actual research)

Go directive by directive and check these classes. Real examples this repo has
hit are in parentheses — they are the shape to look for:

- **Core vs directive** — a rule in `SYSTEM_CORE` that a later directive negates
  (the core once said "plain text only" while `_FORMATTING_DIRECTIVE` asks for a
  Markdown subset; CLAUDE.md now pins them in lockstep). The core must not
  contradict the formatting/greeting/suggestion directives.
- **Directive vs directive** — two directives pulling opposite ways (greeting
  "open with a by-name greeting" vs a no-filler/brevity rule that would drop it;
  the fix was an explicit "the brevity rules do NOT drop the greeting", not
  deleting either).
- **Layer 1 vs Layer 3** — a static rule duplicated or re-stated per-request
  (greeting handled in BOTH the static directive and the personalization
  directive — they must agree, and the per-turn imperative lives in Layer 3 on
  purpose; don't collapse it into Layer 1).
- **Support vs retention drift** — a rule that SHOULD differ but was copied flat.
  Support Nika uses no emoji ever; retention allows one emoji only in a photo
  caption. Support greets by name on the first reply; retention SUPPRESSES the
  first-turn greeting (the menu already greeted). These opposites are correct —
  flag it only if one side accidentally carries the other's rule.
- **Formatting vs channel** — the widget renders a fixed Markdown subset; the
  Telegram channel renders a light HTML subset + link markup and BANS
  em/en dashes and guillemets. A formatting rule that asks for output the channel
  can't render (tables, fenced code, bare URLs) leaks literal characters.
- **Machine-tag consistency** — every `[[TAG]]` a directive tells the model to
  emit must be (a) listed in the core's MACHINE TAGS block with the right
  placement (top-of-reply vs last line), (b) stripped by a `strip_*` helper, and
  (c) never described in visible prose. A directive introducing a tag the core's
  tag list doesn't mention is a contradiction.
- **Stale/duplicate wording** — the same instruction stated twice (drift risk:
  edit one, forget the other), or wording describing behaviour that moved (e.g. a
  directive still generating the closing option the backend now supplies).
- **Placeholder hygiene** — every `{placeholder}` in a template is a registered
  key in the matching registry (support keys in `PROMPT_VARIABLES`, retention base
  placeholders reachable via a `renders_as` in `RETENTION_PROMPT_VARIABLES`); an
  unregistered `{brace}` renders literally. Cross-check with
  `retention_prompt_variable_keys()`.

## 3. Tidy — the rules while you edit

- **English only** for all model-facing wording (Invariant 7) — the language
  directive makes the model answer in the player's language; the prompt text
  never mirrors the player.
- **Keep Layer 1 byte-stable and per-request-free.** A new STATIC rule → a
  directive in `_static_directives()` / `_retention_static_directives()`. A rule
  needing per-request data → Layer 3. Never put per-request data in a core.
- **Don't homogenize the personas.** Edit the support and retention constants
  independently even when the change is "the same idea".
- **Registries hold VALUES, wording holds RULES.** Persona name / brand / tone go
  in the registries (admin-editable); the surrounding sentences stay in the
  template. Don't hard-code a brand name into wording.
- **Preserve the immediate-escalation carve-outs** when touching escalation
  wording (explicit human request, complaint, fraud, legal, responsible-gaming) —
  restraint applies to everything ELSE.
- Match the existing voice and directive structure (`HEADING:` + `- ` bullets).

## 4. Report

Produce a findings list before (or alongside) edits, each as:
`surface → class of issue → the conflicting lines → proposed fix`. Separate
CONTRADICTIONS (must fix) from TIDY-UPS (duplication/staleness, optional). Call
out explicitly any place where support and retention differ *correctly* so a
reviewer doesn't "fix" it later.

## 5. Verify

```bash
bash scripts/preflight.sh --checks    # ruff + invariants + full suite
```

The load-bearing checks: `scripts/check_invariants.py` asserts the Layer-1 cores
stay byte-stable and non-empty; `tests/test_prompt_cache_stable.py` guards
byte-stability across requests; `tests/test_prompt_variables.py` /
`test_retention_prompt_variables.py` guard the registries and no-support-leak;
`tests/test_effective_prompt.py` guards the previews; `tests/test_retention.py`
pins retention persona rules. If you changed documented prompt behaviour, run
`/docs-check` and update the relevant section of `CLAUDE.md`.
