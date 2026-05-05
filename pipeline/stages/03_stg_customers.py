"""Stage 03 — produce stg_customer_enriched.

Adds derived customer attributes (full_name, age, is_us_person, KYC freshness) and
joins src_branch.region for downstream segmentation. We log a KYC-stale warning if
more than 20% of the population has stale or expired KYC — that threshold mirrors
how compliance teams typically wake up to a remediation backlog.
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
INPUT_TABLE = "src_customer"
BRANCH_TABLE = "src_branch"
OUTPUT_TABLE = "stg_customer_enriched"

KYC_STALE_WARN_THRESHOLD = 0.20  # 20%

TRANSFORM_SQL = f"""
CREATE OR REPLACE TABLE {OUTPUT_TABLE} AS
SELECT
    c.customer_id,
    c.kyc_status,
    c.kyc_reviewed_at,
    c.onboarded_date,
    c.branch_id,
    c.first_name || ' ' || c.last_name                    AS full_name,
    CAST(EXTRACT(YEAR FROM AGE(CURRENT_DATE, c.dob)) AS INTEGER) AS age,
    (c.citizenship = 'US' OR c.country_of_birth = 'US')   AS is_us_person,
    CASE
        WHEN c.kyc_reviewed_at IS NULL THEN NULL
        ELSE DATE_DIFF('day', CAST(c.kyc_reviewed_at AS DATE), CURRENT_DATE)
    END                                                   AS kyc_days_since_review,
    (
        (c.kyc_reviewed_at IS NOT NULL
            AND DATE_DIFF('day', CAST(c.kyc_reviewed_at AS DATE), CURRENT_DATE) > 365)
        OR c.kyc_status = 'EXPIRED'
    )                                                     AS kyc_stale_flag,
    b.region                                              AS branch_region
FROM {INPUT_TABLE} c
LEFT JOIN {BRANCH_TABLE} b USING (branch_id)
"""

KYC_STATS_SQL = f"""
SELECT
    SUM(CASE WHEN kyc_stale_flag THEN 1 ELSE 0 END) AS stale_count,
    COUNT(*)                                        AS total_count
FROM {OUTPUT_TABLE}
"""

KYC_BREAKDOWN_SQL = f"""
SELECT kyc_status, COUNT(*) AS n
FROM {OUTPUT_TABLE}
GROUP BY kyc_status
ORDER BY n DESC
"""

UNMAPPED_BRANCH_SQL = f"""
SELECT COUNT(*) FROM {OUTPUT_TABLE} WHERE branch_region IS NULL
"""


def main() -> int:
    run_id = get_run_id()
    log = configure_logging(run_id, STAGE_NAME)
    started = time.perf_counter()

    try:
        with db_connect() as con:
            input_rows = table_row_count(con, INPUT_TABLE)
            branch_rows = table_row_count(con, BRANCH_TABLE)
            log.info(
                "stage_start",
                input_table=INPUT_TABLE,
                input_row_count=input_rows,
                branch_table=BRANCH_TABLE,
                branch_row_count=branch_rows,
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

            try:
                from lineage.manifest_builder import build_and_ingest  # noqa: E402
                build_and_ingest(
                    stage=STAGE_NAME,
                    run_id=run_id,
                    sql=TRANSFORM_SQL,
                    target_table=OUTPUT_TABLE,
                    source_tables=["src_customer", "src_branch"],
                    depends_on_stages=[],
                    transform_type="TRANSFORM_JOIN",
                    output_row_count=output_rows,
                    duration_ms=transform_ms,
                    con=con,
                )
            except Exception as exc:
                log.warning(
                    "lineage_manifest_invoke_failed",
                    stage=STAGE_NAME,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            # KYC freshness summary
            stale_count, total_count = con.execute(KYC_STATS_SQL).fetchone()
            stale_count = int(stale_count or 0)
            total_count = int(total_count or 0)
            stale_ratio = (stale_count / total_count) if total_count else 0.0
            kyc_event = {
                "stale_count": stale_count,
                "total_count": total_count,
                "stale_pct": round(stale_ratio * 100, 4),
                "threshold_pct": round(KYC_STALE_WARN_THRESHOLD * 100, 4),
            }
            if stale_ratio > KYC_STALE_WARN_THRESHOLD:
                log.warning("kyc_stale_summary", **kyc_event)
            else:
                log.info("kyc_stale_summary", **kyc_event)

            for status, n in con.execute(KYC_BREAKDOWN_SQL).fetchall():
                log.info("kyc_status_breakdown", kyc_status=status, count=int(n))

            # Branch mapping coverage — surfaces orphan customers early
            (unmapped,) = con.execute(UNMAPPED_BRANCH_SQL).fetchone()
            unmapped = int(unmapped)
            log.info(
                "data_quality_check",
                check_name="branch_region_mapping",
                rows_checked=output_rows,
                rows_failed=unmapped,
                passed=unmapped == 0,
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
