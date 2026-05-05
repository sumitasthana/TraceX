"""Lineage Search agent — natural-language column/dataset discovery."""
from langgraph.prebuilt import create_react_agent

from lineage.llm_client import get_fast_llm
from lineage.prompt_loader import get_prompt_loader
from lineage.agents.lineage_search.tools import (
    TOOLS,
    search_columns_by_text,
    search_datasets_by_name,
    get_columns_for_dataset,
    get_column_detail,
)

NAME = "lineage_search"


def build():
    """Build the Lineage Search specialist agent (stateless, fast model)."""
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
    "search_columns_by_text",
    "search_datasets_by_name",
    "get_columns_for_dataset",
    "get_column_detail",
]
