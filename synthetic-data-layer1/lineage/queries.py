"""Read-only Cypher queries against the TraceX Kuzu graph.

Each function takes a `kuzu.Connection` and returns plain Python — no Kuzu
result objects leak past this module. All queries are parameterized; we never
splice user values into the query string.
"""
from __future__ import annotations

from typing import List

import kuzu

# `Column` must be backtick-escaped in Cypher because it is a Kuzu reserved word.
# We keep the human-friendly label "Column" in the dict keys we return so callers
# do not need to know about the escape.
NODE_LABELS = ("DataSet", "Column", "Process", "Owner", "Tag")
EDGE_LABELS = ("INPUT_TO", "PRODUCES", "DERIVES_FROM", "DEPENDS_ON", "OWNED_BY", "CLASSIFIED_AS")


def _scalar(result: kuzu.QueryResult) -> int:
    if not result.has_next():
        return 0
    return int(result.get_next()[0])


def _label_for_query(label: str) -> str:
    # Backtick-escape only what we must (`Column` is reserved). Everything else
    # we leave as-is to keep query strings readable.
    return f"`{label}`" if label == "Column" else label


def count_nodes_by_label(conn: kuzu.Connection) -> dict:
    out: dict = {}
    for label in NODE_LABELS:
        # Label has to be in the query body — Kuzu doesn't parameterize node labels —
        # but every value here comes from an internal hardcoded tuple, never from
        # user input, so there's no injection surface.
        r = conn.execute(f"MATCH (n:{_label_for_query(label)}) RETURN count(n) AS cnt")
        out[label] = _scalar(r)
    return out


def count_edges_by_label(conn: kuzu.Connection) -> dict:
    out: dict = {}
    for label in EDGE_LABELS:
        r = conn.execute(f"MATCH ()-[r:{label}]->() RETURN count(r) AS cnt")
        out[label] = _scalar(r)
    return out


def get_dataset_upstream(conn: kuzu.Connection, table_name: str) -> List[dict]:
    """Datasets that feed into `table_name` via one Process hop."""
    r = conn.execute(
        """
        MATCH (src:DataSet)-[:INPUT_TO]->(p:Process)-[:PRODUCES]->(tgt:DataSet {name: $name})
        RETURN src.name AS upstream_table,
               p.stage  AS via_process,
               p.transform_type AS transform_type
        """,
        {"name": table_name},
    )
    return _rows(r, ("upstream_table", "via_process", "transform_type"))


def get_dataset_downstream(conn: kuzu.Connection, table_name: str) -> List[dict]:
    r = conn.execute(
        """
        MATCH (src:DataSet {name: $name})-[:INPUT_TO]->(p:Process)-[:PRODUCES]->(tgt:DataSet)
        RETURN tgt.name AS downstream_table,
               p.stage  AS via_process
        """,
        {"name": table_name},
    )
    return _rows(r, ("downstream_table", "via_process"))


def get_column_lineage(
    conn: kuzu.Connection, table_name: str, column_name: str
) -> List[dict]:
    """Walk DERIVES_FROM up to 10 hops; return ancestor columns ordered by hop count."""
    r = conn.execute(
        """
        MATCH path = (c:`Column` {dataset_name: $table_name, column_name: $column_name})
                     -[:DERIVES_FROM*1..10]->(ancestor:`Column`)
        RETURN ancestor.dataset_name AS source_dataset,
               ancestor.column_name  AS source_column,
               ancestor.derivation   AS derivation,
               length(path)          AS hops
        ORDER BY hops ASC
        """,
        {"table_name": table_name, "column_name": column_name},
    )
    return _rows(r, ("source_dataset", "source_column", "derivation", "hops"))


def get_process_chain(conn: kuzu.Connection, run_id: str) -> List[dict]:
    r = conn.execute(
        """
        MATCH (p:Process {run_id: $run_id})
        RETURN p.stage           AS stage,
               p.transform_type  AS transform_type,
               p.target_table    AS target_table,
               p.duration_ms     AS duration_ms,
               p.output_row_count AS output_row_count
        ORDER BY stage ASC
        """,
        {"run_id": run_id},
    )
    return _rows(r, ("stage", "transform_type", "target_table", "duration_ms", "output_row_count"))


# ----------------------------------------------------------------------
# Result-shape helpers
# ----------------------------------------------------------------------

def _rows(result: kuzu.QueryResult, columns: tuple) -> List[dict]:
    out: List[dict] = []
    while result.has_next():
        row = result.get_next()
        out.append({columns[i]: row[i] for i in range(len(columns))})
    return out
