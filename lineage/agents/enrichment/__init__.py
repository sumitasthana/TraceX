"""Enrichment agent — writes business-language semantic descriptions to Kuzu."""
from langgraph.prebuilt import create_react_agent

from lineage.llm_client import get_fast_llm
from lineage.prompt_loader import get_prompt_loader
from lineage.agents.enrichment.tools import (
    TOOLS,
    read_column_node,
    get_upstream_columns,
    update_column_node,
)

NAME = "enrichment"


def build():
    loader = get_prompt_loader()
    return create_react_agent(
        model=get_fast_llm(),
        tools=TOOLS,
        prompt=loader.get_prompt(NAME),
    )


__all__ = [
    "build",
    "TOOLS",
    "NAME",
    "read_column_node",
    "get_upstream_columns",
    "update_column_node",
]
