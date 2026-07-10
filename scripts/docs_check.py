#!/usr/bin/env python3
"""Remind which docs to reconcile for the current change.

The repo keeps TWO docs for two audiences and does NOT auto-sync them (the old
docs-sync hook was removed on purpose): README.md is the human-facing overview,
CLAUDE.md is the agent/architecture guidance, and frontend/integration-*.html are
the public partner contracts. This script looks at what changed vs a base ref and
flags the docs that a change of that shape usually needs — advisory only, since
whether a given edit warrants a doc change is a judgment call.

    python scripts/docs_check.py            # vs origin/main (auto)
    python scripts/docs_check.py main       # vs an explicit base ref

Exit code is always 0: this is a reminder, not a gate.
"""
from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False
        ).stdout.strip()
    except Exception:
        return ""


def _base_ref(argv: list[str]) -> str:
    if len(argv) > 1 and argv[1]:
        return argv[1]
    for cand in ("origin/main", "main"):
        mb = _run("merge-base", "HEAD", cand)
        if mb:
            return mb
    return "HEAD~1"


def _changed_files(base: str) -> set[str]:
    files: set[str] = set()
    # Tracked changes (committed + staged + unstaged) since the base.
    files.update(f for f in _run("diff", "--name-only", base).splitlines() if f)
    # New untracked files.
    files.update(
        f for f in _run("ls-files", "--others", "--exclude-standard").splitlines() if f
    )
    return files


# Each rule: (predicate over the changed-file set) -> (doc it wants, why).
# Emitted only when the wanted doc is NOT itself among the changed files.
_ARCH_PY = {
    "settings.py", "prompts.py", "db.py", "chat_service.py", "retention.py",
    "retention_v2.py", "player_sync.py", "escalation.py", "openai_client.py",
    "antispam.py",
    "language.py", "tenancy.py", "translations.py", "starter_kb.py", "main.py",
    "secretbox.py", "kb.py", "metrics.py", "telegram_transport.py",
    "telegram_format.py",
}


def _hits(changed: set[str], *, prefix: str | None = None,
          names: set[str] | None = None) -> list[str]:
    out = []
    for f in sorted(changed):
        if prefix and f.startswith(prefix):
            out.append(f)
        elif names and f in names:
            out.append(f)
    return out


def main(argv: list[str]) -> int:
    base = _base_ref(argv)
    changed = _changed_files(base)
    if not changed:
        print(f"No changes vs {base}. Nothing to reconcile.")
        return 0

    claude_touched = "CLAUDE.md" in changed
    readme_touched = "README.md" in changed
    reminders: list[str] = []

    # 1. Architecture / invariants -> CLAUDE.md
    arch = _hits(changed, names=_ARCH_PY) + _hits(changed, prefix="api/")
    if arch and not claude_touched:
        reminders.append(
            "CLAUDE.md — architecture/invariants changed but CLAUDE.md is "
            "untouched.\n        Changed: " + ", ".join(sorted(set(arch))[:8])
            + ("…" if len(set(arch)) > 8 else "")
        )

    # 2. Env vars -> README.md "Environment variables" table
    if "config.py" in changed and not readme_touched:
        reminders.append(
            "README.md (§ Environment variables) — config.py changed; check the "
            "env-vars table for a new/renamed/removed var."
        )

    # 3. Public contracts -> the matching integration-*.html page
    contract_map = [
        ("api/chat.py", "frontend/integration-chat-api.html",
         "public Chat API changed"),
        ("auth.py", "frontend/integration-data.html",
         "signed-handshake / player-data contract may have changed"),
        ("api/retention.py", "frontend/integration-telegram.html",
         "retention/Telegram contract changed"),
        ("api/admin.py", "frontend/integration-admin.html",
         "the /admin/* endpoint reference changed"),
    ]
    for src, page, why in contract_map:
        if src in changed and page not in changed:
            reminders.append(f"{page} — {why} ({src}).")
    # Retention transport/orchestration also feeds the telegram page.
    if ({"retention.py", "retention_v2.py", "telegram_transport.py"} & changed
            and "frontend/integration-telegram.html" not in changed):
        reminders.append(
            "frontend/integration-telegram.html — retention internals changed; "
            "re-check the deeplink / ping / bot contract."
        )

    # 4. The example/landing page served at `/` (frontend/test.html): the widget
    #    demo + feature summary + exactly one link to each integration page.
    if "frontend/test.html" not in changed:
        integ = sorted(f for f in changed if f.startswith("frontend/integration"))
        widget_changed = {"frontend/widget.js", "frontend/widget.css"} & changed
        if integ:
            reminders.append(
                "frontend/test.html — an integration page changed/was added; it "
                "carries one link to each, and a new page needs a link there. "
                "Changed: " + ", ".join(integ)
            )
        if widget_changed:
            reminders.append(
                "frontend/test.html — the embedded widget demo / feature summary "
                "may need updating (" + ", ".join(sorted(widget_changed)) + ")."
            )

    print(f"Docs check vs {base}: {len(changed)} changed file(s).")
    print(f"  CLAUDE.md touched: {'yes' if claude_touched else 'no'} | "
          f"README.md touched: {'yes' if readme_touched else 'no'}")
    if not reminders:
        print("\nNo doc reconciliation flagged. (Still your call — a behaviour "
              "change may warrant a doc note even if not listed.)")
        return 0

    print("\nConsider updating:")
    for r in reminders:
        print(f"  - {r}")
    print("\nReminder only — skip any that don't apply to this change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
