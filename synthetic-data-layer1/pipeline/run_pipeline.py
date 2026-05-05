"""Top-level orchestrator: runs every Layer 1 stage as an independent subprocess.

Each stage inherits the orchestrator's TRACEX_RUN_ID via env so that all events for a
single pipeline run land in the same logs/{run_id}.jsonl. Stages execute sequentially;
the orchestrator exits 1 on the first non-zero stage exit (preserving its error log).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parent
LAYER1_ROOT = PIPELINE_ROOT.parent
sys.path.insert(0, str(LAYER1_ROOT))

from pipeline.config import (  # noqa: E402
    configure_logging,
    get_db_path,
    get_log_dir,
)

ORCHESTRATOR_NAME = "run_pipeline"

STAGES = [
    "00_validate_sources.py",
    "01_stg_fx_normalize.py",
    "02_stg_transactions.py",
    "03_stg_customers.py",
    "10_fct_risk_profile.py",
    "11_fct_sar_candidates.py",
    "99_validate_outputs.py",
]


def main() -> int:
    run_id = os.environ.get("TRACEX_RUN_ID") or str(uuid.uuid4())
    os.environ["TRACEX_RUN_ID"] = run_id
    log = configure_logging(run_id, ORCHESTRATOR_NAME)
    started = time.perf_counter()

    db_path = get_db_path()
    log_dir = get_log_dir()
    os.environ["TRACEX_LOG_DIR"] = str(log_dir)

    log.info(
        "pipeline_start",
        run_id=run_id,
        db_path=str(db_path),
        log_dir=str(log_dir),
        log_file=str(log_dir / f"{run_id}.jsonl"),
        stages=STAGES,
    )

    stages_dir = PIPELINE_ROOT / "stages"
    env = os.environ.copy()

    for stage_file in STAGES:
        stage_path = stages_dir / stage_file
        stage_name = stage_path.stem
        t0 = time.perf_counter()
        log.info("orchestrator_stage_dispatch", stage_file=stage_file, stage_name=stage_name)

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
            return 1

        duration_ms = int((time.perf_counter() - t0) * 1000)
        if proc.returncode != 0:
            log.error(
                "orchestrator_stage_failed",
                stage_file=stage_file,
                stage_name=stage_name,
                exit_code=proc.returncode,
                duration_ms=duration_ms,
            )
            log.error(
                "pipeline_complete",
                status="failed",
                failed_stage=stage_name,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return 1

        log.info(
            "orchestrator_stage_ok",
            stage_file=stage_file,
            stage_name=stage_name,
            exit_code=0,
            duration_ms=duration_ms,
        )

    log.info(
        "pipeline_complete",
        status="ok",
        stages_run=len(STAGES),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
