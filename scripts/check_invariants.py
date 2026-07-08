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
    import translations as t

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
    """Layer-1 support core must be byte-identical across calls (prefix cache)."""
    import prompts

    a = prompts.get_system_core()
    b = prompts.get_system_core()
    if a != b:
        _fail("prompt-core", "get_system_core() is not byte-stable across calls")
        return
    if not a.strip():
        _fail("prompt-core", "get_system_core() returned empty")
        return
    _ok("prompt-core (Layer-1 byte-stable)")


def check_settings_groups_have_ui() -> None:
    """Every writable settings group must surface in the admin Settings schema.

    A new group added to SETTING_KEYS but not to settingsSchema.js is invisible
    in the panel. `escalation` (edited on the Prompt page) and `language` (its
    own editor) are the two documented exceptions.
    """
    import settings

    schema = (ROOT / "admin" / "pages" / "settingsSchema.js")
    if not schema.exists():
        schema = ROOT / "admin" / "src" / "pages" / "settingsSchema.js"
    if not schema.exists():
        _fail("settings-ui", "settingsSchema.js not found")
        return

    labels_block = ""
    m = re.search(r"GROUP_LABELS\s*=\s*\{(.*?)\}", schema.read_text(), re.S)
    if m:
        labels_block = m.group(1)

    ui_exempt = {"escalation"}  # edited on the Prompt page, not the Settings tab
    missing = [
        g
        for g in settings.SETTING_KEYS
        if g not in ui_exempt and not re.search(rf"\b{re.escape(g)}\s*:", labels_block)
    ]
    if missing:
        _fail(
            "settings-ui",
            "SETTING_KEYS groups missing from settingsSchema.js GROUP_LABELS: "
            + ", ".join(missing),
        )
        return
    _ok("settings-ui (every group has an admin schema entry)")


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
