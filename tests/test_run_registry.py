"""Run-registry tests.

The orchestrator records every invocation in `pipeline_runs` and every stage
subprocess in `pipeline_run_stages`. We assert these tables are populated
correctly on success, on failure, and across multiple runs.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_orchestrator(env: dict, args: list[str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(REPO_ROOT / "pipeline" / "run_pipeline.py"), *args]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)


@pytest.fixture
def landing_seeded(tmp_path, seeded_src_db, monkeypatch):
    landing = tmp_path / "landing"
    landing.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TRACEX_LANDING_ROOT", str(landing))

    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(seeded_src_db)
    bootstrap = (
        "import sys\n"
        f"sys.path.insert(0, r'{REPO_ROOT}')\n"
        "import scripts.bootstrap_landing as b\n"
        f"b.LANDING_ROOT = __import__('pathlib').Path(r'{landing}')\n"
        "b.bootstrap(default_business_date='2023-12-31')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        env=env, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"bootstrap_landing failed: {result.stdout}\n{result.stderr}")
    yield seeded_src_db, landing


def test_successful_run_recorded(landing_seeded, tmp_path):
    db_path, landing = landing_seeded
    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(db_path)
    env["TRACEX_LOG_DIR"] = str(tmp_path / "logs")
    env["TRACEX_LANDING_ROOT"] = str(landing)
    env["TRACEX_LINEAGE_AGENTS"] = "off"
    env["TRACEX_CATALOG"] = "off"
    env.pop("TRACEX_RUN_ID", None)

    proc = _run_orchestrator(env, ["--as-of-date", "2023-12-31"])
    assert proc.returncode == 0, f"orchestrator failed:\n{proc.stdout}\n{proc.stderr}"

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        runs = con.execute(
            "SELECT run_id, status, ended_at, failed_stage, duration_ms, "
            "list_aggregate(stages_planned, 'count') AS n_stages "
            "FROM pipeline_runs"
        ).fetchall()
        assert len(runs) == 1, runs
        run_id, status, ended_at, failed_stage, duration_ms, n_stages = runs[0]
        assert status == "ok", status
        assert ended_at is not None
        assert failed_stage is None
        assert duration_ms is not None and duration_ms > 0
        assert int(n_stages) > 0

        stages = con.execute(
            "SELECT stage_name, status, exit_code "
            "FROM pipeline_run_stages WHERE run_id = ? "
            "ORDER BY started_at",
            [run_id],
        ).fetchall()
        assert len(stages) >= 7  # _1 + 00 + 01 + 02 + 03 + 10 + 99
        for stage_name, status, exit_code in stages:
            assert status == "ok", f"stage {stage_name} not ok: status={status}"
            assert exit_code == 0, f"stage {stage_name} exit_code={exit_code}"
    finally:
        con.close()


def test_failed_stage_recorded(landing_seeded, tmp_path, monkeypatch):
    """Corrupt one parquet by a byte → ingest stage exits 1, registry shows failure."""
    db_path, landing = landing_seeded

    # Pick a parquet to corrupt — first one we find.
    target = next(landing.rglob("data.parquet"))
    raw = target.read_bytes()
    target.write_bytes(raw[:-1] + bytes([(raw[-1] ^ 0xFF) & 0xFF]))

    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(db_path)
    env["TRACEX_LOG_DIR"] = str(tmp_path / "logs")
    env["TRACEX_LANDING_ROOT"] = str(landing)
    env["TRACEX_LINEAGE_AGENTS"] = "off"
    env["TRACEX_CATALOG"] = "off"
    env.pop("TRACEX_RUN_ID", None)

    proc = _run_orchestrator(env, ["--as-of-date", "2023-12-31"])
    assert proc.returncode != 0, "expected failure on corrupted parquet"

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        runs = con.execute(
            "SELECT status, failed_stage FROM pipeline_runs"
        ).fetchall()
        assert len(runs) == 1
        status, failed_stage = runs[0]
        assert status == "failed"
        assert failed_stage and "ingest_landing" in failed_stage

        # Downstream stages should NOT have rows (orchestrator broke early).
        downstream = con.execute(
            "SELECT stage_name FROM pipeline_run_stages "
            "WHERE stage_name IN ('00_validate_sources','01_stg_fx_normalize',"
            "                     '02_stg_transactions','03_stg_customers',"
            "                     '10_fct_risk_profile','99_validate_outputs')"
        ).fetchall()
        assert downstream == [], f"unexpected downstream stage rows: {downstream}"
    finally:
        con.close()


def test_two_runs_same_as_of_date_both_recorded(landing_seeded, tmp_path):
    db_path, landing = landing_seeded
    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(db_path)
    env["TRACEX_LOG_DIR"] = str(tmp_path / "logs")
    env["TRACEX_LANDING_ROOT"] = str(landing)
    env["TRACEX_LINEAGE_AGENTS"] = "off"
    env["TRACEX_CATALOG"] = "off"

    for _ in range(2):
        env.pop("TRACEX_RUN_ID", None)
        proc = _run_orchestrator(env, ["--as-of-date", "2023-12-31"])
        assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        n = int(con.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0])
        oks = int(con.execute(
            "SELECT COUNT(*) FROM pipeline_runs WHERE status = 'ok'"
        ).fetchone()[0])
    finally:
        con.close()
    assert n == 2, f"expected 2 run rows, got {n}"
    assert oks == 2
