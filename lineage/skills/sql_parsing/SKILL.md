---
name: sql_parsing
description: Resolve ambiguous DuckDB SQL columns to their original source table.column origins
type: domain
trigger_patterns:
  - ambiguous column
  - cte resolution
  - duckdb sql
priority: 10
---

# Domain Skill: DuckDB SQL Column Resolution

You are called by the `sql_parser` agent to resolve `ColumnLineageMap` entries
that the deterministic sqlglot walker could not place. Your job is to trace
each unresolved expression back to its **original source table.column** and
classify the transform type.

## Inputs

| Field | Type | Source |
|---|---|---|
| `column_map` | ColumnLineageMap JSON with `ambiguous=True` | Caller |
| `full_sql` | The complete `CREATE OR REPLACE TABLE ... AS ...` SQL | Caller |
| `target_table` | The output table name | Caller |
| `source_tables` | Whitelist of physical source tables for this stage | Caller |

## Outputs

A single updated `ColumnLineageMap` JSON with `ambiguous=False`,
populated `sources`, classified `transform_type`, and a confidence score.

## Procedure

### Step 1: Inspect available schemas before guessing

Before classifying anything, call `get_table_schema(t)` for every `t` in
`source_tables`. **Never fabricate column names** — if a column does not
appear in any returned schema, mark the map `confidence=0.5` and explain in
`semantic_description` what was missing.

If the SQL contains CTEs, call `get_cte_definition(cte_name, full_sql)` for
each CTE the target column references, and walk the chain until you reach
a physical source table. Never report a CTE alias as the source table.

### Step 2: CTE chain resolution

When a column passes through N CTE hops, trace to the original source.

**Example**: target `risk_score` selects from CTE `with_pct`, which selects
`*` from `with_ratios`, which derives `international_txn_ratio` from
`per_customer.intl_txn_count / per_customer.total_txn_count`. `per_customer`
in turn aggregates `customer_txns.is_international` and `customer_txns.txn_id`,
both of which originate in `stg_transaction_normalized`. Therefore the
resolved sources for `risk_score` include
`stg_transaction_normalized.is_international` and
`stg_transaction_normalized.txn_id`, **not** `with_pct.*`.

If a CTE applies a transform (cast, filter, aggregate) to the column, record
the cumulative `transform_type` of the deepest non-passthrough hop.

### Step 3: ASOF JOIN semantics

`t ASOF LEFT JOIN fx ON t.currency = fx.from_currency AND t.txn_date >= fx.rate_date`

- Columns selected from `t` originate in `t`.
- Columns selected from `fx` originate in the right-side table (e.g.
  `stg_fx_resolved`).
- Join-condition columns (`currency`, `txn_date`, `from_currency`,
  `rate_date`) originate on **the side they are physically on**, not on
  whichever side the optimizer chose.

### Step 4: DuckDB-specific constructs

| Expression shape | transform_type | Sources |
|---|---|---|
| `list_filter(col_list, lambda x: ...)` | TRANSFORM | the column populating `col_list` |
| `PERCENT_RANK() OVER (ORDER BY col)` | WINDOW | `col` |
| `ROW_NUMBER() OVER (PARTITION BY a ORDER BY b)` | WINDOW | `a`, `b` |
| `DATE_DIFF('day', a, b)` | TRANSFORM | `a`, `b` |
| `EXTRACT(YEAR FROM col)` | TRANSFORM | `col` |
| `AGE(d1, d2)` | TRANSFORM | `d1`, `d2` |
| `INTERVAL N DAY` | CONSTANT | (none) |
| String literal, numeric literal | CONSTANT | (none) |
| `CURRENT_DATE`, `CURRENT_TIMESTAMP` | CONSTANT | (none) |
| `t.a || ' ' || t.b` (concat) | TRANSFORM | `t.a`, `t.b` |
| `CAST(col AS T)` | TRANSFORM | `col` |
| `COALESCE(a)` (single arg) | PASSTHROUGH | `a` |

### Step 5: Multi-source CASE WHEN

`CASE WHEN t.currency = 'USD' THEN 1.0 WHEN fx.rate IS NOT NULL THEN fx.rate ELSE fb.first_rate END`

- `transform_type = AMBIGUOUS`
- `sources = [{table: t, col: currency}, {table: fx, col: rate}, {table: fb, col: first_rate}]`
- `confidence = 0.5` (runtime-conditional branch selection — which source
  contributes is decided per-row at execution time)
- In `semantic_description`, note: "Branch selection is runtime-conditional
  on the input data."

### Step 6: COALESCE across tables

`COALESCE(aj.asof_rate, fb.first_rate)`

- `transform_type = AMBIGUOUS`
- `sources = [aj.asof_rate, fb.first_rate]` (resolve `aj` and `fb` to their
  underlying source tables via Step 1)
- `confidence = 0.5`

### Step 7: transform_type classification rules

Apply these in order; first match wins.

1. Expression is a literal or `CURRENT_*` → `CONSTANT`, sources empty.
2. Expression is exactly `t.col` or `col` and the output column has the same
   name → `PASSTHROUGH`.
3. Expression is exactly `t.col AS new_name` (no further operation) →
   `RENAME`, source is `t.col`.
4. Expression contains `SUM(`, `COUNT(`, `AVG(`, `MIN(`, `MAX(`, `MODE(`,
   `STRING_AGG(`, `LIST(` → `AGGREGATE`.
5. Expression contains `OVER (` → `WINDOW`.
6. Expression contains `CASE WHEN` referencing columns from ≥2 tables, or
   `COALESCE(` over columns from ≥2 tables → `AMBIGUOUS`.
7. Otherwise → `TRANSFORM`.

### Step 8: Confidence scoring

| Confidence | When to use |
|---|---|
| `1.0` | Reserved for the deterministic sqlglot walker; agents do not emit 1.0. |
| `0.8` | All sources confirmed against `get_table_schema` output. No remaining unknowns. |
| `0.5` | Could not confirm one or more sources, or AMBIGUOUS branch selection. |

## Output Contract

Return **exactly one** `ColumnLineageMap` JSON object. **No prose. No markdown.
No backticks. No fenced code blocks.** Raw JSON only.

```
{
  "target_table": "...",
  "target_column": "...",
  "sources": [
    {"source_table": "...", "source_column": "...", "expression": "...", "transform_type": "..."}
  ],
  "full_expression": "...",
  "ambiguous": false,
  "semantic_description": "",
  "confidence": 0.8,
  "data_type": "...",
  "sql_hash": "..."
}
```

If the request is genuinely outside SQL-resolution scope (e.g. asks for a
business explanation), return exactly:

    OUT_OF_SCOPE: <one-line reason>

## Failure Modes

| Condition | Action |
|---|---|
| `get_table_schema` returns "Error: ..." for every source table | confidence=0.5, sources=[], explain in semantic_description |
| CTE not found in SQL | confidence=0.5, fall back to direct table reference if present |
| Expression cannot be parsed | confidence=0.5, transform_type=AMBIGUOUS, sources=[] |
| Bedrock throttle / 5xx during a tool call | surface error verbatim, do not retry |
