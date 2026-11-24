"""Check each claim against the page it cites.

Downloads every reachable cited page and looks for the claim's key figures (amounts, counts, percentages)
in the page text. No model involved.

    python -m scout.verify outputs/<run folder>

Statuses: verified (all figures found), partial, not_found, unreachable (download failed),
no_figures (nothing numeric to check; counted separately).
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from pathlib import Path

from .report import load_run

USER_AGENT = "Mozilla/5.0 (compatible; FintechScout/1.0; research-verification)"
_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\d])")


def fetch_text(url: str, timeout: float = 15.0) -> str | None:
    """Download a page and return its visible text, or None if it can't be fetched."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(3_000_000).decode(
                r.headers.get_content_charset() or "utf-8", errors="replace"
            )
    except Exception:
        return None
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))


def numbers_in(text: str) -> set[float]:
    return {float(m.replace(",", "")) for m in _NUM.findall(text or "")}


def key_figures(claim: str) -> set[float]:
    """Numbers worth checking. Years only count if there is nothing else, and single digits
    are skipped when bigger figures exist: a "2" is on almost every page."""
    nums = numbers_in(claim)
    figures = {n for n in nums if not (1990 <= n <= 2035 and n.is_integer())}
    distinctive = {n for n in figures if n >= 10 or not n.is_integer()}
    return distinctive or figures or nums


def _same(fig: float, number: float) -> bool:
    # "15.5M" in a claim can be "15,500,000" on the page, "1.70bn" can be "1.7 billion"
    variants = (fig, fig * 1e3, fig * 1e6, fig * 1e9, fig / 1e3, fig / 1e6, fig / 1e9)
    return any(abs(v - number) <= 1e-6 * max(1.0, abs(number)) for v in variants)


def _context(fig: float, page: str, width: int = 120) -> str | None:
    """The page text around the first occurrence of a figure, for a person to read."""
    for m in _NUM.finditer(page):
        if _same(fig, float(m.group(1).replace(",", ""))):
            snippet = page[max(0, m.start() - width) : m.end() + width]
            return " ".join(snippet.split())
    return None


def check_claim(claim: str, page: str | None) -> dict:
    figs = key_figures(claim)
    if not figs:
        return {"status": "no_figures"}
    if page is None:
        return {"status": "unreachable"}
    context = {f: _context(f, page) for f in figs}
    hit = sorted(f for f in figs if context[f])
    miss = sorted(f for f in figs if not context[f])
    status = "verified" if not miss else ("partial" if hit else "not_found")
    return {"status": status, "found": hit, "missing": miss, "context": [context[f] for f in hit]}


def collect_claims(candidates: list[dict], facts: list[dict]) -> list[dict]:
    claims = []
    for c in candidates:
        claims += [
            {"company": c["name"], "claim": e["claim"], "url": e["source_url"]}
            for e in c.get("evidence", [])
            if e.get("source_url")
        ]
        if c.get("funding_amount") and c.get("funding_source_url"):
            amt = c["funding_amount"]
            claims.append(
                {
                    "company": c["name"],
                    "url": c["funding_source_url"],
                    "claim": f"Total funding {c.get('funding_currency', 'USD')} {amt:,.0f}",
                }
            )
    claims += [{"company": "(market)", "claim": f["claim"], "url": f["source_url"]} for f in facts]
    return claims


def verify_run(out_dir: Path, fetch=fetch_text, verbose: bool = True) -> dict:
    out_dir = Path(out_dir)
    candidates, facts = load_run(out_dir)
    claims = collect_claims(candidates, facts)
    pages = {}
    for url in dict.fromkeys(c["url"] for c in claims):  # each page downloaded once
        if verbose:
            print(f"[fetch] {url[:100]}")
        pages[url] = fetch(url)
    for c in claims:
        c.update(check_claim(c["claim"], pages[c["url"]]))

    counts = {
        s: sum(c["status"] == s for c in claims)
        for s in ("verified", "partial", "not_found", "unreachable", "no_figures")
    }
    checkable = counts["verified"] + counts["partial"] + counts["not_found"]
    summary = {
        **counts,
        "claims": len(claims),
        "checkable": checkable,
        "verified_rate": round(counts["verified"] / checkable, 2) if checkable else None,
    }
    (out_dir / "verification.json").write_text(
        json.dumps({"summary": summary, "claims": claims}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "verification.md").write_text(_report(summary, claims), encoding="utf-8")
    with (out_dir / "audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "verification", **summary}) + "\n")
    return summary


def _report(summary: dict, claims: list[dict]) -> str:
    md = [
        "# Claim verification",
        "",
        f"**{summary['verified']} of {summary['checkable']} checkable claims verified** on the cited page "
        f"({summary['partial']} partial, {summary['not_found']} not found). "
        f"{summary['unreachable']} pages could not be downloaded; {summary['no_figures']} claims have no "
        "figures to check.",
        "",
        "A claim is verified when every key figure in it (amounts, counts, percentages) appears on the "
        "cited page. *Not found* means the number is not on that page: the source may be wrong, the page "
        "may have changed, or the figure may be wrong.",
        "",
        "| Status | Company | Claim | Missing figures | Source |",
        "|---|---|---|---|---|",
    ]
    order = {"not_found": 0, "partial": 1, "unreachable": 2, "verified": 3, "no_figures": 4}
    for c in sorted(claims, key=lambda c: order[c["status"]]):
        missing = ", ".join(f"{m:g}" for m in c.get("missing", []))
        md.append(
            f"| {c['status']} | {c['company']} | {c['claim'].replace('|', '/')} | {missing} | "
            f"[link]({c['url']}) |"
        )
    return "\n".join(md)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m scout.verify outputs/<run folder>")
    s = verify_run(Path(sys.argv[1]))
    print(
        f"\nVerified {s['verified']}/{s['checkable']} checkable claims "
        f"({s['partial']} partial, {s['not_found']} not found, {s['unreachable']} unreachable). "
        f"See verification.md"
    )
