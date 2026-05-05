---
name: lineage_search
description: Map natural-language business concepts to the right table and columns in the lineage graph
type: domain
trigger_patterns:
  - find column
  - where is
  - which table
  - business concept
  - data discovery
priority: 10
---

# Domain Skill: Lineage Search (Concept → Column)

Called by the `lineage_search` agent. Receives a natural-language data-discovery
query and returns ranked Column / DataSet matches from the Kuzu lineage graph.

## Inputs

| Field | Type |
|---|---|
| `query` | str — the user's question, in their own words |
| `layer_filter` | str — optional `layer_0` / `layer_1` / `layer_2`, may be empty |

## Outputs

Multi-line text listing of ranked matches, one column per line. The supervisor
parses this for synthesis.

## Concept-to-column matching strategy

Three-tier substring search, each query term checked against three properties
in this priority order:

| Priority | Property | What it captures |
|---|---|---|
| 1 | `Column.semantic_description` | Business language (e.g. "transaction volume", "customer risk"). Most useful — written by the enrichment agent. |
| 2 | `Column.column_name` | Direct identifier match (e.g. "kyc_stale_flag"). |
| 3 | `DataSet.name` | Table-level concepts ("transaction data", "FX table"). |

When `semantic_description` is empty for most columns (pipeline ran without
`TRACEX_LINEAGE_AGENTS=on`), tier 1 contributes nothing — fall back to tiers
2 and 3 so the search **never returns zero results when partial name matches
exist**.

## Scoring rules

| Score | Condition |
|---|---|
| `3` | `semantic_description` contains all query terms |
| `2` | `semantic_description` contains ≥1 query term, OR `column_name` is an exact substring of a query term (or vice versa) |
| `1` | `dataset_name` contains a query term, OR `column_name` contains a partial fragment of a term |

Return the top 10 results by descending `score`, then descending `confidence`,
then ascending `dataset_name`.

## Layer prioritization

Infer the user's likely layer from query vocabulary:

| Vocabulary signal | Prefer layer |
|---|---|
| "raw", "source", "original", "ingest", "src_*" mentioned | `layer_0` |
| "enriched", "derived", "computed", "normalized", "staged", "stg_*" mentioned | `layer_1` |
| "risk", "score", "profile", "metric", "kpi", "90-day", "rolling", "fct_*" mentioned | `layer_2` |

When no layer signal is present, sort `layer_2` first, then `layer_1`, then
`layer_0` — business concepts most often resolve to facts.

When a layer signal **is** present, that layer's matches go to the top of the
list, then the others.

## Output contract per result

Multi-line block, one block per match:

```
N. <table>.<column> [<transform_type>] conf=X.XX layer=layer_K
   Meaning: <semantic_description if present, else "(no description — agent enrichment off)">
   Definition: <expression, truncated to 120 chars>
   Why matched: <one short reason — which property hit, which terms>
```

A short trailing `SUGGEST:` line with one concrete follow-up.

When zero matches:

```
NOT_FOUND: No columns matched "<query>"
CLOSEST: <up to 5 closest partial matches as table.column>
SUGGEST: Try searching for <2-3 alternative terms>
```

## Failure Modes

| Condition | Action |
|---|---|
| Tool returns "Error: ..." | Surface verbatim on its own line. Continue with whatever was collected so far. |
| Kuzu query exception | Same — surface, do not retry. |
| Query is gibberish or asks for impact/DQ/pipeline | Return `OUT_OF_SCOPE: <one-line reason>` so the supervisor re-routes. |
| `semantic_description` empty across the board | Fall back to `column_name` + `dataset_name` matching. Note this once at the bottom: `NOTE: semantic descriptions not yet generated — set TRACEX_LINEAGE_AGENTS=on and re-run the pipeline for richer matching.` |

## Edge Cases

- **Multi-word query like "90-day transaction volume"**: split on whitespace
  AND non-alphanum (so "90-day" → `90`, `day`). Match each token. Score 3 if
  every token hits `semantic_description`.
- **Query mentions a specific table name** (`stg_transaction_normalized`):
  return that table's columns first, then text-match the rest.
- **Query like "list columns in fct_customer_risk_profile"**: detect the
  table name, call `get_columns_for_dataset` directly, return everything.
