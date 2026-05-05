"""Stage 10 — produce fct_customer_risk_profile.

One row per customer. Every transactional metric is anchored to MAX(txn_date) from
stg_transaction_normalized so results are deterministic on static synthetic data.
volume_percentile is computed in a second pass over the per-customer aggregation
(PERCENT_RANK can only be applied after the rollup, never inside it).

Customers with zero transactions are preserved via LEFT JOIN; their ratios collapse
to 0 because the LEFT-JOIN ghost row's NULL columns fall out of the FILTER clauses
and we count txns via COUNT(ct.txn_id) (which is 0 when there is no match).
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
OUTPUT_TABLE = "fct_customer_risk_profile"

# Lineage manifest — Option B. This is documentation as much as it is a log event;
# every key is a literal so it can be diffed in source control alongside the SQL.
LINEAGE_MANIFEST: dict = {
    "target_table": "fct_customer_risk_profile",
    "source_tables": [
        "stg_transaction_normalized",
        "stg_customer_enriched",
        "src_account",
    ],
    "depends_on_stages": ["02_stg_transactions", "03_stg_customers"],
    "transform_type": "AGGREGATE_JOIN",
    "derived_columns": {
        "total_txn_volume_usd_90d":
            "COALESCE(SUM(net_amount_usd) FILTER ("
            "txn_date >= reference_date - INTERVAL 90 DAY), 0)",
        "txn_count_90d":
            "COUNT(*) FILTER (NOT is_reversal "
            "AND txn_date >= reference_date - INTERVAL 90 DAY)",
        "international_txn_ratio":
            "CASE WHEN COUNT(txn_id)=0 THEN 0 "
            "ELSE COUNT(*) FILTER (is_international) / COUNT(txn_id) END",
        "avg_txn_amount_usd":
            "COALESCE(AVG(net_amount_usd) FILTER (NOT is_reversal), 0)",
        "reversal_rate":
            "CASE WHEN COUNT(txn_id)=0 THEN 0 "
            "ELSE COUNT(*) FILTER (is_reversal) / COUNT(txn_id) END",
        "volume_percentile":
            "PERCENT_RANK() OVER (ORDER BY total_txn_volume_usd_90d) "
            "(second pass, after per-customer aggregation)",
        "risk_score":
            "0.4*international_txn_ratio "
            "+ 0.3*(CASE WHEN kyc_stale_flag THEN 1.0 ELSE 0.0 END) "
            "+ 0.2*volume_percentile "
            "+ 0.1*reversal_rate",
        "risk_tier":
            "'HIGH' if risk_score > 0.65 else "
            "'MEDIUM' if risk_score > 0.35 else 'LOW'",
    },
}

TRANSFORM_SQL = f"""
CREATE OR REPLACE TABLE {OUTPUT_TABLE} AS
WITH ref AS (
    SELECT MAX(txn_date) AS reference_date FROM stg_transaction_normalized
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

TIER_DISTRIBUTION_SQL = f"""
SELECT
    SUM(CASE WHEN risk_tier = 'HIGH'   THEN 1 ELSE 0 END) AS high_n,
    SUM(CASE WHEN risk_tier = 'MEDIUM' THEN 1 ELSE 0 END) AS medium_n,
    SUM(CASE WHEN risk_tier = 'LOW'    THEN 1 ELSE 0 END) AS low_n,
    COUNT(*)                                              AS total_n
FROM {OUTPUT_TABLE}
"""

REF_DATE_SQL = f"SELECT MIN(reference_date) FROM {OUTPUT_TABLE}"

HIGH_RISK_THRESHOLD = 0.30  # warn if > 30% of customers land in HIGH


def main() -> int:
    run_id = get_run_id()
    log = configure_logging(run_id, STAGE_NAME)
    started = time.perf_counter()

    try:
        with db_connect() as con:
            txn_rows = table_row_count(con, "stg_transaction_normalized")
            cust_rows = table_row_count(con, "stg_customer_enriched")
            acct_rows = table_row_count(con, "src_account")
            log.info(
                "stage_start",
                input_tables=LINEAGE_MANIFEST["source_tables"],
                stg_transaction_normalized_rows=txn_rows,
                stg_customer_enriched_rows=cust_rows,
                src_account_rows=acct_rows,
                output_table=OUTPUT_TABLE,
            )

            log.info("transform_start", **LINEAGE_MANIFEST, sql=TRANSFORM_SQL.strip())
            t0 = time.perf_counter()
            con.execute(TRANSFORM_SQL)
            output_rows = table_row_count(con, OUTPUT_TABLE)
            log.info(
                "transform_complete",
                output_row_count=output_rows,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

            (ref_date,) = con.execute(REF_DATE_SQL).fetchone()
            log.info("reference_date_resolved", reference_date=str(ref_date))

            high_n, medium_n, low_n, total_n = con.execute(TIER_DISTRIBUTION_SQL).fetchone()
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
