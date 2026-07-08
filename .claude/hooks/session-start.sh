#!/usr/bin/env bash
# SessionStart hook: make this repo verifiable the moment a session opens.
# Installs runtime + dev deps so `pytest`, `ruff` and the invariant checks work
# without the "No module named pytest / httpx / fastapi" dance every web session
# otherwise hits (conftest.py stubs only openai + asyncpg; everything else in
# requirements.txt must really be installed — see CLAUDE.md > Commands).
#
# Synchronous (no async block): the session waits until deps are ready, so the
# agent never races ahead and runs tests before they can import. Runs only in
# Claude Code on the web; local machines already have their own env.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"
bash scripts/preflight.sh --install

# Keep tests importable without a real DB/API key in every future command.
echo 'export SUPPORT_CHAT_TEST_MODE=1' >> "${CLAUDE_ENV_FILE:-/dev/null}"
