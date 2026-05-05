"""Chat Supervisor agent — routes user questions to lineage_search / impact_analyst.

Mirrors the ReconX `chat/agents/supervisor/__init__.py` shape: same
`_approx_token_count` helper, same `_build_prompt_with_trimming` with
`MAX_CONTEXT_TOKENS = 48_000`, same lazy specialist injection.
"""
from langchain_core.messages import SystemMessage, trim_messages
from langgraph.prebuilt import create_react_agent

from lineage.llm_client import get_llm
from lineage.prompt_loader import get_prompt_loader
from lineage.agents.chat_supervisor.tools import (
    TOOLS,
    set_specialists,
    ask_lineage_search,
    ask_impact_analyst,
)

NAME = "chat_supervisor"

# Token budget: ~4 chars per token, leave headroom for output.
MAX_CONTEXT_TOKENS = 48_000


def _approx_token_count(messages) -> int:
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else str(msg)
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(block.get("text", "")) // 4
                elif isinstance(block, str):
                    total += len(block) // 4
    return total


def _build_prompt_with_trimming(system_prompt_text: str):
    """Callable prompt: always preserves system message, trims history to fit."""
    trimmer = trim_messages(
        max_tokens=MAX_CONTEXT_TOKENS,
        strategy="last",
        token_counter=_approx_token_count,
        include_system=True,
        allow_partial=False,
        start_on="human",
    )

    def _modifier(state):
        system_msg = SystemMessage(content=system_prompt_text)
        return trimmer.invoke([system_msg] + state["messages"])

    return _modifier


def build(checkpointer=None):
    """Build the chat supervisor agent.

    Lazily builds the two specialist agents and injects them via
    `set_specialists()` so the module-level `@tool` functions can dispatch.
    """
    from lineage.agents.lineage_search import build as build_lineage_search
    from lineage.agents.impact_analyst import build as build_impact_analyst

    lineage_search_agent = build_lineage_search()
    impact_analyst_agent = build_impact_analyst()
    set_specialists(
        lineage_search=lineage_search_agent,
        impact_analyst=impact_analyst_agent,
    )

    loader = get_prompt_loader()
    full_prompt = loader.render(NAME, config=None)

    return create_react_agent(
        model=get_llm(),
        tools=TOOLS,
        prompt=_build_prompt_with_trimming(full_prompt),
        checkpointer=checkpointer,
    )


__all__ = [
    "build",
    "TOOLS",
    "NAME",
    "ask_lineage_search",
    "ask_impact_analyst",
]
