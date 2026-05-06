"""Idempotency tests for the date-anchored pipeline.

The orchestrator MUST produce byte-identical Layer 1 / Layer 2 output for the
same `as_of_date` regardless of when the run executes. We verify this by
hashing every relevant table after two consecutive runs.
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _hash_table(con, table: str, partition_clause: str = "") -> str:
    """Stable row-fingerprint: GROUP BY all columns and sum md5s. Order-independent.

    Excludes `computed_at` because that column is intentionally wall-clock
    (metadata describing when the row was produced, not a join key per
    pipeline/README.md). Idempotency is checked on the data, not the timestamp."""
    cols = [r[0] for r in con.execute(
        f"SELECT column_name FROM information_schema.columns "
        f"WHERE table_name = '{table}' AND column_name <> 'computed_at' "
        f"ORDER BY ordinal_position"
    ).fetchall()]
    if not cols:
        return ""
    expr = "md5(" + " || '|' || ".join(f"COALESCE(CAST({c} AS VARCHAR), '_NULL_')" for c in cols) + ")"
    where = f"WHERE {partition_clause}" if partition_clause else ""
    sql = f"SELECT md5(string_agg({expr}, '\\n' ORDER BY {expr})) FROM {table} {where}"
    row = con.execute(sql).fetchone()
    return row[0] if row else ""


def _run_orchestrator(env: dict, args: list[str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(REPO_ROOT / "pipeline" / "run_pipeline.py"), *args]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)


@pytest.fixture
def landing_seeded(tmp_path, seeded_src_db, monkeypatch):
    """Bootstrap a tmp landing/ from the seeded src_* DB."""
    landing = tmp_path / "landing"
    landing.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TRACEX_LANDING_ROOT", str(landing))

    # Run the bootstrap script against the tmp DB to populate landing/.
    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(seeded_src_db)
    # The bootstrap script writes to REPO_ROOT/landing, not the env-overridable
    # path, so we redirect it by temporarily monkey-patching the script's
    # constant via a lightweight wrapper.
    bootstrap = (
        "import sys\n"
        f"sys.path.insert(0, r'{REPO_ROOT}')\n"
        "import scripts.bootstrap_landing as b\n"
        f"b.LANDING_ROOT = __import__('pathlib').Path(r'{landing}')\n"
        # Force every partition to one date so the ingest stage sees a clean set.\n"
        "summary = b.bootstrap(default_business_date='2023-12-31')\n"
        "for r in summary: print(r)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        env=env, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"bootstrap_landing failed: {result.stdout}\n{result.stderr}")
    yield seeded_src_db, landing


def test_missing_as_of_date_fails():
    """Orchestrator without --as-of-date exits non-zero."""
    env = os.environ.copy()
    proc = _run_orchestrator(env, [])
    assert proc.returncode != 0, proc.stdout
    combined = proc.stdout + proc.stderr
    assert "as-of-date" in combined.lower() or "as_of_date" in combined.lower()


def test_same_as_of_date_byte_identical(landing_seeded, tmp_path):
    """Two runs with the same as_of_date → identical L1/L2 row fingerprints."""
    db_path, landing = landing_seeded
    aod = "2023-12-31"  # well after the synthetic dataset's end_date (2023-12-31)
    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(db_path)
    env["TRACEX_LOG_DIR"] = str(tmp_path / "logs")
    env["TRACEX_LANDING_ROOT"] = str(landing)
    env["TRACEX_LINEAGE_AGENTS"] = "off"   # deterministic only
    env["TRACEX_CATALOG"] = "off"
    env.pop("TRACEX_RUN_ID", None)

    # First run.
    p1 = _run_orchestrator(env, ["--as-of-date", aod])
    assert p1.returncode == 0, f"run1 failed:\n{p1.stdout}\n{p1.stderr}"

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        h1 = {
            t: _hash_table(con, t)
            for t in ("stg_fx_resolved", "stg_transaction_normalized",
                      "stg_customer_enriched")
        }
        h1["fct_partition"] = _hash_table(
            con, "fct_customer_risk_profile", f"as_of_date = DATE '{aod}'",
        )
    finally:
        con.close()

    # Second run, same date.
    env.pop("TRACEX_RUN_ID", None)
    p2 = _run_orchestrator(env, ["--as-of-date", aod])
    assert p2.returncode == 0, f"run2 failed:\n{p2.stdout}\n{p2.stderr}"

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        h2 = {
            t: _hash_table(con, t)
            for t in ("stg_fx_resolved", "stg_transaction_normalized",
                      "stg_customer_enriched")
        }
        h2["fct_partition"] = _hash_table(
            con, "fct_customer_risk_profile", f"as_of_date = DATE '{aod}'",
        )
    finally:
        con.close()

    assert h1 == h2, f"\nrun1: {h1}\nrun2: {h2}"


def _seed_partition(db_path: Path, landing: Path, business_date: str) -> None:
    """Re-run the bootstrap against the seeded src_* DB, but stamp every
    partition at `business_date`. Idempotent — overwrites if it already exists."""
    bootstrap = (
        "import sys\n"
        f"sys.path.insert(0, r'{REPO_ROOT}')\n"
        "import scripts.bootstrap_landing as b\n"
        f"b.LANDING_ROOT = __import__('pathlib').Path(r'{landing}')\n"
        f"b.bootstrap(default_business_date='{business_date}')\n"
    )
    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(db_path)
    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"_seed_partition({business_date}): {result.stderr}"


def test_different_as_of_dates_preserve_history(landing_seeded, tmp_path):
    """Run for D1, then D2 > D1, then re-run D1 → D1 partition unchanged."""
    db_path, landing = landing_seeded
    d1 = "2023-12-30"
    d2 = "2023-12-31"

    # The fixture stamped everything at d2 (2023-12-31). Add a d1 partition.
    _seed_partition(db_path, landing, d1)

    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(db_path)
    env["TRACEX_LOG_DIR"] = str(tmp_path / "logs")
    env["TRACEX_LANDING_ROOT"] = str(landing)
    env["TRACEX_LINEAGE_AGENTS"] = "off"
    env["TRACEX_CATALOG"] = "off"

    # D1
    env.pop("TRACEX_RUN_ID", None)
    p1 = _run_orchestrator(env, ["--as-of-date", d1])
    assert p1.returncode == 0, f"D1 first run failed:\n{p1.stdout}\n{p1.stderr}"

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        h_d1_a = _hash_table(con, "fct_customer_risk_profile",
                             f"as_of_date = DATE '{d1}'")
    finally:
        con.close()

    # D2
    env.pop("TRACEX_RUN_ID", None)
    p2 = _run_orchestrator(env, ["--as-of-date", d2])
    assert p2.returncode == 0, f"D2 run failed:\n{p2.stdout}\n{p2.stderr}"

    # Re-run D1
    env.pop("TRACEX_RUN_ID", None)
    p3 = _run_orchestrator(env, ["--as-of-date", d1])
    assert p3.returncode == 0, f"D1 re-run failed:\n{p3.stdout}\n{p3.stderr}"

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        h_d1_b = _hash_table(con, "fct_customer_risk_profile",
                             f"as_of_date = DATE '{d1}'")
        d2_count = int(con.execute(
            f"SELECT COUNT(*) FROM fct_customer_risk_profile WHERE as_of_date = DATE '{d2}'"
        ).fetchone()[0])
    finally:
        con.close()

    assert h_d1_a == h_d1_b, "D1 partition changed after running D2 + re-running D1"
    assert d2_count > 0, "D2 partition was wiped — partition-overwrite leaking"
