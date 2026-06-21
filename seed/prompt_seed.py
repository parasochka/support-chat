"""Seed the live system-prompt version from the Phase 1 SYSTEM_CORE constant.

Phase 2 loads the live core from `prompt_versions` instead of the constant, so
on first boot we migrate the byte-stable Phase 1 core into a single published,
default row. Idempotent: if a default version already exists, do nothing (so the
owner's later edits/publishes are never clobbered by a redeploy).
"""
from __future__ import annotations

import db
import prompts

# Stable label for the migrated Phase 1 baseline.
BASELINE_NAME = "core-phase1-baseline"


async def run() -> None:
    existing = await db.get_default_prompt_version()
    if existing is not None:
        return
    await db.create_prompt_version(
        name=BASELINE_NAME,
        body=prompts.SYSTEM_CORE,
        status="published",
        is_default=True,
    )
