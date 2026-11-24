"""The client-side tools Claude can call. Each returns a dict that goes back to the model."""

from __future__ import annotations

import re

from .checks import check_candidate
from .config import MAX_CRITIC_ROUNDS, SCORING
from .report import render_memo, save_data
from .schemas import TOOL_SCHEMAS  # noqa: F401  (re-exported for the agent)
from .state import ScoutState, domain, name_key


def add_candidate(
    s: ScoutState,
    name,
    website,
    country,
    description,
    sources,
    founded=None,
    funding_amount=None,
    funding_currency="USD",
    funding_source_url=None,
    evidence=None,
    stage=None,
):
    key = name_key(name)
    duplicate = next(
        (
            c["name"]
            for k, c in s.candidates.items()
            if k == key or (website and domain(c["website"]) == domain(website))
        ),
        None,
    )
    if duplicate and name_key(duplicate) != key:
        return {"ok": False, "error": f"Looks like a duplicate of '{duplicate}' (same website)."}

    previous = s.candidates.get(key, {})  # re-adding keeps earlier scores
    s.candidates[key] = {
        "name": name,
        "website": website,
        "country": country,
        "founded": founded,
        "stage": stage,
        "description": description,
        "funding_amount": funding_amount,
        "funding_currency": (funding_currency or "USD").upper(),
        "funding_source_url": funding_source_url,
        "sources": sorted(set(sources or [])),
        "evidence": evidence or [],
        "scores": previous.get("scores", {}),
        "total": previous.get("total"),
        "issues": [],
    }
    s.validated = False

    out = {"ok": True, "candidates_so_far": len(s.candidates), "updated": bool(duplicate)}
    not_seen = [u for u in sources or [] if not s.is_grounded(u)]
    if not_seen:
        out["warning"] = (
            f"These URLs did not appear in your search results: {not_seen}. "
            "Search for them or remove them."
        )
    return out


def add_fact(s: ScoutState, claim, source_url):
    if not s.is_grounded(source_url):
        return {
            "ok": False,
            "error": "This URL did not appear in your search results. Search first, then cite.",
        }
    s.facts.append({"claim": claim, "source_url": source_url})
    return {"ok": True, "facts_so_far": len(s.facts)}


def score_candidate(s: ScoutState, name, scores):
    c = s.candidates.get(name_key(name))
    if not c:
        return {"ok": False, "error": f"Unknown candidate '{name}'. Add it first."}
    missing = set(SCORING) - set(scores)
    if missing:
        return {"ok": False, "error": f"Missing criteria: {sorted(missing)}"}
    for crit, v in scores.items():
        if crit not in SCORING:
            return {"ok": False, "error": f"Unknown criterion '{crit}'"}
        if not isinstance(v.get("score"), int) or not 1 <= v["score"] <= 5:
            return {"ok": False, "error": f"{crit}: score must be an integer 1-5"}
        if len(v.get("rationale", "").strip()) < 15:
            return {"ok": False, "error": f"{crit}: give a real rationale"}

    if s.approved and c["name"] in s.approved:
        changed = [k for k in SCORING if scores[k]["score"] != c["scores"][k]["score"]]
        if changed:
            return {
                "ok": False,
                "error": f"{c['name']} is already approved: keep the scores for {changed} and "
                "change only the rationale text.",
            }

    c["scores"] = {k: scores[k] for k in SCORING}
    c["total"] = s.total_score(c["scores"])
    s.validated = False
    return {"ok": True, "name": c["name"], "total_score": c["total"]}


def validate_candidates(s: ScoutState):
    report = {}
    for c in s.candidates.values():
        c["issues"] = check_candidate(c, s.is_grounded)
        report[c["name"]] = c["issues"] or "ok"
    clean = [n for n, r in report.items() if r == "ok"]
    s.validated = len(clean) >= s.min_candidates and len(clean) == len(report)

    st = s.stats
    st["validation_rounds"] = st.get("validation_rounds", 0) + 1
    if st["validation_rounds"] == 1:
        st["first_pass_clean_rate"] = round(len(clean) / max(len(report), 1), 2)
        st["first_pass_ungrounded"] = sum(
            any("possibly invented" in i for i in c["issues"]) for c in s.candidates.values()
        )

    if s.validated:
        hint = "All clean. Next: request_human_review with your proposed shortlist."
    else:
        hint = (
            f"Fix or drop flagged candidates (need >= {s.min_candidates} clean ones), "
            "then validate again."
        )
    return {
        "validated": s.validated,
        "clean": len(clean),
        "total": len(report),
        "report": report,
        "next": hint,
    }


def drop_candidate(s: ScoutState, name, reason):
    c = s.candidates.pop(name_key(name), None)
    s.validated = False
    return {"ok": bool(c), "dropped": name, "reason": reason}


def request_human_review(s: ScoutState, shortlist, note=""):
    if not s.validated:
        return {
            "ok": False,
            "error": "Run validate_candidates until it passes before asking for review.",
        }
    unknown = [n for n in shortlist if name_key(n) not in s.candidates]
    if unknown:
        return {"ok": False, "error": f"Not in candidate list: {unknown}"}

    decision = s.ask_human([s.candidates[name_key(n)] for n in shortlist], note)
    if decision["approved"]:
        s.approved = decision["shortlist"]
        s.reviewer_comment = decision.get("comment", "")
        s.auto_approved = decision.get("auto", False)
    return {"ok": True, **decision}


def _squash(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def flagged_rationales(s: ScoutState, claim: str) -> list[str]:
    """Score rationales a flagged quote comes from, as "Company / criterion"."""
    hits = []
    for c in s.candidates.values():
        quote = _squash(claim).removeprefix(_squash(c["name"])).strip()
        if len(quote) < 8:
            continue
        for crit, v in c["scores"].items():
            if quote in _squash(v["rationale"]):
                hits.append(f"{c['name']} / {crit}")
    return hits


def _fix_instructions(s: ScoutState, unsupported: list[dict]) -> str:
    steps = [
        "Fact-check failed. For each flagged claim: if a URL from your search results supports "
        "it, record it with add_fact; otherwise DELETE the claim or cut it back to exactly what "
        "the recorded evidence says. Do not reword an unsupported claim and keep it."
    ]
    located = {u["claim"]: flagged_rationales(s, u["claim"]) for u in unsupported}
    in_rationale = [u for u in unsupported if located[u["claim"]] or u.get("where") == "rationale"]
    if in_rationale:
        listed = "; ".join(
            f'"{u["claim"]}" ({", ".join(located[u["claim"]]) or "find the company"})'
            for u in in_rationale
        )
        steps.append(
            "These are in score rationales, which write_memo cannot change. Fix them with "
            f"score_candidate, same scores, corrected rationale: {listed}."
        )
    steps.append(
        "Anything still flagged next round is published as an open item. Then call write_memo again."
    )
    return " ".join(steps)


def write_memo(s: ScoutState, title, executive_summary, key_trends, market_map, recommendations):
    if not s.approved:
        return {"ok": False, "error": "The shortlist has not been approved yet."}
    sections = dict(
        title=title,
        executive_summary=executive_summary,
        key_trends=key_trends,
        market_map=market_map,
        recommendations=recommendations,
    )

    draft = render_memo(s, **sections)
    unsupported = s.critic(draft, s.evidence_text()) if s.critic else []
    s.critic_rounds += 1
    s.critic_log.append({"round": s.critic_rounds, "unsupported": unsupported})
    s.log("critic", round=s.critic_rounds, unsupported=unsupported)

    if unsupported and s.critic_rounds < MAX_CRITIC_ROUNDS:
        return {
            "ok": False,
            "unsupported_claims": unsupported,
            "next": _fix_instructions(s, unsupported),
        }

    # last round: whatever is still unsupported is printed in the memo, not hidden
    memo = render_memo(s, **sections, unresolved=unsupported)
    (s.out_dir / "memo.md").write_text(memo, encoding="utf-8")
    save_data(s)
    return {
        "ok": True,
        "unresolved_claims": len(unsupported),
        "written": [str(s.out_dir / f) for f in ("memo.md", "candidates.csv", "candidates.json")],
    }


TOOLS = {
    "add_candidate": add_candidate,
    "add_fact": add_fact,
    "score_candidate": score_candidate,
    "validate_candidates": validate_candidates,
    "drop_candidate": drop_candidate,
    "request_human_review": request_human_review,
    "write_memo": write_memo,
}


def run_tool(s: ScoutState, name: str, args: dict) -> dict:
    fn = TOOLS.get(name)
    try:
        result = fn(s, **args) if fn else {"ok": False, "error": f"unknown tool {name}"}
    except TypeError as e:  # wrong or missing arguments: tell the model instead of crashing
        result = {"ok": False, "error": f"bad arguments: {e}"}
    s.log("tool_call", tool=name, args=args, result=result)
    return result
