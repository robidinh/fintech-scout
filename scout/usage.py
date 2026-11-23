"""Token, search and cost accounting for the budget cap and run_summary.json."""

from __future__ import annotations

from .config import (
    CACHE_READ_FACTOR,
    CACHE_WRITE_FACTOR,
    PRICE_INPUT_PER_MTOK,
    PRICE_OUTPUT_PER_MTOK,
    PRICE_PER_1K_SEARCHES,
)
from .state import ScoutState


def track_usage(state: ScoutState, resp) -> None:
    u = getattr(resp, "usage", None)
    if u is None:
        return
    st = state.stats
    read = getattr(u, "cache_read_input_tokens", 0) or 0
    write = getattr(u, "cache_creation_input_tokens", 0) or 0
    st["api_calls"] += 1
    st["input_tokens"] += (getattr(u, "input_tokens", 0) or 0) + read + write
    st["cache_read_tokens"] = st.get("cache_read_tokens", 0) + read
    st["cache_write_tokens"] = st.get("cache_write_tokens", 0) + write
    st["output_tokens"] += getattr(u, "output_tokens", 0) or 0
    server_tools = getattr(u, "server_tool_use", None)
    if server_tools:
        st["searches"] += getattr(server_tools, "web_search_requests", 0) or 0


def estimated_cost(stats: dict) -> float:
    """USD at list prices. input_tokens includes cached tokens, which are priced separately."""
    read = stats.get("cache_read_tokens", 0)
    write = stats.get("cache_write_tokens", 0)
    uncached = stats["input_tokens"] - read - write
    input_cost = (uncached + read * CACHE_READ_FACTOR + write * CACHE_WRITE_FACTOR) / 1e6
    return round(
        input_cost * PRICE_INPUT_PER_MTOK
        + stats["output_tokens"] / 1e6 * PRICE_OUTPUT_PER_MTOK
        + stats["searches"] / 1000 * PRICE_PER_1K_SEARCHES,
        2,
    )
