"""
TraceX — JanusGraph health check + schema bootstrap
Run after docker-compose up before any ingestion.

Usage:
    python healthcheck.py

Exit 0 = JanusGraph is up, Gremlin endpoint accepting, schema verified.
Exit 1 = something is wrong, check output.
"""

import sys
import time
import structlog
from gremlin_python.driver import client, serializer
from gremlin_python.driver.protocol import GremlinServerError

log = structlog.get_logger()

GREMLIN_URL     = "ws://localhost:8182/gremlin"
MAX_RETRIES     = 15
RETRY_INTERVAL  = 10  # seconds


def get_client():
    return client.Client(
        GREMLIN_URL,
        "g",
        message_serializer=serializer.GraphSONSerializersV2d0(),
    )


def wait_for_janusgraph():
    log.info("janusgraph_wait", url=GREMLIN_URL, max_retries=MAX_RETRIES)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            c = get_client()
            result = c.submit("g.V().limit(1).count()").all().result()
            c.close()
            log.info("janusgraph_ready", attempt=attempt, vertex_count=result[0])
            return True
        except Exception as e:
            log.warning(
                "janusgraph_not_ready",
                attempt=attempt,
                max_retries=MAX_RETRIES,
                error=str(e),
                retry_in_seconds=RETRY_INTERVAL,
            )
            time.sleep(RETRY_INTERVAL)
    return False


def bootstrap_schema():
    """
    Create property keys and vertex/edge labels if they don't exist.
    JanusGraph schema is defined once and is idempotent.
    """
    schema_groovy = """
        mgmt = graph.openManagement()

        // ── Vertex labels ──────────────────────────────────────
        if (!mgmt.getVertexLabel('DataSet'))
            mgmt.makeVertexLabel('DataSet').make()
        if (!mgmt.getVertexLabel('Column'))
            mgmt.makeVertexLabel('Column').make()
        if (!mgmt.getVertexLabel('Process'))
            mgmt.makeVertexLabel('Process').make()
        if (!mgmt.getVertexLabel('Owner'))
            mgmt.makeVertexLabel('Owner').make()
        if (!mgmt.getVertexLabel('Tag'))
            mgmt.makeVertexLabel('Tag').make()

        // ── Edge labels ────────────────────────────────────────
        if (!mgmt.getEdgeLabel('INPUT_TO'))
            mgmt.makeEdgeLabel('INPUT_TO').multiplicity(MULTI).make()
        if (!mgmt.getEdgeLabel('PRODUCES'))
            mgmt.makeEdgeLabel('PRODUCES').multiplicity(MULTI).make()
        if (!mgmt.getEdgeLabel('DERIVES_FROM'))
            mgmt.makeEdgeLabel('DERIVES_FROM').multiplicity(MULTI).make()
        if (!mgmt.getEdgeLabel('DEPENDS_ON'))
            mgmt.makeEdgeLabel('DEPENDS_ON').multiplicity(MULTI).make()
        if (!mgmt.getEdgeLabel('OWNED_BY'))
            mgmt.makeEdgeLabel('OWNED_BY').multiplicity(MULTI).make()
        if (!mgmt.getEdgeLabel('CLASSIFIED_AS'))
            mgmt.makeEdgeLabel('CLASSIFIED_AS').multiplicity(MULTI).make()

        // ── Property keys — DataSet ────────────────────────────
        if (!mgmt.getPropertyKey('name'))
            mgmt.makePropertyKey('name').dataType(String.class).make()
        if (!mgmt.getPropertyKey('layer'))
            mgmt.makePropertyKey('layer').dataType(String.class).make()
        if (!mgmt.getPropertyKey('row_count'))
            mgmt.makePropertyKey('row_count').dataType(Long.class).make()

        // ── Property keys — Process ────────────────────────────
        if (!mgmt.getPropertyKey('stage'))
            mgmt.makePropertyKey('stage').dataType(String.class).make()
        if (!mgmt.getPropertyKey('run_id'))
            mgmt.makePropertyKey('run_id').dataType(String.class).make()
        if (!mgmt.getPropertyKey('transform_type'))
            mgmt.makePropertyKey('transform_type').dataType(String.class).make()
        if (!mgmt.getPropertyKey('target_table'))
            mgmt.makePropertyKey('target_table').dataType(String.class).make()
        if (!mgmt.getPropertyKey('duration_ms'))
            mgmt.makePropertyKey('duration_ms').dataType(Long.class).make()
        if (!mgmt.getPropertyKey('depends_on_stages'))
            mgmt.makePropertyKey('depends_on_stages').dataType(String.class).make()
        if (!mgmt.getPropertyKey('output_row_count'))
            mgmt.makePropertyKey('output_row_count').dataType(Long.class).make()

        // ── Property keys — Column ─────────────────────────────
        if (!mgmt.getPropertyKey('column_name'))
            mgmt.makePropertyKey('column_name').dataType(String.class).make()
        if (!mgmt.getPropertyKey('derivation'))
            mgmt.makePropertyKey('derivation').dataType(String.class).make()
        if (!mgmt.getPropertyKey('dataset_name'))
            mgmt.makePropertyKey('dataset_name').dataType(String.class).make()

        // ── Property keys — shared ─────────────────────────────
        if (!mgmt.getPropertyKey('valid_from'))
            mgmt.makePropertyKey('valid_from').dataType(String.class).make()
        if (!mgmt.getPropertyKey('valid_to'))
            mgmt.makePropertyKey('valid_to').dataType(String.class).make()
        if (!mgmt.getPropertyKey('source_system'))
            mgmt.makePropertyKey('source_system').dataType(String.class).make()
        if (!mgmt.getPropertyKey('computed_at'))
            mgmt.makePropertyKey('computed_at').dataType(String.class).make()

        // ── Composite indexes for fast lookup ──────────────────
        if (!mgmt.getGraphIndex('byDataSetName')) {
            name_key = mgmt.getPropertyKey('name')
            mgmt.buildIndex('byDataSetName', Vertex.class)
                .addKey(name_key)
                .indexOnly(mgmt.getVertexLabel('DataSet'))
                .buildCompositeIndex()
        }
        if (!mgmt.getGraphIndex('byProcessStageRunId')) {
            stage_key  = mgmt.getPropertyKey('stage')
            run_key    = mgmt.getPropertyKey('run_id')
            mgmt.buildIndex('byProcessStageRunId', Vertex.class)
                .addKey(stage_key)
                .addKey(run_key)
                .indexOnly(mgmt.getVertexLabel('Process'))
                .buildCompositeIndex()
        }
        if (!mgmt.getGraphIndex('byColumnDataset')) {
            col_key  = mgmt.getPropertyKey('column_name')
            ds_key   = mgmt.getPropertyKey('dataset_name')
            mgmt.buildIndex('byColumnDataset', Vertex.class)
                .addKey(col_key)
                .addKey(ds_key)
                .indexOnly(mgmt.getVertexLabel('Column'))
                .buildCompositeIndex()
        }

        mgmt.commit()
        'schema_bootstrap_complete'
    """
    c = get_client()
    try:
        result = c.submit(schema_groovy).all().result()
        log.info("schema_bootstrap", result=result[0])
    except GremlinServerError as e:
        log.error("schema_bootstrap_failed", error=str(e))
        raise
    finally:
        c.close()


def smoke_test():
    """
    Write a test vertex, read it back, delete it.
    Confirms read/write roundtrip works.
    """
    c = get_client()
    try:
        c.submit(
            "g.addV('DataSet').property('name', n).property('layer', l).next()",
            {"n": "__healthcheck__", "l": "test"},
        ).all().result()

        count = c.submit(
            "g.V().has('DataSet', 'name', n).count()",
            {"n": "__healthcheck__"},
        ).all().result()

        assert count[0] == 1, f"Expected 1 healthcheck vertex, got {count[0]}"

        c.submit(
            "g.V().has('DataSet', 'name', n).drop()",
            {"n": "__healthcheck__"},
        ).all().result()

        log.info("smoke_test_passed", vertex_written=True, vertex_deleted=True)
    except Exception as e:
        log.error("smoke_test_failed", error=str(e))
        raise
    finally:
        c.close()


def main():
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    if not wait_for_janusgraph():
        log.error("janusgraph_unreachable", url=GREMLIN_URL)
        sys.exit(1)

    bootstrap_schema()
    smoke_test()

    log.info("healthcheck_complete", status="OK", gremlin_url=GREMLIN_URL)
    sys.exit(0)


if __name__ == "__main__":
    main()