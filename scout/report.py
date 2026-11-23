"""The memo and the data files a run leaves behind."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from .config import MIN_CANDIDATES, SCORING
from .robustness import rank_robustness
from .state import ScoutState, domain


def _funding(c: dict) -> str:
    if not c["funding_amount"]:
        return "n/a"
    return f"{c['funding_currency']} {c['funding_amount'] / 1e6:,.1f}M"


def render_memo(
    s: ScoutState,
    title,
    executive_summary,
    key_trends,
    market_map,
    recommendations,
    unresolved=None,
) -> str:
    """Claude writes the prose; the tables, scores and sources are filled in here from the data."""
    rows = s.ranked(set(s.approved))
    crit = list(SCORING)

    byline = f"*Generated:* {date.today().isoformat()} · shortlist approved by a human reviewer"
    if s.critic:
        byline += " · fact-checked by a critic model"
    md = [f"# {title}", "", f"*Theme:* {s.theme}  ", byline, ""]
    if s.reviewer_comment:
        md += [f"> Reviewer note: {s.reviewer_comment}", ""]
    if len(s.candidates) < MIN_CANDIDATES:
        md += [
            "> Note: the search budget ran out, so this shortlist is smaller than the usual "
            f"minimum of {MIN_CANDIDATES} validated companies.",
            "",
        ]

    md += ["## Executive summary", executive_summary, "", "## Key trends"]
    md += [f"- {t}" for t in key_trends]
    md += ["", "## Market map"]
    md += [f"- **{seg['segment']}:** {', '.join(seg['companies'])}" for seg in market_map]

    headers = " | ".join(c.replace("_", " ").title() for c in crit)
    md += [
        "",
        "## Ranked shortlist",
        "",
        f"| # | Company | Country | Stage | Funding | {headers} | Score |",
        "|---|---|---|---|---|" + "---|" * len(crit) + "---|",
    ]
    for i, c in enumerate(rows, 1):
        cells = " | ".join(str(c["scores"][k]["score"]) for k in crit)
        md.append(
            f"| {i} | [{c['name']}]({c['website']}) | {c['country']} | {c.get('stage') or 'n/a'} | "
            f"{_funding(c)} | {cells} | **{c['total']}** |"
        )
    weights = ", ".join(f"{k} {w:.0%}" for k, (w, _) in SCORING.items())
    md += ["", f"*Score = weighted average of 1–5 ratings, scaled to 100. Weights: {weights}.*", ""]

    if len(rows) > 3:
        robust = rank_robustness(rows)
        md += [
            "## How robust is the ranking?",
            "Each weight was moved randomly by up to ±50% in 1,000 scenarios and the shortlist "
            "re-ranked.",
            "",
            "| Company | Top-3 in % of scenarios | Rank range |",
            "|---|---|---|",
        ]
        for c in rows:
            r = robust[c["name"]]
            md.append(
                f"| {c['name']} | {r['top_k_share']:.0%} | {r['best_rank']}–{r['worst_rank']} |"
            )
        md.append("")

    md.append("## Company notes")
    for c in rows:
        md += [f"### {c['name']}", c["description"], ""]
        for k, v in c["scores"].items():
            md.append(f"- *{k.replace('_', ' ')}* ({v['score']}/5): {v['rationale']}")
        md += ["", "Sources: " + ", ".join(f"[{domain(u)}]({u})" for u in c["sources"]), ""]

    md += ["## Recommendations"] + [f"- {r}" for r in recommendations]
    if s.facts:
        md += ["", "## Market sources"]
        md += [f"- {f['claim']} ([{domain(f['source_url'])}]({f['source_url']}))" for f in s.facts]
    if unresolved:
        md += [
            "",
            "## Fact-check: open items",
            "The critic model could not match these statements to recorded evidence. "
            "Treat them as lower confidence.",
        ]
        md += [f"- {u['claim']} ({u.get('reason', '')})" for u in unresolved]
    return "\n".join(md)


def save_data(s: ScoutState) -> None:
    """candidates.json and candidates.csv. Also called when a run stops early."""
    s.out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "candidates": list(s.candidates.values()),
        "market_facts": s.facts,
        "shortlist": s.approved,
    }
    (s.out_dir / "candidates.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    crit = list(SCORING)
    with (s.out_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            ["name", "country", "stage", "founded", "funding_amount", "funding_currency"]
            + crit
            + ["total", "approved", "website"]
        )
        for c in s.ranked():
            scores = [c["scores"].get(k, {}).get("score") for k in crit]
            w.writerow(
                [c["name"], c["country"], c.get("stage"), c["founded"], c["funding_amount"]]
                + [c["funding_currency"]]
                + scores
                + [c["total"], c["name"] in (s.approved or []), c["website"]]
            )


def load_run(out_dir: Path) -> tuple[list[dict], list[dict]]:
    """Read candidates and market facts back from a run folder.

    v1 runs saved a plain list with funding in `funding_usd`; those still load.
    """
    data = json.loads((Path(out_dir) / "candidates.json").read_text(encoding="utf-8"))
    if isinstance(data, list):
        candidates, facts = data, []
    else:
        candidates, facts = data["candidates"], data.get("market_facts", [])
    for c in candidates:
        if "funding_amount" not in c:
            c["funding_amount"], c["funding_currency"] = c.get("funding_usd"), "USD"
        c.setdefault("stage", None)
        c.setdefault("evidence", [])
    return candidates, facts
