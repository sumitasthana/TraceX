"""Shared pytest fixtures for tier-1 tests.

Each test gets its own DuckDB file under tmp_path and a tmp `landing/` tree, so
the live `data/tracex_layer0.duckdb` and `landing/` are never mutated. We point
the pipeline at them via `TRACEX_DB_PATH`, `TRACEX_LOG_DIR`, and `TRACEX_AS_OF_DATE`,
plus we monkey-patch `LANDING_ROOT` inside `_1_ingest_landing` because that
stage hardcodes the path off of `__file__`.

Tests run the orchestrator in-process (rather than spawning a python subprocess
of the orchestrator); the orchestrator itself still spawns each stage as a
subprocess, but those inherit our env via `os.environ.copy()`.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def isolate_kuzu(tmp_path, monkeypatch):
    """Every test gets its own Kuzu path so we don't fight the live UI's lock,
    and we disable the catalog + LLM agents to keep tests deterministic."""
    monkeypatch.setenv("TRACEX_GRAPH_PATH", str(tmp_path / "tracex_graph"))
    monkeypatch.setenv("TRACEX_LINEAGE_AGENTS", "off")
    monkeypatch.setenv("TRACEX_CATALOG", "off")
    yield


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Empty DuckDB file. Caller fills with whatever shape they need."""
    db_path = tmp_path / "tracex.duckdb"
    monkeypatch.setenv("TRACEX_DB_PATH", str(db_path))
    monkeypatch.setenv("TRACEX_LOG_DIR", str(tmp_path / "logs"))
    yield db_path


@pytest.fixture
def seeded_src_db(tmp_path, monkeypatch):
    """A tmp DuckDB containing a tiny copy of every src_* table the pipeline needs.
    Built by COPYing rows from the live data/tracex_layer0.duckdb if available, or
    by inserting hand-rolled rows otherwise."""
    db_path = tmp_path / "tracex.duckdb"
    monkeypatch.setenv("TRACEX_DB_PATH", str(db_path))
    monkeypatch.setenv("TRACEX_LOG_DIR", str(tmp_path / "logs"))

    live_db = REPO_ROOT / "data" / "tracex_layer0.duckdb"

    con = duckdb.connect(str(db_path))
    try:
        if live_db.exists():
            # Cheapest: ATTACH the live DB read-only and CREATE TABLE AS SELECT
            con.execute(f"ATTACH '{live_db.as_posix()}' AS live (READ_ONLY)")
            for t in ("src_branch", "src_customer", "src_account",
                      "src_transaction", "src_fx_rate"):
                con.execute(f"CREATE TABLE {t} AS SELECT * FROM live.{t}")
            con.execute("DETACH live")
        else:
            # Minimal hand-rolled dataset; only enough to let the pipeline
            # not crash. Tests that depend on real shape should skip when
            # live_db is absent.
            con.execute("CREATE TABLE src_branch (branch_id VARCHAR, region VARCHAR)")
            con.execute("CREATE TABLE src_customer (customer_id VARCHAR, "
                        "first_name VARCHAR, last_name VARCHAR, dob DATE, "
                        "ssn_hash VARCHAR, country_of_birth VARCHAR, "
                        "citizenship VARCHAR, onboarded_date DATE, "
                        "kyc_status VARCHAR, kyc_reviewed_at TIMESTAMP, "
                        "branch_id VARCHAR)")
            con.execute("CREATE TABLE src_account (account_id VARCHAR, "
                        "customer_id VARCHAR, branch_id VARCHAR)")
            con.execute("CREATE TABLE src_transaction (txn_id VARCHAR, "
                        "account_id VARCHAR, txn_date DATE, currency VARCHAR, "
                        "amount DECIMAL(15,2), reversal_flag VARCHAR, "
                        "counterparty_bank_bic VARCHAR)")
            con.execute("CREATE TABLE src_fx_rate (rate_date DATE, "
                        "from_currency VARCHAR, to_currency VARCHAR, rate DOUBLE, "
                        "rate_source VARCHAR)")
    finally:
        con.close()
    yield db_path


@pytest.fixture
def landing_root(tmp_path, monkeypatch):
    """Tmp landing/ tree, with the ingest stage's LANDING_ROOT redirected to it."""
    root = tmp_path / "landing"
    root.mkdir(parents=True, exist_ok=True)

    # The stage script hardcodes LANDING_ROOT = REPO_ROOT / "landing", so
    # rather than monkeypatch a module attribute (the stage runs as a
    # subprocess), we ship our own landing tree under tmp and monkeypatch
    # via env. Add a TRACEX_LANDING_ROOT escape hatch the stage will honour.
    monkeypatch.setenv("TRACEX_LANDING_ROOT", str(root))
    yield root
