"""Lineage-layer config: env-driven paths, Kuzu helpers, structlog setup.

Defaults resolve relative to the layer1 project root so the script works regardless
of the caller's cwd. Override either via env var:

    TRACEX_GRAPH_PATH    path to the Kuzu database file (single file, not a dir)
    TRACEX_LOG_DIR       directory containing pipeline JSONL logs
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

import kuzu
import structlog
from dotenv import load_dotenv

load_dotenv()

LINEAGE_ROOT = Path(__file__).resolve().parent
LAYER1_ROOT = LINEAGE_ROOT.parent

DEFAULT_GRAPH_PATH = LAYER1_ROOT / "tracex_graph"
DEFAULT_LOG_DIR = LAYER1_ROOT / "logs"


def get_graph_path() -> Path:
    return Path(os.environ.get("TRACEX_GRAPH_PATH", str(DEFAULT_GRAPH_PATH))).resolve()


def get_log_dir() -> Path:
    return Path(os.environ.get("TRACEX_LOG_DIR", str(DEFAULT_LOG_DIR))).resolve()


def get_db() -> kuzu.Database:
    """Open / create a Kuzu Database at the configured path. Ensures the parent
    directory exists; Kuzu itself manages the file.
    """
    path = get_graph_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return kuzu.Database(str(path))


def get_conn(db: kuzu.Database) -> kuzu.Connection:
    return kuzu.Connection(db)


_LOGGING_CONFIGURED = False


def configure_logging(run_id: str, component: str) -> structlog.stdlib.BoundLogger:
    """Configure structlog once per process; bind run_id + component on every event.

    Appends to the same logs/{run_id}.jsonl that the producing pipeline run wrote
    to, so a single timeline carries both pipeline and ingestion events.
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

        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(plain)
        root.addHandler(sh)

        fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        fh.setFormatter(plain)
        root.addHandler(fh)

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
    structlog.contextvars.bind_contextvars(run_id=run_id, component=component)
    return structlog.get_logger()


def find_latest_log(log_dir: Optional[Path] = None) -> Optional[Path]:
    log_dir = log_dir or get_log_dir()
    if not log_dir.exists():
        return None
    candidates = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None
