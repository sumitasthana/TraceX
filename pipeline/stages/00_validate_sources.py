"""Stage 00 — fail-fast precondition check on Layer 0 source tables."""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.config import (  # noqa: E402
    configure_logging,
    db_connect,
    get_run_id,
    table_exists,
    table_row_count,
)

STAGE_NAME = Path(__file__).stem  # "00_validate_sources"

EXPECTED_SOURCES = [
    "src_branch",
    "src_customer",
    "src_account",
    "src_transaction",
    "src_fx_rate",
]


def main() -> int:
    run_id = get_run_id()
    log = configure_logging(run_id, STAGE_NAME)
    started = time.perf_counter()

    log.info("stage_start", expected_tables=EXPECTED_SOURCES)

    failures: list[str] = []
    try:
        with db_connect(read_only=True) as con:
            for table in EXPECTED_SOURCES:
                exists = table_exists(con, table)
                if not exists:
                    log.error(
                        "data_quality_check",
                        check_name=f"{table}_exists",
                        rows_checked=0,
                        rows_failed=1,
                        passed=False,
                        detail="table missing",
                    )
                    failures.append(f"{table} missing")
                    continue

                rows = table_row_count(con, table)
                passed = rows > 0
                log.info(
                    "data_quality_check",
                    check_name=f"{table}_nonempty",
                    rows_checked=rows,
                    rows_failed=0 if passed else 1,
                    passed=passed,
                )
                if not passed:
                    failures.append(f"{table} empty")

        duration_ms = int((time.perf_counter() - started) * 1000)
        if failures:
            log.error(
                "stage_complete",
                status="failed",
                failures=failures,
                duration_ms=duration_ms,
            )
            return 1

        log.info(
            "stage_complete",
            status="ok",
            tables_validated=len(EXPECTED_SOURCES),
            duration_ms=duration_ms,
        )
        return 0
    except Exception as exc:
        log.error(
            "stage_exception",
            error=str(exc),
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )
        raise


if __name__ == "__main__":
    sys.exit(main())
