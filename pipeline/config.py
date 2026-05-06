"""Shared configuration: env-driven paths, run_id propagation, structlog setup, DuckDB helpers.

Every stage script and the orchestrator import from here so logging is configured the same way
everywhere and run_id flows through a single pipeline run no matter how it is invoked.

The pipeline is now date-anchored: every run requires a `TRACEX_AS_OF_DATE` (set by the
orchestrator from `--as-of-date`) so the same input + same date produce byte-identical
output. Stages reach for `get_as_of_date()` instead of `CURRENT_DATE` / `CURRENT_TIMESTAMP`
inside their SQL.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import duckdb
import structlog
from dotenv import load_dotenv

load_dotenv()

# ----- paths ---------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "tracex_layer0.duckdb"
DEFAULT_LOG_DIR = REPO_ROOT / "logs"


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


def get_as_of_date() -> _dt.date:
    """Return the pipeline's business `as_of_date` from `TRACEX_AS_OF_DATE`.

    Required — raises `RuntimeError` if unset. The orchestrator MUST set this
    from its `--as-of-date YYYY-MM-DD` argument before dispatching subprocesses.
    Using a getter (instead of reading env directly inside SQL strings) keeps
    one place to fail loudly when the contract is violated.
    """
    raw = os.environ.get("TRACEX_AS_OF_DATE")
    if not raw:
        raise RuntimeError(
            "TRACEX_AS_OF_DATE is unset. The pipeline is now date-anchored — "
            "invoke the orchestrator with `--as-of-date YYYY-MM-DD`."
        )
    try:
        return _dt.date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise RuntimeError(
            f"TRACEX_AS_OF_DATE={raw!r} is not a valid ISO date (YYYY-MM-DD): {exc}"
        ) from exc


def get_current_run_metadata() -> dict:
    """Read-only snapshot of the current run's identity, for stages that want to log
    run context. Returns `{run_id, as_of_date, started_at}` from env + the registry.
    `started_at` is None when no `pipeline_runs` row exists yet (e.g. standalone stage)."""
    run_id = os.environ.get("TRACEX_RUN_ID") or ""
    aod = os.environ.get("TRACEX_AS_OF_DATE") or ""
    started_at: Optional[str] = None
    db_path = get_db_path()
    if db_path.exists() and run_id:
        try:
            con = duckdb.connect(str(db_path), read_only=True)
            try:
                row = con.execute(
                    "SELECT started_at FROM pipeline_runs WHERE run_id = ?",
                    [run_id],
                ).fetchone()
                if row:
                    started_at = str(row[0])
            finally:
                con.close()
        except Exception:
            started_at = None
    return {"run_id": run_id, "as_of_date": aod, "started_at": started_at}


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
