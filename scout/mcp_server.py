"""The same tools as an MCP server, for Claude Code, Claude Desktop or any MCP client.

The client's model does the research with its own web search; this server keeps the state and
enforces the same rules as the CLI. The shortlist approval is sent to the user through MCP
elicitation, so the model cannot approve its own work.

Differences from the CLI: the server never sees the client's search results, so the check on
the cited pages replaces the grounding check, and there is no critic (it would need an API key).

    python -m scout.mcp_server      # stdio; .mcp.json registers it for Claude Code
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver import AcceptedElicitation, Context, MCPServer
from pydantic import BaseModel, Field

from .config import SCORING
from .postrun import finish_outputs
from .state import ScoutState, name_key
from .tools import run_tool

OUT_ROOT = Path("outputs")
FETCH = None  # tests swap in a fake page downloader
_state: ScoutState | None = None

server = MCPServer(
    name="fintech-scout",
    instructions=(
        "Fintech market-mapping tools with built-in data-quality guardrails. Call start_research first, "
        "then research with your own web search: add_candidate / add_fact, score_candidate, "
        "validate_candidates (fix or drop flagged ones), request_human_review, and finally write_memo."
    ),
)


def _need_state() -> ScoutState:
    if _state is None:
        raise ValueError("No research session. Call start_research first.")
    return _state


class ShortlistReview(BaseModel):
    approve: bool = Field(description="Approve the shortlist?")
    remove: str = Field(default="", description="Companies to remove, comma-separated (optional)")
    comment: str = Field(
        default="", description="Comment for the memo, or what to change if you reject"
    )


@server.tool()
def start_research(theme: str) -> dict:
    """Start a new research session for a theme, e.g. 'embedded finance startups in CEE'.
    Returns the workflow and the scoring framework."""
    global _state
    slug = re.sub(r"[^a-z0-9]+", "-", theme.lower()).strip("-")[:50]
    _state = ScoutState(
        theme=theme,
        out_dir=OUT_ROOT / f"{datetime.now():%Y-%m-%d_%H%M%S}_{slug}-mcp",
        track_grounding=False,
    )
    _state.log("run_start", theme=theme, mode="mcp")
    return {
        "ok": True,
        "output_folder": str(_state.out_dir),
        "workflow": [
            "search the web and call add_candidate for each startup (>= 2 independent sources)",
            "add_fact for every market-level number you will use",
            "score_candidate on every criterion",
            "validate_candidates until it passes",
            "request_human_review (asks the user directly)",
            "write_memo",
        ],
        "scoring": {k: {"weight": w, "a 5 means": d} for k, (w, d) in SCORING.items()},
    }


@server.tool()
def add_candidate(
    name: str,
    website: str,
    country: str,
    description: str,
    sources: list[str],
    founded: int | None = None,
    stage: str | None = None,
    funding_amount: float | None = None,
    funding_currency: str = "USD",
    funding_source_url: str | None = None,
    evidence: list[dict] | None = None,
) -> dict:
    """Record a startup (re-add with the same name to update it).

    funding_amount is the full number in the original currency (33100000, not 33.1).
    Each evidence item is {claim, source_url}; every evidence and funding URL must also be
    in `sources`. Stage: seed, early, growth, profitable/bootstrapped, acquired or public.
    """
    return run_tool(_need_state(), "add_candidate", {k: v for k, v in locals().items()})


@server.tool()
def add_fact(claim: str, source_url: str) -> dict:
    """Record a market-level fact (market size, regulation, funding totals) with its source URL."""
    return run_tool(_need_state(), "add_fact", {"claim": claim, "source_url": source_url})


@server.tool()
def score_candidate(name: str, scores: dict[str, dict]) -> dict:
    """Score a candidate 1-5 on every criterion, each as {"score": int, "rationale": str}. Criteria:"""
    return run_tool(_need_state(), "score_candidate", {"name": name, "scores": scores})


score_candidate.__doc__ += "; ".join(f"{k} = {d}" for k, (_, d) in SCORING.items())


@server.tool()
def validate_candidates() -> dict:
    """Run the data-quality checks on all candidates. Must pass before human review."""
    return run_tool(_need_state(), "validate_candidates", {})


@server.tool()
def drop_candidate(name: str, reason: str) -> dict:
    """Remove a candidate that is out of scope or cannot be verified."""
    return run_tool(_need_state(), "drop_candidate", {"name": name, "reason": reason})


@server.tool()
async def request_human_review(shortlist: list[str], ctx: Context, note: str = "") -> dict:
    """Ask the user (not the model) to approve the shortlist. Only after validation passes."""
    s = _need_state()
    if not s.validated:
        return {
            "ok": False,
            "error": "Run validate_candidates until it passes before asking for review.",
        }
    unknown = [n for n in shortlist if name_key(n) not in s.candidates]
    if unknown:
        return {"ok": False, "error": f"Not in candidate list: {unknown}"}
    rows = [s.candidates[name_key(n)] for n in shortlist]
    message = "Proposed shortlist:\n" + "\n".join(
        f"{i}. {c['name']} ({c['country']}) - score {c['total']}" for i, c in enumerate(rows, 1)
    )
    if note:
        message += f"\n\nAgent note: {note}"
    try:
        answer = await ctx.elicit(message, ShortlistReview)
    except Exception as e:  # client can't ask the user, so no approval
        return {
            "ok": False,
            "error": f"Human approval is required but this client cannot ask the user ({e}).",
        }

    if isinstance(answer, AcceptedElicitation) and answer.data.approve:
        removed = {name_key(x) for x in answer.data.remove.split(",") if x.strip()}
        s.approved = [n for n in shortlist if name_key(n) not in removed]
        s.reviewer_comment = answer.data.comment
        decision = {"approved": True, "shortlist": s.approved, "comment": s.reviewer_comment}
    else:
        feedback = answer.data.comment if isinstance(answer, AcceptedElicitation) else ""
        decision = {
            "approved": False,
            "feedback": feedback or f"review {answer.action}ed by the user",
        }
    s.log(
        "tool_call",
        tool="request_human_review",
        args={"shortlist": shortlist, "note": note},
        result=decision,
    )
    return {"ok": True, **decision}


@server.tool()
def write_memo(
    title: str,
    executive_summary: str,
    key_trends: list[str],
    market_map: list[dict],
    recommendations: list[str],
) -> dict:
    """Write the memo (only after the user approved a shortlist).

    Afterwards every claim is checked against its cited page and the Excel file is built.
    market_map items are {segment, companies: [names]}.
    """
    s = _need_state()
    out = run_tool(s, "write_memo", {k: v for k, v in locals().items() if k != "s"})
    if out.get("ok"):
        check = finish_outputs(s.out_dir, verbose=False, fetch=FETCH)
        out["verification"] = check
        out["written"] = [
            str(s.out_dir / f) for f in ("memo.md", "market_map.xlsx", "verification.md")
        ]
    return out


if __name__ == "__main__":
    server.run()
