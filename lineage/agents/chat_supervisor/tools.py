"""Chat-supervisor delegation tools — routes to lineage_search and impact_analyst.

Mirrors ReconX `chat/agents/supervisor/tools.py` exactly: same `_extract_text`
helper, same `_invoke_specialist` async helper with a 120-second timeout, same
`set_specialists()` injection pattern. Two `@tool` async functions.
"""
from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool


# Module-level specialist references, set by chat_supervisor.build()
_lineage_search = None
_impact_analyst = None

# Per-specialist timeout — prevents hanging if LLM or tool is unresponsive.
SPECIALIST_TIMEOUT_SECONDS = 120


def set_specialists(lineage_search, impact_analyst):
    """Inject built specialist agents for the ask_* tools to dispatch to."""
    global _lineage_search, _impact_analyst
    _lineage_search = lineage_search
    _impact_analyst = impact_analyst


def _extract_text(content) -> str:
    """Extract plain text from LLM response content (str or Bedrock block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


async def _invoke_specialist(agent, question: str) -> str:
    """Invoke a specialist agent asynchronously with a timeout."""
    if agent is None:
        return "Specialist not yet initialized."
    try:
        result = await asyncio.wait_for(
            agent.ainvoke({"messages": [HumanMessage(content=question)]}),
            timeout=SPECIALIST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return f"Specialist timed out after {SPECIALIST_TIMEOUT_SECONDS}s. Try a simpler query."
    except Exception as e:
        return f"Specialist error: {e}"

    messages = result.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "content") and msg.content and not getattr(msg, "tool_calls", None):
            return _extract_text(msg.content)
    return "The specialist agent did not produce a response."


@tool
async def ask_lineage_search(question: str) -> str:
    """Delegate a data discovery question to the Lineage Search specialist.
    Use for: finding tables, finding columns, mapping business concepts to data,
    understanding what a column means, listing columns in a table.
    question: the data discovery question to answer
    """
    return await _invoke_specialist(_lineage_search, question)


@tool
async def ask_impact_analyst(question: str) -> str:
    """Delegate a change impact question to the Impact Analyst specialist.
    Use for: what breaks if I rename/drop/change X, downstream dependencies,
    impact analysis of schema changes.
    question: the impact-analysis question to answer (include table.column and change_type if known)
    """
    return await _invoke_specialist(_impact_analyst, question)


TOOLS = [ask_lineage_search, ask_impact_analyst]
