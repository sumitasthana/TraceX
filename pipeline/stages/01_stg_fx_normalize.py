"""Stage 01 — build stg_fx_resolved, the canonical FX-to-USD lookup used by stg_transactions.

We keep only to_currency='USD' rows from src_fx_rate (transactions normalize to USD).
Downstream consumers should ASOF-join on (from_currency, rate_date) to pick the most
recent rate at-or-before the txn_date. USD itself is short-circuited in the consumer
(rate=1.0) so it does not need to live in this table.
"""
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
    table_row_count,
)

STAGE_NAME = Path(__file__).stem
INPUT_TABLE = "src_fx_rate"
OUTPUT_TABLE = "stg_fx_resolved"

TRANSFORM_SQL = f"""
CREATE OR REPLACE TABLE {OUTPUT_TABLE} AS
SELECT
    rate_date,
    from_currency,
    to_currency,
    rate,
    rate_source
FROM {INPUT_TABLE}
WHERE to_currency = 'USD'
ORDER BY from_currency, rate_date
"""

COVERAGE_SQL = f"""
SELECT
    from_currency,
    COUNT(*)              AS rate_rows,
    MIN(rate_date)        AS first_date,
    MAX(rate_date)        AS last_date
FROM {OUTPUT_TABLE}
GROUP BY from_currency
ORDER BY from_currency
"""


def main() -> int:
    run_id = get_run_id()
    log = configure_logging(run_id, STAGE_NAME)
    started = time.perf_counter()

    try:
        with db_connect() as con:
            input_rows = table_row_count(con, INPUT_TABLE)
            log.info(
                "stage_start",
                input_table=INPUT_TABLE,
                input_row_count=input_rows,
                output_table=OUTPUT_TABLE,
            )

            log.info("transform_start", sql=TRANSFORM_SQL.strip())
            t0 = time.perf_counter()
            con.execute(TRANSFORM_SQL)
            output_rows = table_row_count(con, OUTPUT_TABLE)
            transform_ms = int((time.perf_counter() - t0) * 1000)
            log.info(
                "transform_complete",
                output_row_count=output_rows,
                duration_ms=transform_ms,
            )

            # Deep column-lineage hook — never raises (manifest_builder swallows).
            try:
                from lineage.manifest_builder import build_and_ingest  # noqa: E402
                build_and_ingest(
                    stage=STAGE_NAME,
                    run_id=run_id,
                    sql=TRANSFORM_SQL,
                    target_table=OUTPUT_TABLE,
                    source_tables=["src_fx_rate"],
                    depends_on_stages=[],
                    transform_type="FILTER",
                    output_row_count=output_rows,
                    duration_ms=transform_ms,
                    con=con,
                )
            except Exception as exc:  # defence in depth
                log.warning(
                    "lineage_manifest_invoke_failed",
                    stage=STAGE_NAME,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            for row in con.execute(COVERAGE_SQL).fetchall():
                from_ccy, rate_rows, first_date, last_date = row
                log.info(
                    "fx_currency_coverage",
                    from_currency=from_ccy,
                    rate_rows=int(rate_rows),
                    first_date=str(first_date),
                    last_date=str(last_date),
                )

            log.info(
                "data_quality_check",
                check_name="stg_fx_resolved_nonempty",
                rows_checked=output_rows,
                rows_failed=0 if output_rows > 0 else 1,
                passed=output_rows > 0,
            )

        log.info(
            "stage_complete",
            output_table=OUTPUT_TABLE,
            output_row_count=output_rows,
            duration_ms=int((time.perf_counter() - started) * 1000),
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
