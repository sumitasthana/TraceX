"""Pure-function unit tests for `merge_lineage`.

No DuckDB, no Kuzu, no Bedrock. Tests the precedence-table logic in isolation.

Run with:
    .\\.venv\\Scripts\\python.exe -m unittest lineage.catalog.tests.test_merge
"""
from __future__ import annotations

import unittest

from lineage.catalog.client import (
    CatalogEdge,
    DivergenceEvent,
    REVIEW_PENDING,
    REVIEW_RATIFIED,
    SOURCE_AGENT_INFERRED,
    SOURCE_CATALOG,
    SOURCE_SQLGLOT,
    SOURCE_UNRESOLVED,
)
from lineage.catalog.merge import merge_lineage
from lineage.models import ColumnLineageEdge, ColumnLineageMap, TransformType


def _sg_map(target_table, target_column, sources, *, ambiguous=False, conf=1.0,
            sql_hash="HASH_X", expr="expr"):
    return ColumnLineageMap(
        target_table=target_table,
        target_column=target_column,
        sources=[
            ColumnLineageEdge(
                source_table=t, source_column=c,
                expression=expr, transform_type=TransformType.PASSTHROUGH,
            ) for (t, c) in sources
        ],
        full_expression=expr,
        ambiguous=ambiguous,
        confidence=conf,
        sql_hash=sql_hash,
    )


def _cat_edge(tt, tc, st, sc, *, sql_hash="HASH_X",
              review=REVIEW_RATIFIED, source=SOURCE_CATALOG, conf=1.0):
    return CatalogEdge(
        target_table=tt, target_column=tc,
        source_table=st, source_column=sc,
        expression="expr", transform_type="PASSTHROUGH",
        confidence=conf, source=source, review_state=review,
        sql_hash=sql_hash,
    )


class MergeLineageTests(unittest.TestCase):

    # ── Branch 1: catalog wins ───────────────────────────────────────────

    def test_catalog_hit_matching_sql_hash(self):
        """Catalog has it (matching sql_hash), sqlglot doesn't → catalog wins."""
        catalog = [_cat_edge("stg_fx_resolved", "rate", "src_fx_rate", "rate",
                             sql_hash="HASH_X")]
        # sqlglot didn't produce a map for this column at all
        sqlglot = []
        agent = {}
        resolved, divs = merge_lineage(catalog, sqlglot, agent, "HASH_X")

        self.assertEqual(len(resolved), 1)
        self.assertEqual(len(divs), 0)
        m = resolved[0]
        self.assertEqual(m.source, SOURCE_CATALOG)
        self.assertEqual(m.review_state, REVIEW_RATIFIED)
        self.assertEqual(
            [(s.source_table, s.source_column) for s in m.sources],
            [("src_fx_rate", "rate")],
        )

    def test_catalog_and_sqlglot_agree(self):
        """Catalog has it, sqlglot agrees → catalog wins, no divergence."""
        catalog = [_cat_edge("stg_fx_resolved", "rate", "src_fx_rate", "rate")]
        sqlglot = [_sg_map("stg_fx_resolved", "rate", [("src_fx_rate", "rate")])]
        resolved, divs = merge_lineage(catalog, sqlglot, {}, "HASH_X")

        self.assertEqual(len(divs), 0)
        self.assertEqual(resolved[0].source, SOURCE_CATALOG)
        self.assertEqual(resolved[0].review_state, REVIEW_RATIFIED)

    def test_catalog_hit_sql_hash_drift_but_sources_align(self):
        """Catalog stale (sql_hash mismatch) but sources still align — catalog wins."""
        catalog = [_cat_edge("stg_fx_resolved", "rate", "src_fx_rate", "rate",
                             sql_hash="HASH_OLD")]
        sqlglot = [_sg_map("stg_fx_resolved", "rate", [("src_fx_rate", "rate")])]
        resolved, divs = merge_lineage(catalog, sqlglot, {}, "HASH_NEW")

        self.assertEqual(len(divs), 0)
        self.assertEqual(resolved[0].source, SOURCE_CATALOG)

    # ── Branch 1b: divergence ────────────────────────────────────────────

    def test_catalog_disagrees_with_sqlglot_emits_divergence(self):
        """Catalog points at A, sqlglot points at B → sqlglot wins, divergence emitted."""
        catalog = [_cat_edge("stg_fx_resolved", "rate", "src_fx_rate", "rate_source")]
        sqlglot = [_sg_map("stg_fx_resolved", "rate", [("src_fx_rate", "rate")])]
        resolved, divs = merge_lineage(catalog, sqlglot, {}, "HASH_X")

        self.assertEqual(len(resolved), 1)
        self.assertEqual(len(divs), 1)
        m = resolved[0]
        self.assertEqual(m.source, SOURCE_SQLGLOT)
        self.assertEqual(m.review_state, REVIEW_RATIFIED)
        self.assertEqual(
            [(s.source_table, s.source_column) for s in m.sources],
            [("src_fx_rate", "rate")],
        )
        self.assertEqual(divs[0].catalog_sources, [("src_fx_rate", "rate_source")])
        self.assertEqual(divs[0].sqlglot_sources, [("src_fx_rate", "rate")])

    # ── Branch 2: sqlglot/agent path ─────────────────────────────────────

    def test_no_catalog_sqlglot_clean(self):
        """No catalog. sqlglot resolved, conf=1.0 → source=sqlglot, ratified."""
        sqlglot = [_sg_map("stg_x", "col", [("src_x", "col")])]
        resolved, divs = merge_lineage([], sqlglot, {}, "HASH_X")

        self.assertEqual(len(divs), 0)
        self.assertEqual(resolved[0].source, SOURCE_SQLGLOT)
        self.assertEqual(resolved[0].review_state, REVIEW_RATIFIED)

    def test_no_catalog_sqlglot_ambiguous_agent_resolves(self):
        """sqlglot ambiguous → agent fills in → source=agent_inferred, pending_review."""
        sqlglot = [_sg_map("stg_x", "col", [], ambiguous=True, conf=0.5)]
        agent = {
            ("stg_x", "col"): _sg_map("stg_x", "col", [("src_x", "col")],
                                      ambiguous=False, conf=0.8),
        }
        resolved, divs = merge_lineage([], sqlglot, agent, "HASH_X")

        self.assertEqual(len(divs), 0)
        self.assertEqual(resolved[0].source, SOURCE_AGENT_INFERRED)
        self.assertEqual(resolved[0].review_state, REVIEW_PENDING)
        self.assertEqual(
            [(s.source_table, s.source_column) for s in resolved[0].sources],
            [("src_x", "col")],
        )

    def test_no_catalog_no_agent_unresolved(self):
        """All three absent (sqlglot ambiguous + empty sources, no agent) → unresolved."""
        sqlglot = [_sg_map("stg_x", "col", [], ambiguous=True, conf=0.5)]
        resolved, divs = merge_lineage([], sqlglot, {}, "HASH_X")

        self.assertEqual(len(divs), 0)
        self.assertEqual(resolved[0].source, SOURCE_UNRESOLVED)
        self.assertEqual(resolved[0].review_state, REVIEW_PENDING)
        self.assertEqual(resolved[0].sources, [])

    # ── Catalog-only entries (no sqlglot output for the column) ─────────

    def test_catalog_entry_with_no_matching_sqlglot(self):
        """Catalog has the column, sqlglot didn't produce a map → catalog wins."""
        catalog = [_cat_edge("custom", "col", "src_x", "col")]
        resolved, divs = merge_lineage(catalog, [], {}, "HASH_X")

        self.assertEqual(len(divs), 0)
        self.assertEqual(resolved[0].source, SOURCE_CATALOG)
        self.assertEqual(resolved[0].target_table, "custom")
        self.assertEqual(resolved[0].target_column, "col")

    # ── Multi-source catalog rows folded into one map ───────────────────

    def test_multiple_catalog_edges_for_one_target(self):
        """Two ratified catalog rows for the same target → both in cm.sources."""
        catalog = [
            _cat_edge("stg_x", "col", "src_a", "col"),
            _cat_edge("stg_x", "col", "src_b", "col"),
        ]
        resolved, _ = merge_lineage(catalog, [], {}, "HASH_X")

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].source, SOURCE_CATALOG)
        srcs = sorted([(s.source_table, s.source_column) for s in resolved[0].sources])
        self.assertEqual(srcs, [("src_a", "col"), ("src_b", "col")])


if __name__ == "__main__":
    unittest.main()
