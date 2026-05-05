---
name: impact_analysis
description: Determine what downstream columns and processes break when a column is renamed, type-changed, or dropped
type: domain
trigger_patterns:
  - impact
  - breaking change
  - rename
  - downstream
priority: 10
---

# Domain Skill: Column Change Impact Analysis

You are called by the `impact_analyst` agent. Given a proposed change to a
single column (`RENAME`, `TYPE_CHANGE`, or `DROP`), walk the lineage graph
and identify every downstream column or process that breaks.

## Inputs

| Field | Type |
|---|---|
| `changed_table` | str |
| `changed_column` | str |
| `change_type` | "RENAME" \| "TYPE_CHANGE" \| "DROP" |

## Outputs

A structured impact report JSON, sorted by severity descending then by
table name ascending.

## What constitutes a breaking change

### `RENAME` — old column name disappears, new name appears

- Breaks every downstream column whose `DERIVES_FROM` edge points at the
  old `(table, column)`.
- Breaks every Process whose stored SQL references `old_table.old_column`
  (literal text match).
- Does **not** break columns that read from a different column on the same
  table.

### `TYPE_CHANGE` — column data type changes

- Breaks downstream columns whose `expression` performs **type-sensitive
  operations** on this column. Type-sensitive operations include:
    - String concatenation with `||`, `CONCAT`, string functions
    - Date arithmetic (`DATE_DIFF`, `AGE`, `+ INTERVAL`)
    - Numeric arithmetic (`+`, `-`, `*`, `/`, `ROUND`, `SUM`, `AVG`)
    - Boolean coercion (`CASE WHEN col`, `WHERE col`)
    - Casts that assume the old type
- Does **not** break columns with `transform_type IN (PASSTHROUGH, RENAME)`
  — they propagate the new type unchanged.
- Process nodes do not break for TYPE_CHANGE unless one of their output
  columns breaks.

### `DROP` — column is removed

- Breaks **all** downstream columns unconditionally.
- Breaks every Process node with `INPUT_TO` an affected DataSet whose SQL
  references the dropped column.

## Impact severity rules

| Severity | When to apply |
|---|---|
| `CRITICAL` | Affected column lives in `fct_customer_risk_profile`, OR the column is `risk_score` / `risk_tier` (the regulatory anchors) |
| `HIGH` | Affected column lives in any `stg_*` table, OR the affected node is a Process that feeds a CRITICAL DataSet |
| `LOW` | Affected column lives in a `src_*` table passthrough, OR a column that has no further downstream DERIVES_FROM edges |

If a column qualifies for multiple severities (e.g. lives in `stg_*` AND has
no downstream edges), apply the **higher** severity.

## Procedure

### Step 1: Gather the full downstream chain

Call `get_full_downstream_chain(changed_table, changed_column, max_hops=10)`.
This returns every descendant column with its hop count, expression, and
transform_type. Stop a branch when a node has no further downstream edges.

### Step 2: Resolve process dependencies

Call `get_processes_reading_table(changed_table)` to enumerate every Process
node with `INPUT_TO` this DataSet. Each Process is a candidate breakage
target for `RENAME` and `DROP` (Process nodes do not break for TYPE_CHANGE
on their own — only via column-level fallout).

### Step 3: Per-column type-sensitivity check (TYPE_CHANGE only)

For each affected column from Step 1, call
`get_column_expression(table, column)` and inspect the returned expression:

- If `transform_type IN (PASSTHROUGH, RENAME)` → mark as **propagating**, not
  breaking. Drop from the impact list.
- If the expression contains a type-sensitive operation per the table
  above → confirmed breaking. Record the operation in `reason`.
- If neither → conservative default is **breaking** (record `reason` as
  "type-sensitivity unverified").

### Step 4: Apply severity rules

For each remaining breakage entry, assign severity per the table above.

### Step 5: Compose the report

Build the report. Sort impacts by `severity` (CRITICAL > HIGH > LOW), then
by `affected_table` ascending, then by `affected_column` ascending.

## Output Contract

Return **exactly one** JSON object. **No prose. No markdown. No backticks.**

```
{
  "changed_table": "...",
  "changed_column": "...",
  "change_type": "RENAME",
  "total_affected": 3,
  "impacts": [
    {
      "affected_table": "...",
      "affected_column": "...",
      "stage": "...",
      "severity": "CRITICAL",
      "expression": "...",
      "reason": "..."
    }
  ]
}
```

If the request is outside impact-analysis scope:

    OUT_OF_SCOPE: <one-line reason>

## Failure Modes

| Condition | Action |
|---|---|
| `changed_column` does not exist in the graph | Return `{total_affected: 0, impacts: []}` with a note in a top-level `warning` field |
| Graph traversal returns 0 hops | Return `{total_affected: 0, impacts: []}` — column is a true leaf |
| Tool call returns "Error: ..." | Surface verbatim, return partial report with what was collected so far |
| Bedrock throttle / 5xx | Surface error verbatim, do not retry |

## Edge Cases

- **Cycle in DERIVES_FROM**: stop at first revisit of any `(table, column)`
  pair within a single traversal.
- **Multiple Process nodes for the same stage** (different runs): treat each
  as a separate impact entry with its `run_id` distinguishing them.
- **AMBIGUOUS source columns**: an affected column whose own
  `transform_type=AMBIGUOUS` is conservatively counted as breaking for any
  upstream change.
