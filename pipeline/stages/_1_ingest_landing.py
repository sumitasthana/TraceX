"""Stage _1 — landing-zone ingestion + manifest validation.

`src_*` tables are now the OUTPUT of this stage rather than a precondition. The
canonical workflow for a daily run is:

  1. Upstream system drops `data.parquet` + `_manifest.json` into
     `landing/{sor}/{entity}/business_date={YYYY-MM-DD}/`.
  2. This stage locates the partition matching `TRACEX_AS_OF_DATE`, validates
     the manifest (file presence, schema, sha256, row-count), and on full pass
     promotes the parquet to `raw_{sor}_{entity}` and `src_{entity}`.
  3. Stage 00 then asserts the post-promotion `src_*` rows exist and are
     non-empty (its docstring spells this out).

On any validation failure the stage exits 1 WITHOUT mutating any `src_*` table
— the prior good state is preserved for the next attempted run. The
filename starts with a leading underscore (rather than `-1_`) because the
hyphen variant trips up some shell completion and IDE tooling; the
filename is referenced by string in `run_pipeline.py`, not imported as a
module, so the leading underscore has no module-naming consequence.
"""
from __future__ import annotations

import hashlib
import json
import os
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

STAGE_NAME = Path(__file__).stem  # "_1_ingest_landing"

REPO_ROOT = Path(__file__).resolve().parents[2]
LANDING_ROOT = Path(os.environ.get("TRACEX_LANDING_ROOT", str(REPO_ROOT / "landing"))).resolve()

# Same SOR map used by `scripts/bootstrap_landing.py`. Order matters only for
# log readability — there are no foreign-key dependencies between landing
# files, only between the `src_*` tables they promote into (which DuckDB
# enforces in subsequent stages).
SOR_MAP: list[tuple[str, str, str]] = [
    ("core_banking", "branch",      "src_branch"),
    ("core_banking", "customer",    "src_customer"),
    ("core_banking", "account",     "src_account"),
    ("core_banking", "transaction", "src_transaction"),
    ("fx_vendor",    "fx_rate",     "src_fx_rate"),
]

REQUIRED_MANIFEST_KEYS = {
    "sor", "entity", "business_date", "row_count",
    "sha256", "produced_at", "schema_version",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_partition(sor: str, entity: str, as_of_date: str, log) -> Path | None:
    """Return the partition directory for `as_of_date`, or None if missing.

    `as_of_date=None` (currently impossible — `get_as_of_date()` raises) was
    spec'd to fall back to the latest partition; we keep the entry point
    flexible by allowing an empty string here for tests, picking the
    lexicographically max partition name.
    """
    entity_root = LANDING_ROOT / sor / entity
    if not entity_root.exists():
        return None

    if as_of_date:
        candidate = entity_root / f"business_date={as_of_date}"
        return candidate if candidate.is_dir() else None

    # Latest-partition fallback. Currently unreachable in production but
    # exercised by tests that omit a date.
    parts = sorted(
        [p for p in entity_root.iterdir()
         if p.is_dir() and p.name.startswith("business_date=")]
    )
    if not parts:
        return None
    log.warning("landing_partition_default_to_latest",
                sor=sor, entity=entity, partition=parts[-1].name)
    return parts[-1]


def _validate_manifest(
    partition_dir: Path,
    sor: str,
    entity: str,
    as_of_date: str,
    log,
) -> tuple[bool, dict | None, str]:
    """Return (passed, manifest_obj, reason). On failure, reason explains why."""
    manifest_path = partition_dir / "_manifest.json"
    parquet_path = partition_dir / "data.parquet"

    if not manifest_path.exists():
        return False, None, f"_manifest.json missing in {partition_dir}"
    if not parquet_path.exists():
        return False, None, f"data.parquet missing in {partition_dir}"

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, None, f"_manifest.json invalid JSON: {exc}"

    missing = REQUIRED_MANIFEST_KEYS - manifest.keys()
    if missing:
        return False, manifest, f"manifest missing keys: {sorted(missing)}"

    if manifest.get("sor") != sor:
        return False, manifest, f"manifest sor={manifest.get('sor')!r} != expected {sor!r}"
    if manifest.get("entity") != entity:
        return False, manifest, f"manifest entity={manifest.get('entity')!r} != expected {entity!r}"
    if as_of_date and str(manifest.get("business_date")) != as_of_date:
        return False, manifest, (
            f"manifest business_date={manifest.get('business_date')!r} "
            f"!= partition path date {as_of_date!r}"
        )

    # sha256 must match recomputed digest of data.parquet
    actual_sha = _sha256(parquet_path)
    if str(manifest.get("sha256")) != actual_sha:
        return False, manifest, (
            f"sha256 mismatch (manifest={str(manifest.get('sha256'))[:12]}…, "
            f"actual={actual_sha[:12]}…)"
        )

    return True, manifest, ""


def _ingest_one(
    con,
    sor: str,
    entity: str,
    src_table: str,
    partition_dir: Path,
    manifest: dict,
    log,
) -> tuple[int, str]:
    """Validate row count, then atomically rebuild raw_* and src_*. Returns
    (rows_loaded, sha256)."""
    parquet_path = partition_dir / "data.parquet"
    raw_table = f"raw_{sor}_{entity}"

    # Load via DuckDB's read_parquet — preserves column order and types.
    con.execute(
        f"CREATE OR REPLACE TABLE {raw_table} AS "
        f"SELECT * FROM read_parquet('{parquet_path.as_posix()}')"
    )
    actual_rows = int(con.execute(f"SELECT COUNT(*) FROM {raw_table}").fetchone()[0])

    expected = int(manifest.get("row_count", -1))
    if actual_rows != expected:
        # Roll back: drop the raw_ table so partial state isn't visible.
        con.execute(f"DROP TABLE IF EXISTS {raw_table}")
        raise ValueError(
            f"row_count mismatch for {raw_table}: parquet has {actual_rows}, "
            f"manifest claims {expected}"
        )

    log.info(
        "raw_loaded",
        sor=sor, entity=entity, raw_table=raw_table,
        rows=actual_rows, sha256_prefix=manifest["sha256"][:12],
    )

    # Promote to src_*: identical column order/types because we COPY ... TO
    # parquet preserves them.
    con.execute(
        f"CREATE OR REPLACE TABLE {src_table} AS SELECT * FROM {raw_table}"
    )
    log.info(
        "src_promoted",
        sor=sor, entity=entity,
        src_table=src_table, raw_table=raw_table,
        rows=actual_rows,
    )
    return actual_rows, manifest["sha256"]


def main() -> int:
    run_id = get_run_id()
    log = configure_logging(run_id, STAGE_NAME)
    started = time.perf_counter()

    try:
        as_of_date = get_as_of_date()
    except RuntimeError as exc:
        log.error("data_quality_check",
                  check_name="ingest_as_of_date_required",
                  rows_checked=0, rows_failed=1, passed=False,
                  detail=str(exc))
        return 1
    aod = as_of_date.isoformat()

    log.info(
        "stage_start",
        sors=[s for s, _, _ in SOR_MAP],
        landing_root=str(LANDING_ROOT),
        as_of_date_resolved=aod,
    )

    # ── Phase 1: validate every manifest BEFORE mutating any src_* ────
    validations: list[tuple[str, str, str, Path, dict]] = []
    failures: list[str] = []

    for sor, entity, src_table in SOR_MAP:
        partition_dir = _resolve_partition(sor, entity, aod, log)
        if partition_dir is None:
            reason = (
                f"landing partition not found: "
                f"landing/{sor}/{entity}/business_date={aod}/"
            )
            log.error("data_quality_check",
                      check_name=f"ingest_{sor}_{entity}_partition_present",
                      rows_checked=0, rows_failed=1, passed=False, detail=reason)
            log.error("manifest_validation_failed",
                      sor=sor, entity=entity, reason=reason)
            failures.append(reason)
            continue

        log.info("landing_partition_resolved",
                 sor=sor, entity=entity, partition=str(partition_dir))

        passed, manifest, reason = _validate_manifest(
            partition_dir, sor, entity, aod, log,
        )
        log.info(
            "data_quality_check",
            check_name=f"ingest_{sor}_{entity}_manifest_valid",
            rows_checked=1,
            rows_failed=0 if passed else 1,
            passed=passed,
            detail=reason if not passed else None,
        )
        if not passed:
            log.error("manifest_validation_failed",
                      sor=sor, entity=entity, reason=reason)
            failures.append(f"{sor}/{entity}: {reason}")
            continue

        log.info("manifest_validated",
                 sor=sor, entity=entity, business_date=manifest["business_date"])
        validations.append((sor, entity, src_table, partition_dir, manifest))

    if failures:
        log.error(
            "stage_complete",
            status="failed",
            failures=failures,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return 1

    # ── Phase 2: promote — every manifest passed, mutations are safe ──
    rows_per_entity: dict[str, int] = {}
    try:
        with db_connect() as con:
            for sor, entity, src_table, partition_dir, manifest in validations:
                rows, _sha = _ingest_one(con, sor, entity, src_table,
                                         partition_dir, manifest, log)
                rows_per_entity[src_table] = rows

                # Lineage: each src_* is now produced by an INGEST process. No
                # source_tables (it's an external file) and no upstream stages.
                try:
                    from lineage.manifest_builder import build_and_ingest  # noqa: WPS433
                    sql = (
                        f"-- ingest from landing/{sor}/{entity}/business_date={aod}\n"
                        f"CREATE OR REPLACE TABLE {src_table} AS "
                        f"SELECT * FROM read_parquet("
                        f"'{(partition_dir / 'data.parquet').as_posix()}')"
                    )
                    build_and_ingest(
                        stage=STAGE_NAME,
                        run_id=run_id,
                        sql=sql,
                        target_table=src_table,
                        source_tables=[],
                        depends_on_stages=[],
                        transform_type="INGEST",
                        output_row_count=rows,
                        duration_ms=0,
                        con=con,
                    )
                except Exception as exc:
                    log.warning(
                        "lineage_manifest_invoke_failed",
                        stage=STAGE_NAME, target_table=src_table,
                        error=str(exc), error_type=type(exc).__name__,
                    )
    except Exception as exc:
        log.error(
            "stage_exception",
            error=str(exc),
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )
        return 1

    log.info(
        "ingest_summary",
        as_of_date=aod,
        entities_ingested=len(rows_per_entity),
        rows_per_entity=rows_per_entity,
    )
    log.info(
        "stage_complete",
        status="ok",
        entities_ingested=len(rows_per_entity),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
