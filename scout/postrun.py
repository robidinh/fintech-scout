"""Steps after the agent finishes. Plain HTTP and file work, no model calls."""

from __future__ import annotations

import json
from pathlib import Path

from .export import export_run
from .verify import verify_run


def source_check_line(s: dict) -> str:
    """The memo footer."""
    pages = "page" if s["unreachable"] == 1 else "pages"
    return (
        f"*Source check: {s['verified']} of {s['checkable']} checkable claims found on the "
        f"cited page ({s['unreachable']} {pages} unreachable). Details in verification.md.*\n"
    )


def finish_outputs(out_dir: Path, verbose: bool = True, fetch=None) -> dict | None:
    """Check claims against their cited pages where reachable, then build market_map.xlsx."""
    if not (out_dir / "candidates.json").exists():
        return None
    s = verify_run(out_dir, verbose=False, **({"fetch": fetch} if fetch else {}))

    memo = out_dir / "memo.md"
    if memo.exists():
        with memo.open("a", encoding="utf-8") as f:
            f.write(source_check_line(s))

    summary_path = out_dir / "run_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["verified_rate"] = s["verified_rate"]
        summary["claims_checkable"] = s["checkable"]
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    xlsx = export_run(out_dir)
    if verbose:
        print(f"[verify] {s['verified']}/{s['checkable']} claims found on the cited page.")
        print(f"[excel] {xlsx}")
    return s
