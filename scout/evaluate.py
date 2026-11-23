"""Run several themes unattended and compare quality, cost and time.

    python -m scout.evaluate                      # default themes
    python -m scout.evaluate "theme A" "theme B"

Writes outputs/eval_<timestamp>.md. Re-run after changing the prompt, model or rules.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from .__main__ import build_state
from .agent import run
from .config import MODEL
from .postrun import finish_outputs

DEFAULT_THEMES = [
    "embedded finance startups in Central and Eastern Europe",
    "AI-driven credit underwriting startups in Europe",
    "open banking and account-to-account payment startups in the Nordics",
]

COLS = [
    ("theme", "Theme"),
    ("candidates", "Companies"),
    ("shortlisted", "Shortlist"),
    ("first_pass_clean_rate", "Clean on 1st validation"),
    ("first_pass_ungrounded", "Ungrounded on 1st pass"),
    ("critic_flags_first_round", "Critic flags (1st)"),
    ("unresolved_claims", "Unresolved claims"),
    ("verified_rate", "Claims verified on source"),
    ("searches", "Searches"),
    ("estimated_cost_usd", "Cost $"),
    ("seconds", "Time s"),
]


def main(themes):
    import anthropic

    client = anthropic.Anthropic()
    rows = []
    for theme in themes:
        print(f"\n=== {theme} ===")
        state = build_state(theme, "outputs", auto=True, client=client)
        run(theme, state, client, verbose=False)
        finish_outputs(state.out_dir, verbose=False)
        summary = json.loads((state.out_dir / "run_summary.json").read_text())
        rows.append({"theme": theme, **summary})
        print({k: summary.get(k) for k, _ in COLS[1:]})

    md = [
        f"# Eval run {datetime.now():%Y-%m-%d %H:%M} · model {MODEL}",
        "",
        "| " + " | ".join(h for _, h in COLS) + " |",
        "|" + "---|" * len(COLS),
    ]
    md += ["| " + " | ".join(str(r.get(k, "")) for k, _ in COLS) + " |" for r in rows]
    n = len(rows)
    md += [
        "",
        f"Average cost ${sum(r['estimated_cost_usd'] for r in rows) / n:.2f}, "
        f"average time {sum(r['seconds'] for r in rows) / n:.0f}s, "
        f"memos written {sum(r['memo_written'] for r in rows)}/{n}.",
    ]
    path = Path("outputs") / f"eval_{datetime.now():%Y%m%d_%H%M}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nEval written to {path}")


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT_THEMES)
