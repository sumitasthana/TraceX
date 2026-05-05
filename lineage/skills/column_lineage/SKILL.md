---
name: column_lineage
description: Write business-language semantic descriptions for Column nodes and govern DERIVES_FROM edge writes
type: domain
trigger_patterns:
  - column node
  - derives_from
  - semantic description
priority: 10
---

# Domain Skill: Column Lineage Enrichment

You are called by the `enrichment` agent after a `ColumnLineageMap` has been
ingested into Kuzu. Your job is to read the column node and its upstream
chain, then write a single-sentence business-language semantic description
back to the node.

## Inputs

| Field | Type | Source |
|---|---|---|
| `column_map` | Resolved ColumnLineageMap (confidence ≥ 0.5) | Caller |

## Outputs

A single JSON object: `{updated, table, column, semantic_description}`.

## Node Properties

| Property | Where written |
|---|---|
| `column_name` | `_upsert_column` — set on first MERGE, never overwritten |
| `dataset_name` | `_upsert_column` — set on first MERGE, never overwritten |
| `expression` | `_upsert_column_from_map` — the per-column SQL fragment, rewritten on every ingest |
| `transform_type` | `_upsert_column_from_map` — enum value, rewritten on every ingest |
| `confidence` | `_upsert_column_from_map` — sqlglot=1.0 / agent=0.8 or 0.5 |
| `data_type` | `_upsert_column_from_map` — inferred from sqlglot or DuckDB schema |
| `sql_hash` | `_upsert_column_from_map` — SHA-256 of the per-column expression |
| `semantic_description` | this skill (`update_column_node`) — written ONLY by enrichment agent |
| `derivation` | legacy free-form text from the JSONL ingest path; preserved untouched |

## DERIVES_FROM edge semantics

- Direction: **target column → source column**. Reading "x DERIVES_FROM y"
  means "x's value is computed from y's value".
- Cardinality: one edge per source column. If a target column has 5 source
  columns, write 5 DERIVES_FROM edges.
- Self-references are forbidden. Skip any edge where target and source refer
  to the same `(dataset, column)` tuple.
- Idempotency: `_safe_merge_rel` skips creating an edge that already exists.

## Procedure

### Step 1: Read current node state

Call `read_column_node(table, column)` to see what is currently stored,
including `expression`, `transform_type`, and any prior
`semantic_description`. **If `semantic_description` is already populated AND
the new map's `confidence` is not strictly higher than the stored value, do
not overwrite — return `{updated: false, ...}`.**

### Step 2: Read the upstream chain

Call `get_upstream_columns(table, column)` to retrieve the immediate
DERIVES_FROM ancestors with their expressions and (where present) prior
semantic descriptions. The chain reveals the full derivation context.

### Step 3: Write a business-language description

Write **one sentence**, present tense, in the language a business user
(compliance officer, risk analyst, branch manager) would read in a data
catalog. The sentence must capture **what the column means** to that user,
not how it was computed.

**Good**: "Customer's rolling 90-day net transaction volume in USD,
excluding reversals and zero-amount rows."

**Bad**: "SUM of net_amount_usd filtered by txn_date >= reference_date -
INTERVAL 90 DAY."

Rules:

1. Never restate the SQL expression.
2. Never use technical terms (CTE, JOIN, FILTER, ASOF, COALESCE, percentile)
   unless unavoidable for meaning.
3. Use units explicitly when relevant (USD, days, customers, transactions).
4. State the time window when the column is time-bounded.
5. State exclusions when the column filters specific rows (e.g.
   "excluding reversals").
6. Boolean flags: state what `true` means.
   Example: "True when KYC review is older than 365 days OR status is EXPIRED."
7. Aggregates: state the grain (per-customer, per-account, per-day).

### Step 4: Persist

Call `update_column_node(table, column, semantic_description, confidence)`
with the sentence from Step 3 and the `confidence` from the input map.

Do not write if `confidence < 0.5`. Return `{updated: false, ...,
semantic_description: ""}` and explain the skip in
`semantic_description` of the response (NOT the node).

## Confidence Scoring Rubric

| Confidence | Meaning | Source |
|---|---|---|
| `1.0` | sqlglot fully resolved, single unambiguous source | sql_parser.py |
| `0.8` | Agent resolved with full schema context, all sources confirmed | sql_parser agent |
| `0.5` | Agent resolved with partial context, or AMBIGUOUS runtime-branch | sql_parser agent |

## Idempotency Contract

Same `sql_hash` + same source schema → identical graph state. Concretely:

- Re-running ingest of the same `StageLineageManifest` produces the same
  set of nodes and edges.
- If a Process node with the matching `sql_hash` already exists, column
  nodes whose `confidence` would not improve are skipped (no overwrite).
- The enrichment agent **does** re-run on every ingest to allow improvements
  in description quality, but only writes back if Step 1's overwrite-guard
  permits.

## Output Contract

Return **exactly one** JSON object. **No prose. No markdown. No backticks.**

```
{"updated": true, "table": "...", "column": "...", "semantic_description": "..."}
```

If the column is out-of-scope for enrichment (e.g. confidence < 0.5):

```
{"updated": false, "table": "...", "column": "...", "semantic_description": "skipped: <reason>"}
```

If the request is fundamentally outside enrichment scope:

    OUT_OF_SCOPE: <one-line reason>

## Failure Modes

| Condition | Action |
|---|---|
| `read_column_node` returns "not found" | Do not write — the upstream graph_builder must run first |
| `update_column_node` raises | Surface the error verbatim, return `updated: false` |
| Upstream chain empty (no DERIVES_FROM ancestors) | Still write a description based on `expression` alone |
| Bedrock throttle / 5xx | Surface error verbatim, do not retry |
