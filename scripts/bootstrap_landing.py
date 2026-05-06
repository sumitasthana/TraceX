"""One-shot migrator from the legacy `src_*` tables (in-DuckDB) to a landing tree.

Reads each existing `src_*` table out of `data/tracex_layer0.duckdb`, writes it
as parquet to:

    landing/{sor}/{entity}/business_date={MAX_DATE}/data.parquet

…and stamps a `_manifest.json` next to it. After this runs once, the canonical
workflow becomes "drop new files into landing → run pipeline" — the pipeline's
ingestion stage (`_1_ingest_landing.py`) is the only thing that mutates `src_*`
tables.

Idempotent: re-running overwrites the existing partition and refreshes the
manifest. Safe to invoke whenever the live `src_*` tables have been mutated by
hand (e.g. during local development).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from pipeline.config import get_db_path  # noqa: E402

LANDING_ROOT = REPO_ROOT / "landing"

# Mapping: src_* table → (sor, entity, optional date column whose MAX is the partition).
# A `None` date column means "use today's date" (entities without an event date).
SOR_MAP: list[tuple[str, str, str, str | None]] = [
    ("src_transaction", "core_banking", "transaction", "txn_date"),
    ("src_account",     "core_banking", "account",     None),
    ("src_customer",    "core_banking", "customer",    None),
    ("src_branch",      "core_banking", "branch",      None),
    ("src_fx_rate",     "fx_vendor",    "fx_rate",     "rate_date"),
]


def _today_iso() -> str:
    return _dt.date.today().isoformat()


def _resolve_business_date(
    con,
    table: str,
    date_col: str | None,
    default_date: str | None = None,
) -> str:
    """If `default_date` is passed, it wins for ALL entities — useful for tests
    that need every partition at one date. Otherwise: MAX(date_col) when the
    column exists, today otherwise."""
    if default_date:
        return default_date
    if date_col is None:
        return _today_iso()
    row = con.execute(f"SELECT MAX({date_col}) FROM {table}").fetchone()
    if not row or row[0] is None:
        return _today_iso()
    val = row[0]
    if isinstance(val, _dt.date):
        return val.isoformat()
    return str(val)[:10]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


def bootstrap(default_business_date: str | None = None) -> list[dict]:
    db_path = get_db_path()
    if not db_path.exists():
        raise SystemExit(
            f"DuckDB not found at {db_path}. Run `python layer0/load_duckdb.py` first."
        )

    summary: list[dict] = []
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        existing = {
            r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }

        for table, sor, entity, date_col in SOR_MAP:
            if table not in existing:
                summary.append({
                    "table": table, "status": "skipped",
                    "reason": f"{table} not in DuckDB",
                })
                continue

            business_date = _resolve_business_date(
                con, table, date_col, default_date=default_business_date,
            )
            partition_dir = (
                LANDING_ROOT / sor / entity / f"business_date={business_date}"
            )
            partition_dir.mkdir(parents=True, exist_ok=True)
            parquet_path = partition_dir / "data.parquet"
            manifest_path = partition_dir / "_manifest.json"

            # DuckDB COPY ... TO 'file.parquet' is the simplest path; no pyarrow
            # in our app code. Single quotes inside a path break the literal
            # but our paths are repo-controlled so this is safe.
            con.execute(
                f"COPY (SELECT * FROM {table}) TO '{parquet_path.as_posix()}' "
                f"(FORMAT PARQUET)"
            )
            row_count = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

            sha = _sha256(parquet_path)
            manifest = {
                "sor": sor,
                "entity": entity,
                "business_date": business_date,
                "row_count": row_count,
                "sha256": sha,
                "produced_at": _utc_now(),
                "schema_version": "1.0",
            }
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            try:
                rel = str(parquet_path.relative_to(REPO_ROOT))
            except ValueError:
                rel = str(parquet_path)
            summary.append({
                "table": table,
                "sor": sor,
                "entity": entity,
                "business_date": business_date,
                "row_count": row_count,
                "sha256_prefix": sha[:12],
                "parquet": rel,
            })
    finally:
        con.close()

    return summary


def _parse_args(argv=None):
    import argparse
    p = argparse.ArgumentParser(
        description="Migrate existing src_* tables in DuckDB into landing/.",
    )
    p.add_argument(
        "--business-date",
        default=None,
        help="ISO YYYY-MM-DD; if set, every partition is stamped at this date "
             "(overrides per-table MAX(event_date) inference). Use this when "
             "you want one as_of_date to cover the whole snapshot.",
    )
    return p.parse_args(argv)


def main() -> int:
    args = _parse_args()
    if args.business_date:
        # Sanity-check the format so a typo fails loud.
        _dt.date.fromisoformat(args.business_date)
    summary = bootstrap(default_business_date=args.business_date)
    # Print a compact summary table. ASCII-only for Windows cp1252 stdout.
    sys.stdout.write("\n  Landing-zone bootstrap\n  " + "-" * 76 + "\n")
    sys.stdout.write(f"  {'TABLE':18s} {'SOR':14s} {'ENTITY':14s} {'BUSINESS_DATE':14s} {'ROWS':>8s}  SHA\n")
    sys.stdout.write("  " + "-" * 76 + "\n")
    for row in summary:
        if row.get("status") == "skipped":
            sys.stdout.write(f"  {row['table']:18s}  SKIPPED -- {row.get('reason','')}\n")
            continue
        sys.stdout.write(
            f"  {row['table']:18s} {row['sor']:14s} {row['entity']:14s} "
            f"{row['business_date']:14s} {row['row_count']:>8,}  {row['sha256_prefix']}\n"
        )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
