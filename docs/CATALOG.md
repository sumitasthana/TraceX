# TraceX — Local Catalog

The catalog layer makes TraceX **catalog-first**: every column lookup the
pipeline does hits the local catalog before sqlglot, and every AI-inferred
edge enters as `pending_review` until a steward ratifies it. The catalog is
the seam where DataHub or OpenMetadata would later plug in — for now, a
DuckDB-backed implementation lives in `lineage/catalog/local.py`.

## Why catalog-first

Most "AI lineage" platforms learn from data and emit edges with no audit
trail. That's fine for a synthetic demo and unsafe for a regulated dataset:
when a steward sees `risk_score derives from net_amount_usd`, they need to
know whether that came from a parser, an LLM, or a human ratifying it.
Without provenance, every inferred edge is treated the same as a verified
one — risky.

Catalog-first inverts the flow. The catalog is the source of truth. The
pipeline parses SQL fresh on every run, but it only **upgrades** the
catalog: ratified entries win unless sqlglot disagrees with them, in which
case a divergence event is emitted and the steward decides whether the
catalog is stale or the new sqlglot view is wrong.

The result is that every column you see in the Lineage Explorer carries
visible provenance — `[CATALOG ✓ ratified]`, `[SQLGLOT ✓ ratified]`,
`[AGENT ⏱ pending]`, or `[UNRESOLVED]` — and the impact analyst gates its
answers on review state for P1 (regulatory) tables.

## Phase A → G in `manifest_builder.build_and_ingest`

```text
┌─ Phase A ──────────────────────────────────────────────────┐
│ catalog.get_column_lineage(target_table)                   │
└──────────────┬─────────────────────────────────────────────┘
               │
┌─ Phase B ────▼─────────────────────────────────────────────┐
│ sql_parser.parse(sql, target_table, source_tables)         │
└──────────────┬─────────────────────────────────────────────┘
               │
┌─ Phase C ────▼─────────────────────────────────────────────┐
│ sql_parser_agent — only for sqlglot-ambiguous columns      │
│ (skipped for columns with a ratified catalog hit)          │
└──────────────┬─────────────────────────────────────────────┘
               │
┌─ Phase D ────▼─────────────────────────────────────────────┐
│ merge_lineage(catalog_edges, sqlglot_maps, agent_maps)     │
│   → resolved column_maps (each tagged with source +        │
│     review_state via the precedence table)                 │
│   → divergence events for ratified-vs-sqlglot mismatches   │
└──────────────┬─────────────────────────────────────────────┘
               │
┌─ Phase E ────▼─────────────────────────────────────────────┐
│ graph_builder.ingest_stage_manifest — Kuzu writes carry    │
│ source/review_state; downgrades from ratified are blocked. │
└──────────────┬─────────────────────────────────────────────┘
               │
┌─ Phase F ────▼─────────────────────────────────────────────┐
│ enrichment agent → semantic_description (per resolved map) │
└──────────────┬─────────────────────────────────────────────┘
               │
┌─ Phase G ────▼─────────────────────────────────────────────┐
│ catalog.emit_lineage(manifest) — fire-and-forget. sqlglot  │
│ conf=1.0 maps auto-ratify; everything else stays pending.  │
└────────────────────────────────────────────────────────────┘
```

## Provenance precedence

| Priority | Source | When | `source` | `review_state` |
|---|---|---|---|---|
| 1 | Catalog (sql_hash matches) | catalog has a ratified row, sql_hash equals current | `catalog` | `ratified` |
| 2 | Catalog (sql_hash drifts, sources align) | catalog has a ratified row, sources still match sqlglot | `catalog` | `ratified` |
| 3 | sqlglot deterministic | sqlglot returned ambiguous=False, conf=1.0 | `sqlglot` | `ratified` (auto-ratified by Phase G) |
| 4 | sql_parser agent | sqlglot ambiguous, agent resolved | `agent_inferred` | `pending_review` |
| 5 | None | nothing resolved | `unresolved` | `pending_review` |

## Divergence rule

A **divergence** is when priority 1/2 catalog rows exist for a column AND
priority 3 sqlglot output disagrees on source columns. When detected:

1. sqlglot wins for this run — Kuzu gets the new view.
2. The catalog row is flipped from `ratified` back to `pending_review`.
3. A `lineage_divergence` event is emitted with both source-column lists.
4. The Catalog UI's pending queue shows the entry so the steward sees it.

The steward decides:

- **Catalog was stale.** Click Ratify on the new sqlglot view. The catalog
  is updated; future runs hit it again.
- **sqlglot is wrong** (rare — usually a CTE-walk bug). Open
  `lineage/catalog/local.py` and re-INSERT the correct catalog row by hand,
  or use `python cli.py catalog reject` followed by `ratify` on the right
  sources.

## CLI

```powershell
# One-shot setup of certifications + the demo ratified entry
python cli.py catalog seed

# Snapshot
python cli.py catalog status

# Pending-review queue
python cli.py catalog list-pending

# Ratify or reject an AI-inferred edge
python cli.py catalog ratify  fct_customer_risk_profile.risk_score --reason "verified against spec"
python cli.py catalog reject  stg_x.suspect_col                    --reason "false positive — agent overreach"
```

The CLI defaults `actor` to `$USER` / `$USERNAME`.

## Ratifying from the UI

Open `#/catalog`. Pending review queue shows every AI-inferred edge with a
**Ratify** and **Reject** button. Both prompt for an optional reason and
hit `POST /api/catalog/{ratify|reject}`. Ratifying flips the catalog row
to `ratified`; on the next pipeline run, Phase A will hit the catalog and
the column shows `[CATALOG ✓ ratified]` everywhere it appears.

## Profile gating

`get_full_downstream_chain(table, column, include_unratified=False)`
filters out edges whose target column lives in a P1-certified table AND has
`review_state=pending_review`. The gating prevents a steward from being
told "this rename will affect risk_score" when the link to risk_score is
itself an unratified AI guess.

To override: ask the chat agent again with "include unratified" / "show
everything", and the supervisor will re-invoke with
`include_unratified=True`.

## Briefing principles addressed

| Principle | Surface |
|---|---|
| Catalog-first, model-second | Phase A short-circuits sqlglot/agent for ratified hits |
| Visible provenance | `source` + `review_state` chips next to every column reference |
| Pending-review state machine | Catalog UI ratify/reject queue + CLI parity |
| Profile gating | `impact_analyst.get_full_downstream_chain(include_unratified=False)` |
| Divergence triggers re-review | merge_lineage emits `lineage_divergence`, downgrades the catalog row |

## Verification (4 of the 9 in-spec tests)

1. **Reversibility.** Run `pipeline` with `TRACEX_CATALOG=off` and again
   with `=on` and an empty catalog. Kuzu node/edge counts must match.
2. **Catalog-hit.** After `catalog seed`, run the pipeline. Logs must
   contain `lineage_catalog_hit` for `stg_fx_resolved.rate`, AND no
   agent-resolution event for that column.
3. **Pending review.** With only certifications seeded, pending queue
   contains every agent-inferred edge from `fct_customer_risk_profile`.
   Ratify `risk_score`; re-run pipeline; the inspector now shows
   `source=catalog`.
4. **Divergence.** Hand-insert a wrong catalog row
   (`stg_fx_resolved.rate ← src_fx_rate.rate_source`). Re-run stage 01.
   `lineage_divergence` log; pending queue shows the row flipped back.

## Disabling

```powershell
$env:TRACEX_CATALOG = "off"
python cli.py pipeline
```

When off:

- Phase A is skipped, Phase D treats catalog as empty, Phase G is a no-op.
- All Kuzu writes get `source=sqlglot|agent_inferred` and `review_state=ratified`
  (the legacy behaviour — everything was effectively ratified before this change).
- The Catalog UI tab still renders but shows a banner.

## Layout

```text
lineage/catalog/
  __init__.py
  client.py        — CatalogClient protocol + CatalogEdge / DivergenceEvent
  local.py         — LocalCatalog (DuckDB)
  merge.py         — pure merge_lineage function
  seed.py          — `python cli.py catalog seed`
  tests/
    __init__.py
    test_merge.py  — 9 unit tests
```

DuckDB tables (live in `data/tracex_layer0.duckdb`):

```text
catalog_lineage         (target_table, target_column, source_table, source_column,
                         expression, transform_type, confidence, source,
                         review_state, ratified_by, ratified_at, sql_hash, computed_at)
catalog_certification   (table_name, profile, certified_by, certified_at, notes)
catalog_review_log      (ts, action, table_name, column_name, actor,
                         from_state, to_state, reason)
catalog_descriptions    (table_name, column_name, description, source,
                         review_state, ratified_by, ratified_at, computed_at)
```
