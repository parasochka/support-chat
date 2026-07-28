#!/usr/bin/env python3
"""Fast static checks for the "breaks silently" invariants in CLAUDE.md.

These are the cross-file consistency rules that the test suite does not (or
cannot cheaply) cover and that have bitten real commits: a translation key added
to the registry but not to the shipped English copy, the Layer-1 prompt core
drifting from byte-stable, and a new settings group that never surfaces in the
admin UI. Import-based (not grep) so it reads the real data structures.

Run directly or via scripts/preflight.sh / the /preflight skill:

    SUPPORT_CHAT_TEST_MODE=1 python scripts/check_invariants.py

Exit code 0 = all good, 1 = at least one invariant violated.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("SUPPORT_CHAT_TEST_MODE", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Reuse the test bootstrap so we stub openai/asyncpg exactly like the suite does
# (db.py imports asyncpg at module load; the modules we check pull in db). This
# keeps the stubbing single-sourced in tests/conftest.py instead of duplicated.
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("_conftest_bootstrap", ROOT / "tests" / "conftest.py")
if _spec and _spec.loader:  # pragma: no branch
    _spec.loader.exec_module(_ilu.module_from_spec(_spec))

_failures: list[str] = []
_passes: list[str] = []


def _fail(check: str, msg: str) -> None:
    _failures.append(f"{check}: {msg}")


def _ok(check: str) -> None:
    _passes.append(check)


def check_translation_completeness() -> None:
    """Every registry key must have shipped English copy, and vice-versa.

    Resolution falls back to English, so a missing ru/es/tr/pt entry is fine by
    design — but a key in KEYS with no DEFAULTS['en'] entry renders empty for
    everyone, and a DEFAULTS key not in KEYS is dead copy. (contact_url is the
    documented en-only key, so it is exempt from the orphan check.)
    """
    from app.i18n import translations as t

    registry = {k for (k, _scope, _desc) in t.KEYS}
    english = set(t.DEFAULTS.get("en", {}))

    missing_en = sorted(registry - english)
    if missing_en:
        _fail(
            "translations",
            "keys in KEYS with no DEFAULTS['en'] copy: " + ", ".join(missing_en),
        )

    exempt = {"contact_url"}
    orphan = sorted(english - registry - exempt)
    if orphan:
        _fail(
            "translations",
            "keys in DEFAULTS['en'] not registered in KEYS: " + ", ".join(orphan),
        )

    if not missing_en and not orphan:
        _ok(f"translations ({len(registry)} keys, English copy complete)")


def check_prompt_core_byte_stable() -> None:
    """Layer-1 cores must be byte-identical across calls (prefix cache).

    Both cores are byte-stability invariants (CLAUDE.md §1): the support core AND
    the retention (Telegram) core. The retention one was previously only guarded
    by a pytest test, so a per-request leak into the retention Layer-1 slipped the
    static gate the /preflight skill leans on.
    """
    from app.ai import prompts

    for name, fn in (("support", prompts.get_system_core),
                     ("retention", prompts.get_retention_system_core)):
        a = fn()
        b = fn()
        if a != b:
            _fail("prompt-core", f"{name} core ({fn.__name__}) is not byte-stable across calls")
            return
        if not a.strip():
            _fail("prompt-core", f"{name} core ({fn.__name__}) returned empty")
            return
    _ok("prompt-core (support + retention Layer-1 byte-stable)")


def _js_block(source: str, name: str) -> str:
    """The body of a top-level `export const NAME = { … }` object.

    Brace-counted, not regex-matched: a non-greedy `\\{(.*?)\\}` stops at the
    first nested `}`, so everything after the first field object silently fell
    outside the searched text.
    """
    m = re.search(rf"{name}\s*=\s*\{{", source)
    if not m:
        return ""
    start = m.end()
    depth = 1
    for i in range(start, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start:i]
    return ""


def check_settings_groups_have_ui() -> None:
    """Every writable settings group must surface in the admin Settings schema.

    A new group added to SETTING_KEYS but not to settingsSchema.js is invisible
    in the panel. It needs BOTH a label and at least one field: the typed editor
    renders from GROUP_FIELDS, so a group with only a GROUP_LABELS entry shows
    up as an empty page. Checking labels alone passed a group whose editor had
    nothing in it — `language` was exactly that, which is why it needs an
    exemption here rather than being caught.

    Exempt (own editors, deliberately absent from GROUP_FIELDS):
      - `escalation`, edited on the Common → Escalation keywords page;
      - `language`, which has its own LanguageEditor in Settings.jsx.
    """
    from app.core import settings

    schema = ROOT / "admin" / "src" / "pages" / "settingsSchema.js"
    if not schema.exists():
        _fail("settings-ui", "settingsSchema.js not found")
        return
    source = schema.read_text()

    labels_block = _js_block(source, "GROUP_LABELS")
    fields_block = _js_block(source, "GROUP_FIELDS")
    if not labels_block or not fields_block:
        _fail("settings-ui",
              "could not parse GROUP_LABELS / GROUP_FIELDS out of "
              "settingsSchema.js")
        return

    # Own-editor groups: exempt from GROUP_FIELDS, still required in the labels.
    fields_exempt = {"escalation", "language"}
    labels_exempt = {"escalation"}

    missing_labels = [
        g for g in settings.SETTING_KEYS
        if g not in labels_exempt
        and not re.search(rf"\b{re.escape(g)}\s*:", labels_block)
    ]
    # A field list must exist AND be non-empty — `foo: []` is as invisible as no
    # entry at all.
    missing_fields = [
        g for g in settings.SETTING_KEYS
        if g not in fields_exempt
        and not re.search(rf"\b{re.escape(g)}\s*:\s*\[\s*\{{", fields_block)
    ]
    problems = []
    if missing_labels:
        problems.append("missing from GROUP_LABELS: " + ", ".join(missing_labels))
    if missing_fields:
        problems.append("no fields in GROUP_FIELDS: " + ", ".join(missing_fields))
    if problems:
        _fail("settings-ui", "settingsSchema.js — " + "; ".join(problems))
        return
    _ok("settings-ui (every group has a label and typed fields)")


def main() -> int:
    for check in (
        check_translation_completeness,
        check_prompt_core_byte_stable,
        check_settings_groups_have_ui,
    ):
        try:
            check()
        except Exception as exc:  # a check that crashes is itself a failure
            _fail(check.__name__, f"raised {type(exc).__name__}: {exc}")

    for name in _passes:
        print(f"  PASS  {name}")
    for msg in _failures:
        print(f"  FAIL  {msg}")

    if _failures:
        print(f"\n{len(_failures)} invariant check(s) failed.")
        return 1
    print(f"\nAll {len(_passes)} invariant checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
