"""Top-level orchestrator: runs every Layer 1 stage as an independent subprocess.

Each stage inherits `TRACEX_RUN_ID` and `TRACEX_AS_OF_DATE` via env so all events
for a single pipeline run land in the same `logs/{run_id}.jsonl` and every SQL
expression that needs a date can read it from one place.

The orchestrator owns the run registry: it inserts `pipeline_runs` and
`pipeline_run_stages` rows, holds NO DuckDB connection while a stage runs
(stages need the write lock), and finalises the run row before exit.

Invocation:

    python pipeline/run_pipeline.py --as-of-date 2024-09-30

Running without `--as-of-date` is a hard failure — the pipeline is now
date-anchored to give byte-identical output for the same business date.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.config import (  # noqa: E402
    configure_logging,
    get_db_path,
    get_log_dir,
)
from pipeline import registry as _registry  # noqa: E402

ORCHESTRATOR_NAME = "run_pipeline"

STAGES = [
    "_1_ingest_landing.py",
    "00_validate_sources.py",
    "01_stg_fx_normalize.py",
    "02_stg_transactions.py",
    "03_stg_customers.py",
    "10_fct_risk_profile.py",
    "99_validate_outputs.py",
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_pipeline",
        description="TraceX pipeline orchestrator (date-anchored).",
    )
    p.add_argument(
        "--as-of-date",
        required=True,
        type=lambda s: _dt.date.fromisoformat(s),
        help="Business date to process (YYYY-MM-DD). Required — same input + "
             "same date → byte-identical output.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    as_of_date: _dt.date = args.as_of_date

    run_id = os.environ.get("TRACEX_RUN_ID") or str(uuid.uuid4())
    os.environ["TRACEX_RUN_ID"] = run_id
    os.environ["TRACEX_AS_OF_DATE"] = as_of_date.isoformat()

    log = configure_logging(run_id, ORCHESTRATOR_NAME)
    started_perf = time.perf_counter()

    db_path = get_db_path()
    log_dir = get_log_dir()
    os.environ["TRACEX_LOG_DIR"] = str(log_dir)
    log_file = str(log_dir / f"{run_id}.jsonl")

    # ── Registry bootstrap (idempotent) + insert this run ─────────────
    git_sha = _registry.resolve_git_sha()
    log.info("git_sha_resolved", git_sha=git_sha)
    try:
        _registry.init_registry()
    except Exception as exc:
        # The registry is best-effort — don't kill the run if the table can't
        # be created (e.g. concurrent writer). Just log and proceed.
        log.warning("run_registry_init_failed", error=str(exc))

    started_at = None
    try:
        started_at = _registry.insert_run(
            run_id=run_id,
            as_of_date=as_of_date,
            stages_planned=STAGES,
            log_file=log_file,
            git_sha=git_sha,
        )
        log.info(
            "run_registry_insert",
            run_id=run_id,
            as_of_date=as_of_date.isoformat(),
            stages_planned=STAGES,
            git_sha=git_sha,
        )
    except Exception as exc:
        log.warning("run_registry_insert_failed", error=str(exc))

    log.info(
        "pipeline_start",
        run_id=run_id,
        as_of_date=as_of_date.isoformat(),
        db_path=str(db_path),
        log_dir=str(log_dir),
        log_file=log_file,
        stages=STAGES,
        git_sha=git_sha,
    )

    stages_dir = PIPELINE_ROOT / "stages"
    env = os.environ.copy()
    failed_stage: str | None = None

    for stage_file in STAGES:
        stage_path = stages_dir / stage_file
        stage_name = stage_path.stem
        t0 = time.perf_counter()
        log.info("orchestrator_stage_dispatch", stage_file=stage_file, stage_name=stage_name)

        # Registry: stage row open BEFORE subprocess (which holds the DB lock).
        stage_started_at = None
        try:
            stage_started_at = _registry.insert_stage(run_id, stage_name)
        except Exception as exc:
            log.warning("run_registry_stage_insert_failed",
                        stage=stage_name, error=str(exc))

        try:
            proc = subprocess.run(
                [sys.executable, str(stage_path)],
                env=env,
                check=False,
            )
        except Exception as exc:
            log.error(
                "orchestrator_stage_exception",
                stage_file=stage_file,
                error=str(exc),
                error_type=type(exc).__name__,
                traceback=traceback.format_exc(),
            )
            if stage_started_at is not None:
                try:
                    _registry.finalize_stage(run_id, stage_name, stage_started_at, exit_code=1)
                except Exception:
                    pass
            failed_stage = stage_name
            break

        duration_ms = int((time.perf_counter() - t0) * 1000)
        if stage_started_at is not None:
            try:
                _registry.finalize_stage(
                    run_id, stage_name, stage_started_at, exit_code=proc.returncode,
                )
            except Exception as exc:
                log.warning("run_registry_stage_finalize_failed",
                            stage=stage_name, error=str(exc))

        if proc.returncode != 0:
            log.error(
                "orchestrator_stage_failed",
                stage_file=stage_file,
                stage_name=stage_name,
                exit_code=proc.returncode,
                duration_ms=duration_ms,
            )
            failed_stage = stage_name
            break

        log.info(
            "orchestrator_stage_ok",
            stage_file=stage_file,
            stage_name=stage_name,
            exit_code=0,
            duration_ms=duration_ms,
        )

    # ── Finalise the run regardless of outcome ─────────────────────────
    final_status = "failed" if failed_stage else "ok"
    if started_at is not None:
        try:
            _registry.finalize_run(run_id, started_at, final_status, failed_stage)
            log.info(
                "run_registry_finalize",
                status=final_status,
                failed_stage=failed_stage,
                duration_ms=int((time.perf_counter() - started_perf) * 1000),
            )
        except Exception as exc:
            log.warning("run_registry_finalize_failed", error=str(exc))

    if failed_stage:
        log.error(
            "pipeline_complete",
            status="failed",
            failed_stage=failed_stage,
            duration_ms=int((time.perf_counter() - started_perf) * 1000),
        )
        return 1

    log.info(
        "pipeline_complete",
        status="ok",
        stages_run=len(STAGES),
        as_of_date=as_of_date.isoformat(),
        duration_ms=int((time.perf_counter() - started_perf) * 1000),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
