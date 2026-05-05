"""GraphBuilder — writes the TraceX knowledge graph into an embedded Kuzu DB.

Two intentional deviations from the Cypher-style spec, dictated by Kuzu 0.11.x:

  * Composite primary keys are not supported. The Column and Process tables use
    a synthetic single-column PK (`pk STRING`) derived from their natural-key
    tuple. The natural-key columns remain as regular STRING properties so all
    `MATCH (c:Column {column_name: $cn, dataset_name: $ds})` queries still work.
  * `Column` is a reserved word in Kuzu's parser. We backtick-escape it
    everywhere it appears as a label, the way standard Cypher would.

Idempotency contract:
    Running ingest_run twice on the same ParsedRun must leave the graph in an
    identical state. Achieved by:
      * Node upserts use Cypher MERGE keyed on the table's PRIMARY KEY column
        (the synthetic `pk` for Column/Process, `name` for the others), with
        ON MATCH/ON CREATE SET branches for every non-PK field.
      * Relationship merges use a MATCH-count-then-CREATE-if-absent helper.
        Works on every Kuzu version including releases without rel-MERGE.

Parameterization:
    Every `conn.execute` call passes values as a parameters dict — no f-string
    interpolation into Cypher.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

import kuzu

from lineage.config import configure_logging, get_db, get_conn, get_graph_path
from lineage.models import LineageManifest, ParsedRun, StageMetrics

COLUMN_REF_PATTERN = re.compile(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)")

# Backtick form for the reserved-word `Column` label.
COL = "`Column`"

NODE_TABLES = [
    """
    CREATE NODE TABLE DataSet(
        name        STRING,
        layer       STRING,
        row_count   INT64,
        computed_at STRING,
        PRIMARY KEY (name)
    )
    """,
    f"""
    CREATE NODE TABLE {COL}(
        pk           STRING,
        column_name  STRING,
        dataset_name STRING,
        derivation   STRING,
        computed_at  STRING,
        PRIMARY KEY (pk)
    )
    """,
    """
    CREATE NODE TABLE Process(
        pk                STRING,
        stage             STRING,
        run_id            STRING,
        transform_type    STRING,
        target_table      STRING,
        duration_ms       INT64,
        output_row_count  INT64,
        computed_at       STRING,
        PRIMARY KEY (pk)
    )
    """,
    """
    CREATE NODE TABLE Owner(
        name STRING,
        PRIMARY KEY (name)
    )
    """,
    """
    CREATE NODE TABLE Tag(
        name STRING,
        PRIMARY KEY (name)
    )
    """,
]

REL_TABLES = [
    f"CREATE REL TABLE INPUT_TO(FROM DataSet TO Process)",
    f"CREATE REL TABLE PRODUCES(FROM Process TO DataSet)",
    f"CREATE REL TABLE DERIVES_FROM(FROM {COL} TO {COL})",
    "CREATE REL TABLE DEPENDS_ON(FROM Process TO Process)",
    "CREATE REL TABLE OWNED_BY(FROM DataSet TO Owner)",
    f"CREATE REL TABLE CLASSIFIED_AS(FROM {COL} TO Tag)",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_already_exists(exc: Exception) -> bool:
    """Kuzu raises plain RuntimeError on catalog conflicts; sniff the message."""
    return "already exists" in str(exc).lower()


def _column_pk(column_name: str, dataset_name: str) -> str:
    return f"{dataset_name}::{column_name}"


def _process_pk(stage: str, run_id: str) -> str:
    return f"{stage}::{run_id}"


class GraphBuilder:
    def __init__(self, graph_path: Optional[str] = None, run_id: str = "lineage"):
        self.graph_path = graph_path or str(get_graph_path())
        self.run_id = run_id
        self.log = configure_logging(run_id, "graph_builder")
        self.db = kuzu.Database(self.graph_path) if graph_path else get_db()
        self.conn = get_conn(self.db)
        self._bootstrap_schema()
        self.log.info("graph_builder_ready", graph_path=self.graph_path)

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def _bootstrap_schema(self) -> None:
        created: list[str] = []
        for stmt in NODE_TABLES + REL_TABLES:
            label = self._extract_label(stmt)
            try:
                self.conn.execute(stmt)
                created.append(label)
            except RuntimeError as exc:
                if _is_already_exists(exc):
                    self.log.debug("schema_table_exists", label=label)
                    continue
                self.log.error("schema_bootstrap_failed", label=label, error=str(exc))
                raise
        self.log.info("schema_bootstrap_complete", tables_created=created)

    @staticmethod
    def _extract_label(stmt: str) -> str:
        m = re.search(r"CREATE\s+(?:NODE|REL)\s+TABLE\s+`?(\w+)`?", stmt, re.IGNORECASE)
        return m.group(1) if m else "?"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_layer(table_name: str) -> str:
        if table_name.startswith("src_"):
            return "layer_0"
        if table_name.startswith("stg_"):
            return "layer_1"
        if table_name.startswith("fct_") or table_name.startswith("dim_"):
            return "layer_2"
        return "unknown"

    def _safe_merge_rel(
        self,
        from_label: str,
        from_filter: dict,
        rel_label: str,
        to_label: str,
        to_filter: dict,
    ) -> bool:
        """MATCH-count → CREATE-if-absent edge upsert.

        from_filter / to_filter are {column_name: value} dicts. We auto-prefix the
        TO-side parameter names with `to_` to avoid collisions when both endpoints
        share column names (e.g. DEPENDS_ON: Process→Process).
        """
        from_clause = ", ".join(f"{k}: $from_{k}" for k in from_filter)
        to_clause = ", ".join(f"{k}: $to_{k}" for k in to_filter)
        params = {
            **{f"from_{k}": v for k, v in from_filter.items()},
            **{f"to_{k}": v for k, v in to_filter.items()},
        }

        check_query = (
            f"MATCH (a:{from_label} {{{from_clause}}})-[r:{rel_label}]->"
            f"(b:{to_label} {{{to_clause}}}) RETURN count(r)"
        )
        result = self.conn.execute(check_query, params)
        existing = int(result.get_next()[0])
        if existing > 0:
            return False

        create_query = (
            f"MATCH (a:{from_label} {{{from_clause}}}), "
            f"(b:{to_label} {{{to_clause}}}) "
            f"CREATE (a)-[:{rel_label}]->(b)"
        )
        self.conn.execute(create_query, params)
        return True

    def _node_exists(self, label: str, filter_: dict) -> bool:
        clause = ", ".join(f"{k}: ${k}" for k in filter_)
        q = f"MATCH (n:{label} {{{clause}}}) RETURN count(n)"
        return int(self.conn.execute(q, dict(filter_)).get_next()[0]) > 0

    # ------------------------------------------------------------------
    # Node upserts
    # ------------------------------------------------------------------

    def _upsert_dataset(
        self, name: str, row_count: int = 0, computed_at: str = ""
    ) -> None:
        layer = self._infer_layer(name)
        ts = computed_at or _utc_now_iso()
        self.conn.execute(
            """
            MERGE (d:DataSet {name: $name})
            ON CREATE SET d.layer = $layer,
                          d.row_count = $row_count,
                          d.computed_at = $computed_at
            ON MATCH  SET d.row_count = $row_count
            """,
            {"name": name, "layer": layer, "row_count": int(row_count), "computed_at": ts},
        )
        self.log.info("dataset_upserted", name=name, layer=layer, row_count=int(row_count))

    def _upsert_process(
        self, manifest: LineageManifest, metrics: Optional[StageMetrics]
    ) -> None:
        duration_ms = int(metrics.duration_ms) if metrics is not None else 0
        output_row_count = int(metrics.output_row_count) if metrics is not None else 0
        ts = manifest.ts or _utc_now_iso()
        pk = _process_pk(manifest.stage, manifest.run_id)
        self.conn.execute(
            """
            MERGE (p:Process {pk: $pk})
            ON CREATE SET p.stage            = $stage,
                          p.run_id           = $run_id,
                          p.transform_type   = $transform_type,
                          p.target_table     = $target_table,
                          p.duration_ms      = $duration_ms,
                          p.output_row_count = $output_row_count,
                          p.computed_at      = $computed_at
            ON MATCH  SET p.transform_type   = $transform_type,
                          p.target_table     = $target_table,
                          p.duration_ms      = $duration_ms,
                          p.output_row_count = $output_row_count
            """,
            {
                "pk": pk,
                "stage": manifest.stage,
                "run_id": manifest.run_id,
                "transform_type": manifest.transform_type,
                "target_table": manifest.target_table,
                "duration_ms": duration_ms,
                "output_row_count": output_row_count,
                "computed_at": ts,
            },
        )
        self.log.info(
            "process_upserted",
            stage=manifest.stage,
            run_id=manifest.run_id,
            transform_type=manifest.transform_type,
        )

    def _upsert_column(
        self, column_name: str, dataset_name: str, derivation: str, computed_at: str
    ) -> None:
        ts = computed_at or _utc_now_iso()
        pk = _column_pk(column_name, dataset_name)
        self.conn.execute(
            f"""
            MERGE (c:{COL} {{pk: $pk}})
            ON CREATE SET c.column_name  = $column_name,
                          c.dataset_name = $dataset_name,
                          c.derivation   = $derivation,
                          c.computed_at  = $computed_at
            ON MATCH  SET c.derivation   = $derivation
            """,
            {
                "pk": pk,
                "column_name": column_name,
                "dataset_name": dataset_name,
                "derivation": derivation or "",
                "computed_at": ts,
            },
        )
        self.log.info("column_upserted", column_name=column_name, dataset_name=dataset_name)

    # ------------------------------------------------------------------
    # Edge upserts
    # ------------------------------------------------------------------

    def _link_input_to(self, dataset_name: str, stage: str, run_id: str) -> None:
        created = self._safe_merge_rel(
            "DataSet", {"name": dataset_name},
            "INPUT_TO",
            "Process", {"pk": _process_pk(stage, run_id)},
        )
        if created:
            self.log.info(
                "edge_created",
                label="INPUT_TO",
                from_node=dataset_name,
                to_node=f"{stage}@{run_id[:8]}",
            )

    def _link_produces(self, stage: str, run_id: str, dataset_name: str) -> None:
        created = self._safe_merge_rel(
            "Process", {"pk": _process_pk(stage, run_id)},
            "PRODUCES",
            "DataSet", {"name": dataset_name},
        )
        if created:
            self.log.info(
                "edge_created",
                label="PRODUCES",
                from_node=f"{stage}@{run_id[:8]}",
                to_node=dataset_name,
            )

    def _link_depends_on(self, stage: str, run_id: str, depends_on_stage: str) -> None:
        # Strict per spec: do not auto-create a stub for the upstream Process.
        # Log a WARNING and skip the edge if the upstream Process is missing.
        if not self._node_exists("Process", {"pk": _process_pk(depends_on_stage, run_id)}):
            self.log.warning(
                "depends_on_target_missing",
                stage=stage,
                missing_depends_on_stage=depends_on_stage,
            )
            return

        created = self._safe_merge_rel(
            "Process", {"pk": _process_pk(stage, run_id)},
            "DEPENDS_ON",
            "Process", {"pk": _process_pk(depends_on_stage, run_id)},
        )
        if created:
            self.log.info(
                "edge_created",
                label="DEPENDS_ON",
                from_node=stage,
                to_node=depends_on_stage,
            )

    def _link_column_derivations(self, manifest: LineageManifest) -> None:
        # Only honour table.column references whose table appears in the
        # manifest's source_tables (or the target itself). Stray matches like
        # numeric literals "0.65" or expressions "INTERVAL.DAY" get dropped.
        trusted_tables = set(manifest.source_tables) | {manifest.target_table}

        for col_name, derivation in manifest.derived_columns.items():
            for src_table, src_col in COLUMN_REF_PATTERN.findall(derivation or ""):
                if src_table not in trusted_tables:
                    continue
                if src_table == manifest.target_table and src_col == col_name:
                    continue  # self-reference

                self._upsert_column(src_col, src_table, "", manifest.ts)
                self._upsert_column(col_name, manifest.target_table, derivation, manifest.ts)

                created = self._safe_merge_rel(
                    COL, {"pk": _column_pk(col_name, manifest.target_table)},
                    "DERIVES_FROM",
                    COL, {"pk": _column_pk(src_col, src_table)},
                )
                if created:
                    self.log.info(
                        "column_lineage_edge",
                        target_col=col_name,
                        target_dataset=manifest.target_table,
                        source_col=src_col,
                        source_dataset=src_table,
                    )

    # ------------------------------------------------------------------
    # Driver
    # ------------------------------------------------------------------

    def ingest_run(self, parsed_run: ParsedRun) -> dict:
        started = time.perf_counter()
        manifests_processed = 0
        failures: list[str] = []

        for manifest in parsed_run.manifests:
            try:
                self._ingest_one_manifest(manifest, parsed_run.metrics.get(manifest.stage))
                manifests_processed += 1
            except Exception:
                self.log.exception(
                    "operation_failed",
                    operation="ingest_one_manifest",
                    stage=manifest.stage,
                    target_table=manifest.target_table,
                )
                failures.append(manifest.stage)
                # Do not re-raise — continue with the rest of the run.

        return {
            "run_id": parsed_run.run_id,
            "manifests_processed": manifests_processed,
            "manifests_failed": failures,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    def _ingest_one_manifest(
        self, manifest: LineageManifest, metrics: Optional[StageMetrics]
    ) -> None:
        # 1. Datasets
        target_row_count = int(metrics.output_row_count) if metrics is not None else 0
        self._upsert_dataset(manifest.target_table, target_row_count, manifest.ts)
        for src in manifest.source_tables:
            self._upsert_dataset(src, 0, manifest.ts)

        # 2. Process
        self._upsert_process(manifest, metrics)

        # 3. Edges around the Process
        for src in manifest.source_tables:
            self._link_input_to(src, manifest.stage, manifest.run_id)
        self._link_produces(manifest.stage, manifest.run_id, manifest.target_table)
        for dep in manifest.depends_on_stages:
            self._link_depends_on(manifest.stage, manifest.run_id, dep)

        # 4. Columns (target side)
        for col_name, derivation in manifest.derived_columns.items():
            self._upsert_column(col_name, manifest.target_table, derivation, manifest.ts)

        # 5. Column-level lineage (only fires when the derivation expression
        #    contains qualified table.column refs whose table is in source_tables).
        self._link_column_derivations(manifest)

    def close(self) -> None:
        try:
            if hasattr(self.conn, "close"):
                self.conn.close()
        finally:
            self.log.info("graph_builder_closed")
