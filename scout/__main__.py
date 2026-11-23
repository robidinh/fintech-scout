"""Command line.

python -m scout "embedded finance startups in CEE" [--n 8] [--auto-approve] [--no-critic]
python -m scout --resume outputs/<run folder>
"""

import argparse
import re
from datetime import datetime
from pathlib import Path

from .agent import CHECKPOINT, auto_review, console_review, load_checkpoint, run
from .critic import make_critic
from .postrun import finish_outputs
from .state import ScoutState


def build_state(theme: str, out: str, auto: bool, client, critic: bool = True) -> ScoutState:
    slug = re.sub(r"[^a-z0-9]+", "-", theme.lower()).strip("-")[:50]
    state = ScoutState(
        theme=theme,
        out_dir=Path(out) / f"{datetime.now():%Y-%m-%d_%H%M}_{slug}",
        ask_human=auto_review if auto else console_review,
    )
    if critic:
        state.critic = make_critic(client, state)
    return state


def main():
    p = argparse.ArgumentParser(description="Fintech Scout: researches and maps fintech startups.")
    p.add_argument("theme", nargs="?", help='e.g. "embedded finance startups in CEE"')
    p.add_argument("--resume", metavar="FOLDER", help="continue a run that stopped early")
    p.add_argument("--n", type=int, default=8, help="how many startups to research (default 8)")
    p.add_argument("--auto-approve", action="store_true", help="skip the human review (demo only)")
    p.add_argument("--no-critic", action="store_true", help="skip the fact-check pass")
    p.add_argument("--out", default="outputs")
    a = p.parse_args()
    if not a.theme and not a.resume:
        p.error("give a theme, or --resume <run folder>")

    import anthropic  # here rather than at the top so the tests run without the SDK

    client = anthropic.Anthropic()
    review = auto_review if a.auto_approve else console_review

    if a.resume:
        folder = Path(a.resume)
        if (folder / "memo.md").exists():
            print("That run already finished; nothing to resume.")
            return
        if not (folder / CHECKPOINT).exists():
            p.error(f"no {CHECKPOINT} in {folder}")
        state, messages = load_checkpoint(folder, ask_human=review)
        if not a.no_critic:
            state.critic = make_critic(client, state)
        print(
            f"Resuming '{state.theme}' after {state.stats.get('turns', 0)} turns. The budget cap "
            "counts the whole run, so raise SCOUT_BUDGET_USD if it stopped on budget."
        )
        run(state.theme, state, client, n_target=a.n, messages=messages)
    else:
        state = build_state(a.theme, a.out, a.auto_approve, client, critic=not a.no_critic)
        run(a.theme, state, client, n_target=a.n)

    finish_outputs(state.out_dir)
    memo = state.out_dir / "memo.md"
    if memo.exists():
        print(f"\nDone. Memo: {memo}")
    else:
        print("\nStopped before the memo was written; see audit.jsonl.")


if __name__ == "__main__":
    main()
