"""Pipeline run registry — DuckDB-backed.

Records every orchestrator invocation as a row in `pipeline_runs` and every
stage subprocess as a row in `pipeline_run_stages`. The orchestrator is the
only writer; stages read via `config.get_current_run_metadata()`.

Concurrency: DuckDB does not support concurrent writers across processes, so
every helper here opens a short-lived connection, writes, closes — the
orchestrator must NOT hold a connection while a stage subprocess runs (the
subprocess needs the write lock to mutate src_/stg_/fct_ tables).

Schema is created idempotently on every orchestrator entry.
"""
from __future__ import annotations

import datetime as _dt
import subprocess
from pathlib import Path
from typing import List, Optional

import duckdb

from pipeline.config import get_db_path

DDL = [
    """
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        run_id           VARCHAR PRIMARY KEY,
        as_of_date       DATE      NOT NULL,
        started_at       TIMESTAMP NOT NULL,
        ended_at         TIMESTAMP,
        status           VARCHAR   NOT NULL,
        failed_stage     VARCHAR,
        git_sha          VARCHAR,
        stages_planned   VARCHAR[]   NOT NULL,
        duration_ms      INTEGER,
        log_file         VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_run_stages (
        run_id      VARCHAR NOT NULL,
        stage_name  VARCHAR NOT NULL,
        started_at  TIMESTAMP NOT NULL,
        ended_at    TIMESTAMP,
        status      VARCHAR NOT NULL,
        exit_code   INTEGER,
        duration_ms INTEGER,
        PRIMARY KEY (run_id, stage_name)
    )
    """,
]


def _now() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(tzinfo=None)


def _connect() -> duckdb.DuckDBPyConnection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def init_registry() -> None:
    """Idempotent: create the registry tables if missing."""
    con = _connect()
    try:
        for stmt in DDL:
            con.execute(stmt)
    finally:
        con.close()


def resolve_git_sha() -> Optional[str]:
    """Best-effort `git rev-parse HEAD`; returns None on failure (e.g. no git
    binary on PATH, or the working dir is not a repo)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return None


def insert_run(
    run_id: str,
    as_of_date: _dt.date,
    stages_planned: List[str],
    log_file: str,
    git_sha: Optional[str],
) -> _dt.datetime:
    """Open a row in `pipeline_runs` with `status='running'` and return its
    `started_at`. Caller passes the value back via `finalize_run`."""
    started_at = _now()
    con = _connect()
    try:
        con.execute(
            """
            INSERT INTO pipeline_runs
            (run_id, as_of_date, started_at, ended_at, status, failed_stage,
             git_sha, stages_planned, duration_ms, log_file)
            VALUES (?, ?, ?, NULL, 'running', NULL, ?, ?, NULL, ?)
            """,
            [run_id, as_of_date, started_at, git_sha, stages_planned, log_file],
        )
    finally:
        con.close()
    return started_at


def finalize_run(
    run_id: str,
    started_at: _dt.datetime,
    status: str,
    failed_stage: Optional[str],
) -> None:
    """Close a `pipeline_runs` row with the terminal status."""
    ended_at = _now()
    duration_ms = int((ended_at - started_at).total_seconds() * 1000)
    con = _connect()
    try:
        con.execute(
            """
            UPDATE pipeline_runs
               SET ended_at = ?, status = ?, failed_stage = ?, duration_ms = ?
             WHERE run_id = ?
            """,
            [ended_at, status, failed_stage, duration_ms, run_id],
        )
    finally:
        con.close()


def insert_stage(run_id: str, stage_name: str) -> _dt.datetime:
    started_at = _now()
    con = _connect()
    try:
        con.execute(
            """
            INSERT INTO pipeline_run_stages
            (run_id, stage_name, started_at, ended_at, status, exit_code, duration_ms)
            VALUES (?, ?, ?, NULL, 'running', NULL, NULL)
            """,
            [run_id, stage_name, started_at],
        )
    finally:
        con.close()
    return started_at


def finalize_stage(
    run_id: str,
    stage_name: str,
    started_at: _dt.datetime,
    exit_code: int,
) -> None:
    ended_at = _now()
    duration_ms = int((ended_at - started_at).total_seconds() * 1000)
    status = "ok" if exit_code == 0 else "failed"
    con = _connect()
    try:
        con.execute(
            """
            UPDATE pipeline_run_stages
               SET ended_at = ?, status = ?, exit_code = ?, duration_ms = ?
             WHERE run_id = ? AND stage_name = ?
            """,
            [ended_at, status, exit_code, duration_ms, run_id, stage_name],
        )
    finally:
        con.close()


__all__ = [
    "init_registry",
    "resolve_git_sha",
    "insert_run",
    "finalize_run",
    "insert_stage",
    "finalize_stage",
]
