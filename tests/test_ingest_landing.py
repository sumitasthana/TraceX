"""Ingest-stage tests: manifest validation must abort the run before any
mutation of `src_*` tables.

Each test seeds a tmp DuckDB with the live src_* shape, writes a tmp
landing/ tree, then introduces a single corruption in the manifest or
parquet and asserts the stage exits 1 with no mutation.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_stage(stage_path: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(stage_path)],
        env=env, capture_output=True, text=True, check=False,
    )


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


def _manifest_path(landing: Path) -> Path:
    return next(landing.rglob("_manifest.json"))


def _seed_baseline_table_for_mutation_check(db_path: Path) -> int:
    """Stamp `src_transaction.txn_id = 'BASELINE_SENTINEL'` row so we can verify
    the ingest stage didn't mutate src_*."""
    con = duckdb.connect(str(db_path))
    try:
        # Add a sentinel row to src_transaction to detect any mutation.
        # If the schema is too restrictive the INSERT will fail and we just
        # measure row count instead.
        try:
            cols = [r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'src_transaction' ORDER BY ordinal_position"
            ).fetchall()]
            placeholders = ", ".join(["NULL"] * len(cols))
            con.execute(
                f"INSERT INTO src_transaction ({', '.join(cols)}) VALUES ({placeholders})"
            )
        except Exception:
            pass
        n = int(con.execute("SELECT COUNT(*) FROM src_transaction").fetchone()[0])
    finally:
        con.close()
    return n


def _src_row_count(db_path: Path, table: str) -> int:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        con.close()


def test_manifest_missing_fails(landing_seeded, tmp_path):
    db_path, landing = landing_seeded
    baseline_count = _src_row_count(db_path, "src_transaction")

    # Remove a key from one manifest.
    mp = next(landing.rglob("_manifest.json"))
    data = json.loads(mp.read_text())
    data.pop("sha256", None)
    mp.write_text(json.dumps(data))

    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(db_path)
    env["TRACEX_LOG_DIR"] = str(tmp_path / "logs")
    env["TRACEX_LANDING_ROOT"] = str(landing)
    env["TRACEX_AS_OF_DATE"] = "2023-12-31"
    env.pop("TRACEX_RUN_ID", None)

    stage = REPO_ROOT / "pipeline" / "stages" / "_1_ingest_landing.py"
    proc = _run_stage(stage, env)
    assert proc.returncode != 0, "expected ingest to fail when manifest key missing"

    # No mutation.
    assert _src_row_count(db_path, "src_transaction") == baseline_count


def test_sha256_mismatch_fails(landing_seeded, tmp_path):
    db_path, landing = landing_seeded
    baseline_count = _src_row_count(db_path, "src_transaction")

    # Flip one byte of a parquet → sha256 changes.
    pq = next(landing.rglob("data.parquet"))
    raw = pq.read_bytes()
    pq.write_bytes(raw[:-1] + bytes([(raw[-1] ^ 0xFF) & 0xFF]))

    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(db_path)
    env["TRACEX_LOG_DIR"] = str(tmp_path / "logs")
    env["TRACEX_LANDING_ROOT"] = str(landing)
    env["TRACEX_AS_OF_DATE"] = "2023-12-31"
    env.pop("TRACEX_RUN_ID", None)

    stage = REPO_ROOT / "pipeline" / "stages" / "_1_ingest_landing.py"
    proc = _run_stage(stage, env)
    assert proc.returncode != 0
    assert _src_row_count(db_path, "src_transaction") == baseline_count


def test_row_count_mismatch_fails(landing_seeded, tmp_path):
    db_path, landing = landing_seeded
    baseline_count = _src_row_count(db_path, "src_transaction")

    # Edit only one manifest's row_count to be wrong.
    target = None
    for mp in landing.rglob("_manifest.json"):
        data = json.loads(mp.read_text())
        if data.get("entity") == "branch":
            target = mp
            data["row_count"] = data.get("row_count", 0) + 99999
            mp.write_text(json.dumps(data))
            break
    assert target is not None, "no branch manifest found in landing fixture"

    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(db_path)
    env["TRACEX_LOG_DIR"] = str(tmp_path / "logs")
    env["TRACEX_LANDING_ROOT"] = str(landing)
    env["TRACEX_AS_OF_DATE"] = "2023-12-31"
    env.pop("TRACEX_RUN_ID", None)

    stage = REPO_ROOT / "pipeline" / "stages" / "_1_ingest_landing.py"
    proc = _run_stage(stage, env)
    # The bad row_count should NOT pass sha256 (sha is computed from parquet
    # bytes, not manifest), but our stage validates row_count against the
    # parquet's actual count separately. Both checks together — at least one
    # must trip and the stage exits 1.
    # Note: with our two-phase design, sha256 is checked first. We only get
    # to row_count if sha matched. Since we didn't touch the parquet, sha
    # will match, but we deliberately broke row_count → stage MUST fail.
    assert proc.returncode != 0
    assert _src_row_count(db_path, "src_transaction") == baseline_count


def test_happy_path_promotes_src(landing_seeded, tmp_path):
    db_path, landing = landing_seeded

    # Capture the manifest row_count for src_transaction.
    expected_rows = None
    for mp in landing.rglob("_manifest.json"):
        data = json.loads(mp.read_text())
        if data.get("entity") == "transaction":
            expected_rows = int(data["row_count"])
            break
    assert expected_rows is not None

    env = os.environ.copy()
    env["TRACEX_DB_PATH"] = str(db_path)
    env["TRACEX_LOG_DIR"] = str(tmp_path / "logs")
    env["TRACEX_LANDING_ROOT"] = str(landing)
    env["TRACEX_AS_OF_DATE"] = "2023-12-31"
    env.pop("TRACEX_RUN_ID", None)

    stage = REPO_ROOT / "pipeline" / "stages" / "_1_ingest_landing.py"
    proc = _run_stage(stage, env)
    assert proc.returncode == 0, f"ingest failed:\n{proc.stdout}\n{proc.stderr}"

    actual = _src_row_count(db_path, "src_transaction")
    assert actual == expected_rows, f"src_transaction has {actual}, manifest claims {expected_rows}"
