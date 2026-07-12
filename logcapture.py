"""In-process capture of the application's own log records so the admin panel
can show recent runtime logs (the "Railway logs") without leaving the panel.

Design constraints:
  - Logging is called from synchronous code (and occasionally other threads),
    while the DB is async. So the handler NEVER touches the DB: it only appends
    a small dict to an in-memory deque under a plain lock (fast, thread-safe,
    re-entrancy-proof — a DB write that itself logged would otherwise recurse).
  - A background flush task (main.py lifespan) periodically drains the deque and
    batch-inserts into the bounded `app_logs` table.
  - The deque is capped: if the flusher ever stalls, the OLDEST pending records
    are dropped rather than growing memory without bound.

Only the application logger (config.SERVICE_NAME) is captured — NOT uvicorn's
access log — so the volume stays meaningful (escalations, failovers, retention
decisions, warnings, errors), not one row per HTTP request.
"""
from __future__ import annotations

import collections
import logging
import threading
from typing import Any

# Backpressure cap: at most this many un-flushed records are held in memory.
_MAX_PENDING = 20000

_pending: "collections.deque[dict[str, Any]]" = collections.deque()
_lock = threading.Lock()
_installed = False


class _BufferHandler(logging.Handler):
    """Appends each record to the in-memory buffer. Never raises, never blocks."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                # Fold the traceback into the message so the admin log row is
                # self-contained (the flusher stores a single text column).
                message = f"{message}\n{self.formatException(record.exc_info)}"
            entry = {
                "level": record.levelname,
                "logger": record.name,
                "message": message[:8000],
                "created": record.created,
            }
            with _lock:
                _pending.append(entry)
                while len(_pending) > _MAX_PENDING:
                    _pending.popleft()
        except Exception:
            # A logging handler must never break the code that logged.
            pass


def drain() -> list[dict[str, Any]]:
    """Pop and return all buffered records (called by the flush loop)."""
    with _lock:
        if not _pending:
            return []
        items = list(_pending)
        _pending.clear()
    return items


def install(logger_name: str, level: int = logging.INFO) -> None:
    """Attach the buffer handler to the named logger. Idempotent."""
    global _installed
    if _installed:
        return
    handler = _BufferHandler()
    handler.setLevel(level)
    logging.getLogger(logger_name).addHandler(handler)
    _installed = True
