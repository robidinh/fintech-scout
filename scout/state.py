"""Run state shared by the agent loop, the tools and the MCP server."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .config import MIN_CANDIDATES, SCORING


def domain(url: str) -> str:
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host.removeprefix("www.")


def norm_url(url: str) -> str:
    """Comparable form of a URL: no scheme, no www, no #fragment, no trailing slash."""
    u = (url or "").strip().split("#")[0]
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    return u.rstrip("/").lower()


def name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class ScoutState:
    theme: str
    out_dir: Path
    ask_human: Callable | None = None  # (shortlist, note) -> decision dict
    critic: Callable | None = None  # (memo, evidence) -> list of unsupported claims
    candidates: dict = field(default_factory=dict)  # name_key -> candidate
    facts: list = field(default_factory=list)  # market-level facts with a source
    seen_urls: set = field(default_factory=set)  # every URL web_search returned in this run
    track_grounding: bool = True
    min_candidates: int = MIN_CANDIDATES
    validated: bool = False
    approved: list | None = None
    auto_approved: bool = False
    reviewer_comment: str = ""
    critic_rounds: int = 0
    critic_log: list = field(default_factory=list)
    stats: dict = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "searches": 0,
            "api_calls": 0,
        }
    )

    def __post_init__(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.out_dir / "audit.jsonl"

    def log(self, event: str, **data):
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **data}
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def is_grounded(self, url: str) -> bool:
        return not self.track_grounding or norm_url(url) in self.seen_urls

    @staticmethod
    def total_score(scores: dict) -> float | None:
        """Weighted average of the 1-5 ratings, scaled to 20-100."""
        if not scores or set(scores) != set(SCORING):
            return None
        weighted = sum(SCORING[c][0] * scores[c]["score"] for c in SCORING)
        return round(weighted / sum(w for w, _ in SCORING.values()) * 20, 1)

    def ranked(self, names=None) -> list[dict]:
        rows = [c for c in self.candidates.values() if names is None or c["name"] in names]
        return sorted(rows, key=lambda c: c.get("total") or 0, reverse=True)

    def evidence_text(self) -> str:
        """Everything recorded so far, as plain text for the critic."""
        lines = []
        for c in self.candidates.values():
            if c["funding_amount"]:
                funding = f"{c['funding_amount']:,.0f} {c['funding_currency']}"
            else:
                funding = "not disclosed"
            lines.append(
                f"[{c['name']}] country={c['country']}; founded={c['founded']}; "
                f"total funding={funding}; description: {c['description']}"
            )
            lines += [f"  - {e['claim']} (source: {e.get('source_url')})" for e in c["evidence"]]
        lines += [f"[market] {f['claim']} (source: {f['source_url']})" for f in self.facts]
        return "\n".join(lines)
