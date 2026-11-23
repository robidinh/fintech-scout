"""Does the ranking survive different weights?

Each scenario multiplies every weight by a random factor between 0.5 and 1.5, re-normalises,
and re-ranks. Fixed seed, so the result is reproducible.
"""

from __future__ import annotations

import random

from .config import SCORING


def rank_robustness(
    candidates: list[dict], n: int = 1000, top_k: int = 3, spread: float = 0.5, seed: int = 42
) -> dict[str, dict]:
    """{name: {"top_k_share": 0-1, "best_rank": int, "worst_rank": int}} per scored candidate."""
    rows = [c for c in candidates if c.get("scores") and set(c["scores"]) >= set(SCORING)]
    if not rows:
        return {}
    rng = random.Random(seed)
    crit = list(SCORING)
    out = {c["name"]: {"top_k_share": 0, "best_rank": len(rows), "worst_rank": 1} for c in rows}
    for _ in range(n):
        w = {k: SCORING[k][0] * rng.uniform(1 - spread, 1 + spread) for k in crit}
        total = sum(w.values())
        scored = sorted(
            rows, key=lambda c: -sum(w[k] * c["scores"][k]["score"] for k in crit) / total
        )
        for rank, c in enumerate(scored, 1):
            r = out[c["name"]]
            r["top_k_share"] += rank <= top_k
            r["best_rank"], r["worst_rank"] = min(r["best_rank"], rank), max(r["worst_rank"], rank)
    for r in out.values():
        r["top_k_share"] = round(r["top_k_share"] / n, 2)
    return out
