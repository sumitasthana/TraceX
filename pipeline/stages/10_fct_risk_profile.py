"""Stage 10 — produce fct_customer_risk_profile (historised by as_of_date).

One row per (customer_id, as_of_date). Re-running for the same business date
overwrites just that partition; running for a new date appends. The 90-day
transactional metrics are anchored to LEAST(MAX(txn_date), as_of_date) so an
earlier-date re-run can't leak future txns into the windows.

`computed_at` stays as wall-clock CURRENT_TIMESTAMP — it is metadata describing
when this row was produced, not a join key, and stripping it from the output
would lose useful provenance. The two-key PK (customer_id, as_of_date) plus a
DELETE-then-INSERT inside one connection means the table is byte-identical for
the partition on re-runs.

Customers with zero transactions are preserved via LEFT JOIN; their ratios
collapse to 0 because the LEFT-JOIN ghost row's NULL columns fall out of the
FILTER clauses and we count txns via COUNT(ct.txn_id).
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
    get_as_of_date,
    get_run_id,
    table_row_count,
)

STAGE_NAME = Path(__file__).stem
OUTPUT_TABLE = "fct_customer_risk_profile"

# Source tables and stage dependencies are now consumed by manifest_builder
# directly from the call site below — no hand-written lineage manifest dict.
SOURCE_TABLES = [
    "stg_transaction_normalized",
    "stg_customer_enriched",
    "src_account",
]
DEPENDS_ON_STAGES = ["02_stg_transactions", "03_stg_customers"]

# DDL is idempotent; a fresh DB starts with this shape, an existing DB
# has it added on first run after the migration. Schema is identical to
# the historic CREATE OR REPLACE output PLUS an `as_of_date` partition key.
CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {OUTPUT_TABLE} (
    customer_id              VARCHAR NOT NULL,
    as_of_date               DATE    NOT NULL,
    full_name                VARCHAR,
    age                      INTEGER,
    is_us_person             BOOLEAN,
    kyc_status               VARCHAR,
    kyc_stale_flag           BOOLEAN,
    branch_region            VARCHAR,
    total_txn_volume_usd_90d DOUBLE,
    txn_count_90d            BIGINT,
    international_txn_ratio  DOUBLE,
    avg_txn_amount_usd       DOUBLE,
    reversal_rate            DOUBLE,
    volume_percentile        DOUBLE,
    risk_score               DOUBLE,
    risk_tier                VARCHAR,
    reference_date           DATE,
    computed_at              TIMESTAMP,
    CONSTRAINT pk_fct_risk PRIMARY KEY (customer_id, as_of_date)
)
"""


def build_transform_sql(as_of_date) -> str:
    """SELECT body for the partition INSERT. `reference_date` is clamped to
    `as_of_date` so the 90-day window is stable across re-runs of any prior
    date — no temporal leak when back-filling."""
    aod = as_of_date.isoformat()
    return f"""
WITH ref AS (
    SELECT LEAST(MAX(txn_date), DATE '{aod}') AS reference_date
    FROM stg_transaction_normalized
),
customer_txns AS (
    SELECT
        a.customer_id,
        t.txn_id,
        t.txn_date,
        t.is_reversal,
        t.is_international,
        t.net_amount_usd
    FROM stg_transaction_normalized t
    JOIN src_account a ON a.account_id = t.account_id
),
per_customer AS (
    SELECT
        c.customer_id,
        c.full_name,
        c.age,
        c.is_us_person,
        c.kyc_status,
        c.kyc_stale_flag,
        c.branch_region,
        r.reference_date,
        COUNT(ct.txn_id)                                                AS total_txn_count,
        COUNT(*) FILTER (WHERE ct.is_international)                     AS intl_txn_count,
        COUNT(*) FILTER (WHERE ct.is_reversal)                          AS reversal_txn_count,
        COUNT(*) FILTER (
            WHERE ct.is_reversal = FALSE
              AND ct.txn_date >= r.reference_date - INTERVAL 90 DAY
        )                                                               AS txn_count_90d,
        COALESCE(SUM(ct.net_amount_usd) FILTER (
            WHERE ct.txn_date >= r.reference_date - INTERVAL 90 DAY
        ), 0)                                                           AS total_txn_volume_usd_90d,
        AVG(ct.net_amount_usd) FILTER (WHERE ct.is_reversal = FALSE)    AS avg_excl_reversal
    FROM stg_customer_enriched c
    CROSS JOIN ref r
    LEFT JOIN customer_txns ct ON ct.customer_id = c.customer_id
    GROUP BY
        c.customer_id, c.full_name, c.age, c.is_us_person, c.kyc_status,
        c.kyc_stale_flag, c.branch_region, r.reference_date
),
with_ratios AS (
    SELECT
        customer_id,
        full_name,
        age,
        is_us_person,
        kyc_status,
        kyc_stale_flag,
        branch_region,
        reference_date,
        total_txn_volume_usd_90d,
        txn_count_90d,
        CASE WHEN total_txn_count = 0 THEN 0.0
             ELSE intl_txn_count::DOUBLE / total_txn_count END     AS international_txn_ratio,
        COALESCE(avg_excl_reversal, 0.0)                           AS avg_txn_amount_usd,
        CASE WHEN total_txn_count = 0 THEN 0.0
             ELSE reversal_txn_count::DOUBLE / total_txn_count END AS reversal_rate
    FROM per_customer
),
with_pct AS (
    SELECT
        *,
        PERCENT_RANK() OVER (ORDER BY total_txn_volume_usd_90d) AS volume_percentile
    FROM with_ratios
),
scored AS (
    SELECT
        *,
        (0.4 * international_txn_ratio)
        + (0.3 * CASE WHEN kyc_stale_flag THEN 1.0 ELSE 0.0 END)
        + (0.2 * volume_percentile)
        + (0.1 * reversal_rate) AS risk_score
    FROM with_pct
)
SELECT
    customer_id,
    DATE '{aod}' AS as_of_date,
    full_name,
    age,
    is_us_person,
    kyc_status,
    kyc_stale_flag,
    branch_region,
    total_txn_volume_usd_90d,
    txn_count_90d,
    international_txn_ratio,
    avg_txn_amount_usd,
    reversal_rate,
    volume_percentile,
    risk_score,
    CASE
        WHEN risk_score > 0.65 THEN 'HIGH'
        WHEN risk_score > 0.35 THEN 'MEDIUM'
        ELSE 'LOW'
    END AS risk_tier,
    reference_date,
    CURRENT_TIMESTAMP AS computed_at
FROM scored
"""

HIGH_RISK_THRESHOLD = 0.30  # warn if > 30% of customers land in HIGH


def main() -> int:
    run_id = get_run_id()
    log = configure_logging(run_id, STAGE_NAME)
    started = time.perf_counter()
    as_of_date = get_as_of_date()
    aod = as_of_date.isoformat()
    transform_sql = build_transform_sql(as_of_date)

    # Wrapper that gives the lineage parser a CREATE OR REPLACE TABLE shape
    # to parse, even though we actually execute as DELETE + INSERT below.
    lineage_sql = f"CREATE OR REPLACE TABLE {OUTPUT_TABLE} AS {transform_sql}"

    try:
        with db_connect() as con:
            txn_rows = table_row_count(con, "stg_transaction_normalized")
            cust_rows = table_row_count(con, "stg_customer_enriched")
            acct_rows = table_row_count(con, "src_account")
            log.info(
                "stage_start",
                input_tables=SOURCE_TABLES,
                stg_transaction_normalized_rows=txn_rows,
                stg_customer_enriched_rows=cust_rows,
                src_account_rows=acct_rows,
                output_table=OUTPUT_TABLE,
                as_of_date_resolved=aod,
            )

            # Ensure the historised table exists with the right shape.
            # If a legacy version of the table exists (pre-historisation,
            # no `as_of_date` column), drop it so CREATE TABLE rebuilds
            # with the partition key. This makes the schema migration
            # idempotent across any number of re-runs.
            existing_cols = {
                r[0] for r in con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = ?",
                    [OUTPUT_TABLE],
                ).fetchall()
            }
            if existing_cols and "as_of_date" not in existing_cols:
                log.warning(
                    "fct_legacy_schema_dropped",
                    table=OUTPUT_TABLE,
                    reason="no as_of_date column; pre-historisation shape",
                )
                con.execute(f"DROP TABLE {OUTPUT_TABLE}")
            con.execute(CREATE_TABLE_SQL)

            log.info("transform_start", sql=transform_sql.strip())
            t0 = time.perf_counter()
            # Partition overwrite: delete this as_of_date's slice, then
            # re-INSERT it. Other partitions are left intact.
            con.execute(
                f"DELETE FROM {OUTPUT_TABLE} WHERE as_of_date = DATE '{aod}'"
            )
            con.execute(f"INSERT INTO {OUTPUT_TABLE} {transform_sql}")
            output_rows = int(con.execute(
                f"SELECT COUNT(*) FROM {OUTPUT_TABLE} WHERE as_of_date = DATE '{aod}'"
            ).fetchone()[0])
            transform_ms = int((time.perf_counter() - t0) * 1000)
            log.info(
                "transform_complete",
                output_row_count=output_rows,
                duration_ms=transform_ms,
                partition_as_of_date=aod,
            )

            try:
                from lineage.manifest_builder import build_and_ingest  # noqa: E402
                build_and_ingest(
                    stage=STAGE_NAME,
                    run_id=run_id,
                    sql=lineage_sql,
                    target_table=OUTPUT_TABLE,
                    source_tables=SOURCE_TABLES,
                    depends_on_stages=DEPENDS_ON_STAGES,
                    transform_type="AGGREGATE_JOIN",
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

            (ref_date,) = con.execute(
                f"SELECT MIN(reference_date) FROM {OUTPUT_TABLE} WHERE as_of_date = DATE '{aod}'"
            ).fetchone()
            log.info("reference_date_resolved", reference_date=str(ref_date), as_of_date=aod)

            high_n, medium_n, low_n, total_n = con.execute(
                f"""
                SELECT
                    SUM(CASE WHEN risk_tier = 'HIGH'   THEN 1 ELSE 0 END) AS high_n,
                    SUM(CASE WHEN risk_tier = 'MEDIUM' THEN 1 ELSE 0 END) AS medium_n,
                    SUM(CASE WHEN risk_tier = 'LOW'    THEN 1 ELSE 0 END) AS low_n,
                    COUNT(*)                                              AS total_n
                FROM {OUTPUT_TABLE}
                WHERE as_of_date = DATE '{aod}'
                """,
            ).fetchone()
            high_n = int(high_n or 0)
            medium_n = int(medium_n or 0)
            low_n = int(low_n or 0)
            total_n = int(total_n or 0)
            pct_high = (high_n / total_n) if total_n else 0.0
            log.info(
                "risk_tier_distribution",
                HIGH=high_n,
                MEDIUM=medium_n,
                LOW=low_n,
                pct_high=round(pct_high, 6),
            )
            if pct_high > HIGH_RISK_THRESHOLD:
                log.warning(
                    "high_risk_concentration_warning",
                    pct_high=round(pct_high, 6),
                    threshold=HIGH_RISK_THRESHOLD,
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
