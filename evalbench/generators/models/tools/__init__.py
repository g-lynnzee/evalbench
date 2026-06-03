from typing import Dict, List

from .base import Tool
from .fetch_url import FETCH_URL_TOOL

TOOL_REGISTRY: Dict[str, Tool] = {
    FETCH_URL_TOOL.name: FETCH_URL_TOOL,
}


def get_tools(names: List[str]) -> List[Tool]:
    """Resolves tool names to Tool objects. Raises on unknown names."""
    unknown = [n for n in names if n not in TOOL_REGISTRY]
    if unknown:
        raise ValueError(
            f"Unknown tools: {unknown}. "
            f"Available: {sorted(TOOL_REGISTRY)}"
        )
    return [TOOL_REGISTRY[n] for n in names]


__all__ = ["Tool", "TOOL_REGISTRY", "get_tools"]
