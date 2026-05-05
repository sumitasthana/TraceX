"""Shared configuration: env-driven paths, run_id propagation, structlog setup, DuckDB helpers.

Every stage script and the orchestrator import from here so logging is configured the same way
everywhere and run_id flows through a single pipeline run no matter how it is invoked.
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb
import structlog
from dotenv import load_dotenv

load_dotenv()

# ----- paths ---------------------------------------------------------------

LAYER1_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = LAYER1_ROOT.parent
DEFAULT_DB_PATH = REPO_ROOT / "synthetic-data-layer0" / "tracex_layer0.duckdb"
DEFAULT_LOG_DIR = LAYER1_ROOT / "logs"


def get_db_path() -> Path:
    return Path(os.environ.get("TRACEX_DB_PATH", str(DEFAULT_DB_PATH))).resolve()


def get_log_dir() -> Path:
    return Path(os.environ.get("TRACEX_LOG_DIR", str(DEFAULT_LOG_DIR))).resolve()


def get_run_id() -> str:
    """Return the orchestrator-supplied run_id, or mint a fresh one for standalone runs."""
    rid = os.environ.get("TRACEX_RUN_ID")
    if rid:
        return rid
    rid = str(uuid.uuid4())
    os.environ["TRACEX_RUN_ID"] = rid
    return rid


# ----- logging -------------------------------------------------------------

_LOGGING_CONFIGURED = False


def configure_logging(run_id: str, stage: str) -> structlog.stdlib.BoundLogger:
    """Configure structlog + stdlib logging exactly once per process.

    Emits one JSON object per line to stdout and appends to logs/{run_id}.jsonl.
    Binds run_id and stage as contextvars so every event carries them automatically.
    """
    global _LOGGING_CONFIGURED

    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.jsonl"

    if not _LOGGING_CONFIGURED:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        root.setLevel(logging.INFO)

        plain = logging.Formatter("%(message)s")

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(plain)
        root.addHandler(stream_handler)

        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setFormatter(plain)
        root.addHandler(file_handler)

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", key="ts", utc=True),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(sort_keys=False),
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
        _LOGGING_CONFIGURED = True

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(run_id=run_id, stage=stage)
    return structlog.get_logger()


# ----- duckdb --------------------------------------------------------------

@contextmanager
def db_connect(read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a DuckDB connection; close it cleanly even on error."""
    db_path = get_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"DuckDB file not found at {db_path} (set TRACEX_DB_PATH)")
    con = duckdb.connect(str(db_path), read_only=read_only)
    try:
        yield con
    finally:
        con.close()


def table_row_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    (n,) = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(n)


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
        [table],
    ).fetchone()
    return row is not None
