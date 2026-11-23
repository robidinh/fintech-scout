"""The agent loop: send the conversation to Claude, run the tools it asks for, repeat."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import (
    BUDGET_USD,
    MAX_IDLE_TURNS,
    MAX_SEARCHES,
    MAX_TOKENS,
    MAX_TURNS,
    MIN_CANDIDATES,
    MIN_CANDIDATES_AFTER_SEARCH_CAP,
    MODEL,
    SCORING,
    WEB_SEARCH_TOOL,
)
from .report import save_data
from .state import ScoutState, norm_url
from .tools import TOOL_SCHEMAS, run_tool
from .usage import estimated_cost, track_usage

SYSTEM_PROMPT = f"""You are a fintech research analyst building a market map for a banking strategy team.

Workflow:
1. Plan: break the theme into 3-5 segments and decide what to search for.
2. Research with web_search. Prefer independent sources (press, funding databases, regulators) over company websites.
   You have {MAX_SEARCHES} searches in total for the whole run: avoid repeating queries, and keep a few for
   fixing validation issues later.
3. For each relevant startup call add_candidate. Every claim needs a source URL, and you may only cite URLs that
   appeared in your search results. Record funding in its original currency. Leave it null if you cannot source it.
   Set `stage` honestly (an acquired or public company is not a startup).
4. Record every market-level fact you plan to use in the memo (market size, regulation, funding totals) with add_fact.
5. Score each candidate with score_candidate on all criteria: {", ".join(SCORING)}. Be critical; 5 is rare.
6. Call validate_candidates. For each flagged candidate, research more and re-add it, or drop it with a reason.
   Repeat until validation passes with at least {MIN_CANDIDATES} candidates.
7. Call request_human_review with the shortlist you recommend and a short note. If the human edits or rejects,
   follow their feedback.
8. After approval, call write_memo. Only state facts you recorded with add_candidate or add_fact; no extra details
   from memory (countries, certifications, dates, debt/equity splits). A critic will fact-check the memo; delete or
   source whatever it flags and call write_memo again.

Rules: never invent companies, figures or URLs. If evidence is thin, say so or drop the candidate."""

CHECKPOINT = "checkpoint.json"
_CHECKPOINT_FIELDS = (
    "theme",
    "candidates",
    "facts",
    "validated",
    "approved",
    "reviewer_comment",
    "critic_rounds",
    "critic_log",
    "stats",
    "min_candidates",
)


# --- human review ---


def console_review(shortlist: list[dict], note: str) -> dict:
    """Show the proposed shortlist in the terminal: approve, remove some, or reject."""
    print("\n" + "=" * 70 + "\nAGENT PROPOSES THIS SHORTLIST\n" + "=" * 70)
    if note:
        print(f"Agent note: {note}\n")
    for i, c in enumerate(shortlist, 1):
        if c["funding_amount"]:
            funding = f"{c['funding_currency']} {c['funding_amount']:,.0f}"
        else:
            funding = "funding n/a"
        stage = c.get("stage") or "stage n/a"
        print(f"{i:>2}. {c['name']} ({c['country']}, {stage}) - score {c['total']} - {funding}")
        print(f"    {c['description']}")
    print("\n[a] approve   [r 2,5] remove items 2 and 5, then approve   [x] reject with feedback")

    choice = input("> ").strip().lower()
    names = [c["name"] for c in shortlist]
    if choice.startswith("x"):
        feedback = input("What should the agent change? ").strip()
        return {"approved": False, "shortlist": [], "feedback": feedback}
    if choice.startswith("r"):
        drop = {int(x) - 1 for x in choice[1:].replace(" ", "").split(",") if x.isdigit()}
        names = [n for i, n in enumerate(names) if i not in drop]
    comment = input("Comment for the memo (optional): ").strip()
    return {"approved": True, "shortlist": names, "comment": comment}


def auto_review(shortlist: list[dict], note: str) -> dict:
    names = [c["name"] for c in shortlist]
    return {"approved": True, "shortlist": names, "comment": "auto-approved (demo mode)"}


# --- checkpoints ---


def save_checkpoint(state: ScoutState, messages: list) -> None:
    data = {f: getattr(state, f) for f in _CHECKPOINT_FIELDS}
    data["seen_urls"] = sorted(state.seen_urls)
    tmp = state.out_dir / (CHECKPOINT + ".tmp")
    tmp.write_text(
        json.dumps({"state": data, "messages": messages}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    tmp.replace(state.out_dir / CHECKPOINT)  # rename is atomic, so a crash never leaves half a file


def load_checkpoint(out_dir: Path, **state_kwargs) -> tuple[ScoutState, list]:
    data = json.loads((out_dir / CHECKPOINT).read_text(encoding="utf-8"))
    saved = data["state"]
    state = ScoutState(theme=saved["theme"], out_dir=out_dir, **state_kwargs)
    for f in _CHECKPOINT_FIELDS[1:]:
        setattr(state, f, saved[f])
    state.seen_urls = set(saved["seen_urls"])
    return state, data["messages"]


# --- the loop ---


def run(
    theme: str,
    state: ScoutState,
    client,
    n_target: int = 8,
    verbose: bool = True,
    messages: list | None = None,
) -> ScoutState:
    """Run the agent until the memo is written or a limit is hit.

    Pass `messages` from load_checkpoint() to continue a run that stopped early.
    """
    tools = [WEB_SEARCH_TOOL, *TOOL_SCHEMAS]
    if messages is None:
        task = f"Research theme: {theme}\nTarget: about {n_target} startups, shortlist of the best 5-6."
        messages = [{"role": "user", "content": task}]
        weights = {k: w for k, (w, _) in SCORING.items()}
        state.log("run_start", theme=theme, model=MODEL, n_target=n_target, scoring=weights)
    else:
        state.log(
            "run_resumed",
            turns_so_far=state.stats.get("turns", 0),
            cost_so_far_usd=estimated_cost(state.stats),
        )
    t0 = time.time() - state.stats.get("seconds", 0)

    status = "max_turns_reached"
    try:
        status = _loop(state, client, tools, messages, verbose)
    except KeyboardInterrupt:
        status = "interrupted"
    except Exception as e:  # out of credit, network, rate limit...: keep what we have
        status = f"error: {type(e).__name__}"
        state.log("error", error_type=type(e).__name__, message=str(e)[:500])
        if verbose:
            print(f"\n[error] {type(e).__name__}: {e}")
    finally:
        _finish(state, status, t0, verbose)
    return state


def _loop(state: ScoutState, client, tools: list, messages: list, verbose: bool) -> str:
    idle_turns = 0
    for _ in range(MAX_TURNS):
        cost = estimated_cost(state.stats)
        if cost >= BUDGET_USD:
            state.log("budget_stop", estimated_cost_usd=cost, budget_usd=BUDGET_USD)
            if verbose:
                print(f"\n[budget] ~${cost} spent, cap is ${BUDGET_USD}. Stopping and saving.")
            return "budget_exceeded"

        # Saved here because every tool call so far has its result, so the history is valid.
        save_checkpoint(state, messages)
        state.stats["turns"] = state.stats.get("turns", 0) + 1

        # max_uses on the search tool only limits one request, so the run-wide cap is enforced by
        # telling the model. Removing the tool would invalidate the prompt cache.
        if state.stats["searches"] >= MAX_SEARCHES and not state.stats.get("search_cap_reached"):
            state.stats["search_cap_reached"] = True
            state.min_candidates = MIN_CANDIDATES_AFTER_SEARCH_CAP
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Search budget used ({MAX_SEARCHES} searches). Do not search again. "
                        "Drop candidates you cannot source with the evidence you have. Validation "
                        f"now passes with at least {MIN_CANDIDATES_AFTER_SEARCH_CAP} clean "
                        "candidates; then continue to human review and the memo."
                    ),
                }
            )
            state.log("search_cap_reached", searches=state.stats["searches"])

        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
            cache_control={"type": "ephemeral"},  # earlier turns are re-read from the cache
        )
        track_usage(state, resp)
        state.log(
            "api_call",
            turn=state.stats["turns"],
            running_cost_usd=estimated_cost(state.stats),
            input_tokens=state.stats["input_tokens"],
            cache_read_tokens=state.stats["cache_read_tokens"],
            output_tokens=state.stats["output_tokens"],
        )
        content = [
            b if isinstance(b, dict) else b.model_dump(exclude_none=True) for b in resp.content
        ]
        messages.append({"role": "assistant", "content": content})

        results = []
        for block in content:
            kind = block["type"]
            if kind == "text" and verbose and block["text"].strip():
                print(f"\n[agent] {block['text'].strip()}")
            elif kind == "server_tool_use" and block.get("name") == "web_search":
                query = block.get("input", {}).get("query", "")
                state.log("web_search", query=query)
                if verbose:
                    print(f"[search] {query}")
            elif kind == "web_search_tool_result":
                for r in block.get("content") or []:
                    if isinstance(r, dict) and r.get("url"):
                        state.seen_urls.add(norm_url(r["url"]))
            elif kind == "tool_use":
                out = run_tool(state, block["name"], block["input"])
                if verbose:
                    print(f"[tool] {block['name']} -> {json.dumps(out, ensure_ascii=False)[:200]}")
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": json.dumps(out, ensure_ascii=False),
                        "is_error": not out.get("ok", True),
                    }
                )

        if resp.stop_reason == "pause_turn":  # server-side search still running
            continue
        if results:
            idle_turns = 0
            messages.append({"role": "user", "content": results})
            continue
        if (state.out_dir / "memo.md").exists():
            return "completed"

        idle_turns += 1
        if idle_turns >= MAX_IDLE_TURNS:  # stuck or waiting for input; more turns only cost money
            state.log("stalled", turns_without_tool_calls=idle_turns)
            return "stalled"
        messages.append(
            {"role": "user", "content": "Continue the workflow. The memo is not written yet."}
        )
    return "max_turns_reached"


def _finish(state: ScoutState, status: str, t0: float, verbose: bool) -> None:
    """Runs after every run, including failed ones."""
    state.stats["seconds"] = round(time.time() - t0, 1)
    if state.candidates:
        save_data(state)

    first, last = (state.critic_log[0], state.critic_log[-1]) if state.critic_log else (None, None)
    summary = {
        "status": status,
        **state.stats,
        "turns": state.stats.get("turns", 0),
        "estimated_cost_usd": estimated_cost(state.stats),
        "candidates": len(state.candidates),
        "shortlisted": len(state.approved or []),
        "market_facts": len(state.facts),
        "urls_seen": len(state.seen_urls),
        "critic_rounds": state.critic_rounds,
        "critic_flags_first_round": len(first["unsupported"]) if first else None,
        "unresolved_claims": len(last["unsupported"]) if last else None,
        "memo_written": (state.out_dir / "memo.md").exists(),
        "model": MODEL,
    }
    (state.out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    memo = state.out_dir / "memo.md"
    if memo.exists():
        with memo.open("a", encoding="utf-8") as f:
            f.write(
                f"\n\n---\n*Run stats: {summary['candidates']} companies researched, "
                f"{summary['searches']} web searches, {summary['urls_seen']} sources seen, "
                f"{summary['critic_rounds']} fact-check round(s), "
                f"~${summary['estimated_cost_usd']} estimated cost, {summary['seconds']:.0f}s. "
                f"Model: {MODEL}.*\n"
            )
    state.log("run_end", **summary)

    if verbose:
        tokens = summary["input_tokens"] + summary["output_tokens"]
        print(
            f"\n[stats] {summary['candidates']} companies, {summary['searches']} searches, "
            f"{tokens:,} tokens, ~${summary['estimated_cost_usd']}, {summary['seconds']:.0f}s, "
            f"critic flags {summary['critic_flags_first_round']} -> "
            f"{summary['unresolved_claims']}, status: {status}"
        )
