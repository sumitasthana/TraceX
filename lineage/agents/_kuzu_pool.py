"""Process-local Kuzu Database singleton for agent tools.

Kuzu enforces a single-writer file lock per Database instance — even within
one process, two simultaneous `kuzu.Database(path)` calls collide. The
LangGraph ToolNode runs tool calls in parallel when the model emits multiple
tool_use blocks in one turn, so without a singleton the second tool call
crashes with "Could not set lock on file".

This module exposes one shared Database (lazy-init, thread-safe) and hands
out fresh Connections per call. Connections themselves are not thread-safe,
so each call gets its own.
"""
from __future__ import annotations

import threading
from typing import Optional

import kuzu

from lineage.config import get_graph_path

_lock = threading.Lock()
_db: Optional[kuzu.Database] = None


def get_shared_db() -> kuzu.Database:
    """Return the process-local Kuzu Database singleton."""
    global _db
    with _lock:
        if _db is None:
            path = get_graph_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _db = kuzu.Database(str(path))
        return _db


def get_shared_conn() -> kuzu.Connection:
    return kuzu.Connection(get_shared_db())


def reset_shared_db() -> None:
    """Drop the singleton — used by tests or when an outer GraphBuilder needs
    exclusive write access. Safe to call any time; next get_* re-initializes."""
    global _db
    with _lock:
        _db = None
