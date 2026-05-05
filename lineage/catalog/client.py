"""Catalog protocol — the seam an external catalog (DataHub / OpenMetadata)
would plug into. Everything outside `local.py` depends on this protocol, never
on the concrete `LocalCatalog`.

The shapes here are deliberately simple dataclasses so callers can pass them
through JSON / structlog without translation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable

# ── Provenance enums (open string types — kept as constants for greppability) ──

SOURCE_CATALOG = "catalog"
SOURCE_SQLGLOT = "sqlglot"
SOURCE_AGENT_INFERRED = "agent_inferred"
SOURCE_UNRESOLVED = "unresolved"
ALL_SOURCES = {SOURCE_CATALOG, SOURCE_SQLGLOT, SOURCE_AGENT_INFERRED, SOURCE_UNRESOLVED}

REVIEW_RATIFIED = "ratified"
REVIEW_PENDING = "pending_review"
REVIEW_ABSENT = "absent"
ALL_REVIEW_STATES = {REVIEW_RATIFIED, REVIEW_PENDING, REVIEW_ABSENT}


@dataclass
class CatalogEdge:
    """One source-side reference for an output column, as stored in the catalog."""
    target_table: str
    target_column: str
    source_table: str
    source_column: str
    expression: str = ""
    transform_type: str = ""
    confidence: float = 0.0
    source: str = SOURCE_UNRESOLVED
    review_state: str = REVIEW_PENDING
    sql_hash: str = ""
    ratified_by: str = ""
    ratified_at: str = ""
    computed_at: str = ""


@dataclass
class DivergenceEvent:
    """Emitted by the merge step when a ratified catalog edge disagrees with
    the live sqlglot output. The catalog row gets flipped to pending_review;
    sqlglot wins the run; the steward decides via UI/CLI which version is
    authoritative going forward."""
    target_table: str
    target_column: str
    catalog_sources: List[tuple[str, str]] = field(default_factory=list)   # (table, column)
    sqlglot_sources: List[tuple[str, str]] = field(default_factory=list)
    catalog_sql_hash: str = ""
    current_sql_hash: str = ""
    reason: str = ""


@runtime_checkable
class CatalogClient(Protocol):
    """Protocol every catalog implementation must satisfy.

    All methods MUST be safe to call when the underlying store is unavailable —
    return safe empty values, log a warning, never raise.
    """

    # ── Reads ──
    def get_column_lineage(self, table: str) -> List[CatalogEdge]: ...

    def get_certification(self, table: str) -> Optional[str]: ...
    """Return 'P1' / 'P2' / 'P3' or None when no certification exists."""

    def get_review_state(self, table: str, column: str) -> str: ...
    """Return one of `ratified`, `pending_review`, `absent`."""

    def list_pending_reviews(self) -> List[CatalogEdge]: ...

    def list_certifications(self) -> List[dict]: ...

    def list_activity(self, limit: int = 20) -> List[dict]: ...

    # ── Writes ──
    def emit_lineage(self, manifest, source: str, auto_ratify: bool) -> None: ...
    """Write one row per source edge in every column_map of the manifest.

    `source` is the resolved provenance for the manifest's columns
    (`catalog`, `sqlglot`, `agent_inferred`, `unresolved`); rows that
    qualify for `auto_ratify` (Phase G uses this for sqlglot conf=1.0)
    enter as `ratified`, the rest as `pending_review`.
    """

    def emit_description(self, table: str, column: str, description: str,
                         source: str = SOURCE_AGENT_INFERRED) -> None: ...

    def ratify(self, table: str, column: str, actor: str, reason: str = "") -> None: ...

    def reject(self, table: str, column: str, actor: str, reason: str = "") -> None: ...

    def downgrade_to_pending(self, target_table: str, target_column: str,
                             reason: str = "") -> None: ...
    """Used by the divergence path to flip a ratified row back to pending_review."""

    def health(self) -> dict: ...
    """Return {ok: bool, lineage_count: int, certification_count: int}."""


__all__ = [
    "CatalogClient",
    "CatalogEdge",
    "DivergenceEvent",
    "SOURCE_CATALOG", "SOURCE_SQLGLOT", "SOURCE_AGENT_INFERRED", "SOURCE_UNRESOLVED",
    "ALL_SOURCES",
    "REVIEW_RATIFIED", "REVIEW_PENDING", "REVIEW_ABSENT",
    "ALL_REVIEW_STATES",
]
