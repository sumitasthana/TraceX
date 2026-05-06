"""Stage 99 — gate the run with data-quality checks on Layer 1 + Layer 2 outputs.

Each check emits a `data_quality_check` event with check_name, expected, actual,
rows_checked, rows_failed, and passed. The stage exits non-zero if any check fails —
that turns this script into a hard merge gate when wired into Airflow / cron.

L1 checks preserved verbatim from the prior version (10 checks, names unchanged).
L2 adds checks L2_DQ_01..L2_DQ_05 for fct_customer_risk_profile.
"""
from __future__ import annotations

import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.config import (  # noqa: E402
    configure_logging,
    db_connect,
    get_as_of_date,
    get_run_id,
    table_row_count,
)

STAGE_NAME = Path(__file__).stem


@dataclass
class CheckResult:
    name: str
    expected: object
    actual: object
    rows_checked: int
    rows_failed: int
    passed: bool


def _scalar(con, sql: str) -> int:
    (n,) = con.execute(sql).fetchone()
    return int(n or 0)


def _build_l1_checks(con) -> list[Callable[[], CheckResult]]:
    txn_total = table_row_count(con, "stg_transaction_normalized")
    src_txn_total = table_row_count(con, "src_transaction")
    cust_total = table_row_count(con, "stg_customer_enriched")
    src_cust_total = table_row_count(con, "src_customer")

    def check_txn_amount_usd_not_null() -> CheckResult:
        bad = _scalar(con, "SELECT COUNT(*) FROM stg_transaction_normalized WHERE amount_usd IS NULL")
        return CheckResult(
            "stg_txn_amount_usd_not_null", expected=0, actual=bad,
            rows_checked=txn_total, rows_failed=bad, passed=bad == 0,
        )

    def check_txn_net_amount_usd_not_null() -> CheckResult:
        bad = _scalar(con, "SELECT COUNT(*) FROM stg_transaction_normalized WHERE net_amount_usd IS NULL")
        return CheckResult(
            "stg_txn_net_amount_usd_not_null", expected=0, actual=bad,
            rows_checked=txn_total, rows_failed=bad, passed=bad == 0,
        )

    def check_txn_amount_usd_nonneg() -> CheckResult:
        bad = _scalar(con, "SELECT COUNT(*) FROM stg_transaction_normalized WHERE amount_usd < 0")
        return CheckResult(
            "stg_txn_amount_usd_nonneg", expected=0, actual=bad,
            rows_checked=txn_total, rows_failed=bad, passed=bad == 0,
        )

    def check_txn_reversal_zero() -> CheckResult:
        bad = _scalar(
            con,
            "SELECT COUNT(*) FROM stg_transaction_normalized "
            "WHERE is_reversal = TRUE AND net_amount_usd <> 0",
        )
        return CheckResult(
            "stg_txn_reversal_net_zero", expected=0, actual=bad,
            rows_checked=txn_total, rows_failed=bad, passed=bad == 0,
        )

    def check_txn_no_silent_drops() -> CheckResult:
        passed = txn_total >= src_txn_total
        return CheckResult(
            "stg_txn_no_silent_drops",
            expected=f">= {src_txn_total}", actual=txn_total,
            rows_checked=src_txn_total,
            rows_failed=max(0, src_txn_total - txn_total),
            passed=passed,
        )

    def check_cust_full_name_not_null() -> CheckResult:
        bad = _scalar(con, "SELECT COUNT(*) FROM stg_customer_enriched WHERE full_name IS NULL")
        return CheckResult(
            "stg_cust_full_name_not_null", expected=0, actual=bad,
            rows_checked=cust_total, rows_failed=bad, passed=bad == 0,
        )

    def check_cust_age_not_null() -> CheckResult:
        bad = _scalar(con, "SELECT COUNT(*) FROM stg_customer_enriched WHERE age IS NULL")
        return CheckResult(
            "stg_cust_age_not_null", expected=0, actual=bad,
            rows_checked=cust_total, rows_failed=bad, passed=bad == 0,
        )

    def check_cust_branch_region_not_null() -> CheckResult:
        bad = _scalar(con, "SELECT COUNT(*) FROM stg_customer_enriched WHERE branch_region IS NULL")
        return CheckResult(
            "stg_cust_branch_region_not_null", expected=0, actual=bad,
            rows_checked=cust_total, rows_failed=bad, passed=bad == 0,
        )

    def check_cust_age_range() -> CheckResult:
        bad = _scalar(
            con,
            "SELECT COUNT(*) FROM stg_customer_enriched WHERE age < 18 OR age > 120",
        )
        return CheckResult(
            "stg_cust_age_18_120", expected=0, actual=bad,
            rows_checked=cust_total, rows_failed=bad, passed=bad == 0,
        )

    def check_cust_one_to_one() -> CheckResult:
        passed = cust_total == src_cust_total
        return CheckResult(
            "stg_cust_one_to_one_with_src",
            expected=src_cust_total, actual=cust_total,
            rows_checked=src_cust_total,
            rows_failed=abs(src_cust_total - cust_total),
            passed=passed,
        )

    return [
        check_txn_amount_usd_not_null,
        check_txn_net_amount_usd_not_null,
        check_txn_amount_usd_nonneg,
        check_txn_reversal_zero,
        check_txn_no_silent_drops,
        check_cust_full_name_not_null,
        check_cust_age_not_null,
        check_cust_branch_region_not_null,
        check_cust_age_range,
        check_cust_one_to_one,
    ]


def _build_l2_checks(con) -> list[Callable[[], CheckResult]]:
    # All L2 checks scope to the current run's as_of_date partition. The fact
    # table is now historised; counting / aggregating across every partition
    # would silently break L2_DQ_03 (1-row-per-customer) on the second run.
    aod = get_as_of_date().isoformat()
    partition_clause = f"as_of_date = DATE '{aod}'"
    rp_total = _scalar(
        con,
        f"SELECT COUNT(*) FROM fct_customer_risk_profile WHERE {partition_clause}",
    )
    src_cust_total = table_row_count(con, "src_customer")

    # ---- fct_customer_risk_profile (partition: as_of_date) -----------------

    def l2_dq_01_risk_score_range() -> CheckResult:
        bad = _scalar(
            con,
            f"SELECT COUNT(*) FROM fct_customer_risk_profile "
            f"WHERE {partition_clause} AND (risk_score < 0.0 OR risk_score > 1.0)",
        )
        return CheckResult(
            "L2_DQ_01", expected="risk_score in [0,1]", actual=f"{bad} rows out of range",
            rows_checked=rp_total, rows_failed=bad, passed=bad == 0,
        )

    def l2_dq_02_no_nulls() -> CheckResult:
        bad = _scalar(
            con,
            f"SELECT COUNT(*) FROM fct_customer_risk_profile "
            f"WHERE {partition_clause} AND "
            f"(risk_score IS NULL OR risk_tier IS NULL OR volume_percentile IS NULL)",
        )
        return CheckResult(
            "L2_DQ_02",
            expected="0 NULLs in (risk_score, risk_tier, volume_percentile)",
            actual=f"{bad} NULL rows",
            rows_checked=rp_total, rows_failed=bad, passed=bad == 0,
        )

    def l2_dq_03_one_per_customer() -> CheckResult:
        passed = rp_total == src_cust_total
        return CheckResult(
            "L2_DQ_03",
            expected=src_cust_total, actual=rp_total,
            rows_checked=src_cust_total,
            rows_failed=abs(src_cust_total - rp_total),
            passed=passed,
        )

    def l2_dq_04_three_tiers() -> CheckResult:
        rows = con.execute(
            f"SELECT DISTINCT risk_tier FROM fct_customer_risk_profile "
            f"WHERE {partition_clause} ORDER BY 1"
        ).fetchall()
        tiers = sorted(r[0] for r in rows)
        expected = ["HIGH", "LOW", "MEDIUM"]
        passed = tiers == expected
        return CheckResult(
            "L2_DQ_04",
            expected=expected, actual=tiers,
            rows_checked=rp_total,
            rows_failed=0 if passed else 1,
            passed=passed,
        )

    def l2_dq_05_volume_nonneg() -> CheckResult:
        bad = _scalar(
            con,
            f"SELECT COUNT(*) FROM fct_customer_risk_profile "
            f"WHERE {partition_clause} AND total_txn_volume_usd_90d < 0",
        )
        return CheckResult(
            "L2_DQ_05",
            expected="total_txn_volume_usd_90d >= 0",
            actual=f"{bad} negative rows",
            rows_checked=rp_total, rows_failed=bad, passed=bad == 0,
        )

    return [
        l2_dq_01_risk_score_range,
        l2_dq_02_no_nulls,
        l2_dq_03_one_per_customer,
        l2_dq_04_three_tiers,
        l2_dq_05_volume_nonneg,
    ]


def main() -> int:
    run_id = get_run_id()
    log = configure_logging(run_id, STAGE_NAME)
    started = time.perf_counter()

    try:
        with db_connect(read_only=True) as con:
            log.info(
                "stage_start",
                input_tables=[
                    "stg_transaction_normalized",
                    "stg_customer_enriched",
                    "fct_customer_risk_profile",
                ],
            )

            checks = _build_l1_checks(con) + _build_l2_checks(con)
            results: list[CheckResult] = []
            for fn in checks:
                r = fn()
                results.append(r)
                event = log.info if r.passed else log.error
                event(
                    "data_quality_check",
                    check_name=r.name,
                    expected=r.expected,
                    actual=r.actual,
                    rows_checked=r.rows_checked,
                    rows_failed=r.rows_failed,
                    passed=r.passed,
                )

            failed = [r.name for r in results if not r.passed]
            duration_ms = int((time.perf_counter() - started) * 1000)
            if failed:
                log.error(
                    "stage_complete",
                    status="failed",
                    checks_total=len(results),
                    checks_failed=len(failed),
                    failed_checks=failed,
                    duration_ms=duration_ms,
                )
                return 1

            log.info(
                "stage_complete",
                status="ok",
                checks_total=len(results),
                checks_failed=0,
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
