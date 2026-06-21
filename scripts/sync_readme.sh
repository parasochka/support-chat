#!/bin/sh
# Keep README.md byte-identical to CLAUDE.md (the single source of truth).
#
# CLAUDE.md is authoritative; README.md is a generated mirror of it. The root
# test page (main.py "/") already renders CLAUDE.md live, so this only mirrors
# the repo file. Run on demand, or automatically via the .githooks/pre-commit
# hook (enable once with: git config core.hooksPath .githooks).
set -e
root="$(git rev-parse --show-toplevel)"
if [ -f "$root/CLAUDE.md" ]; then
  cp "$root/CLAUDE.md" "$root/README.md"
fi
