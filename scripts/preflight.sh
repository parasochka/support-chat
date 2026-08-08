#!/usr/bin/env bash
# One command to get this repo verifiable: install deps, run ruff, the invariant
# checks, and the test suite. Safe to run repeatedly. Used by the SessionStart
# hook (install step) and the /preflight skill (full run).
#
# Usage:
#   scripts/preflight.sh            # install (if needed) + ruff + invariants + tests
#   scripts/preflight.sh --install  # only install dependencies (hook uses this)
#   scripts/preflight.sh --checks   # skip install; ruff + invariants + tests only
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
export SUPPORT_CHAT_TEST_MODE=1

MODE="${1:-all}"
rc=0

install_deps() {
  echo ">> Installing dependencies (test env + dev tools)…"
  # The suite runs against conftest.py's stubs for `openai` and `asyncpg`, so
  # those two must NOT be really installed — the failover tests build openai
  # error objects with the stub's lenient constructors, which the real SDK
  # rejects. Install everything else from requirements.txt (fastapi, uvicorn,
  # httpx, python-multipart — all imported for real) plus the dev tools. Filter
  # from requirements.txt (not a hand-copied list) so it can't drift.
  local req_test
  req_test="$(mktemp)"
  grep -viE '^\s*(openai|asyncpg)\b' requirements.txt > "$req_test"
  # Retries + a generous timeout so a flaky PyPI read doesn't fail session start.
  python -m pip install -q --retries 5 --timeout 60 \
    -r "$req_test" -r requirements-dev.txt
  local rc_install=$?
  rm -f "$req_test"
  return "$rc_install"
}

run_ruff() {
  echo ">> ruff (real-bug rules; see pyproject.toml)…"
  ruff check . || return 1
}

run_invariants() {
  echo ">> Invariant checks (translations / prompt core / settings UI)…"
  python scripts/check_invariants.py || return 1
}

run_tests() {
  echo ">> pytest…"
  python -m pytest -q || return 1
}

run_admin_syntax() {
  # The admin SPA has its own Vite build that nothing here runs (it deploys as
  # its own static site), so a syntax error in it ships green: preflight and CI
  # are Python-only. `admin/src/i18n.js` is the likeliest casualty — it is a
  # plain-JS dict of thousands of quoted Russian strings, hand-edited whenever
  # an admin-visible string is added, and one mismatched quote breaks the whole
  # build. `node --check` costs milliseconds and needs no node_modules, so it
  # catches that class without pulling an npm install into this script.
  # Skipped (not failed) where node is absent — this is a guard, not a gate.
  if ! command -v node >/dev/null 2>&1; then
    echo ">> admin JS syntax… skipped (no node)"
    return 0
  fi
  echo ">> admin JS syntax (node --check)…"
  local bad=0 f
  while IFS= read -r f; do
    node --check "$f" || { echo "  BROKEN: $f"; bad=1; }
  done < <(find admin/src -name '*.js' -not -path '*/node_modules/*' | sort)
  [ "$bad" -eq 0 ] || return 1
}

case "$MODE" in
  --install)
    install_deps || rc=1
    ;;
  --checks)
    run_ruff         || rc=1
    run_invariants   || rc=1
    run_admin_syntax || rc=1
    run_tests        || rc=1
    ;;
  all|"")
    install_deps     || { echo "!! dependency install failed"; exit 1; }
    run_ruff         || rc=1
    run_invariants   || rc=1
    run_admin_syntax || rc=1
    run_tests        || rc=1
    ;;
  *)
    echo "usage: $0 [--install|--checks]" >&2
    exit 2
    ;;
esac

if [ "$rc" -eq 0 ]; then
  echo "== preflight OK =="
else
  echo "== preflight FAILED =="
fi
exit "$rc"
