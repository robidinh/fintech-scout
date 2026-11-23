"""Build market_map.xlsx from a run folder.

Scores are Excel formulas that read the Weights sheet, so the ranking updates when someone
changes a weight.

    python -m scout.export outputs/<run folder>
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .config import SCORING
from .report import load_run
from .robustness import rank_robustness

HEADER = PatternFill("solid", fgColor="1F3864")
INPUT = PatternFill("solid", fgColor="FFF2CC")
WHITE_BOLD = Font(bold=True, color="FFFFFF")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _shortlist(out_dir: Path) -> set[str]:
    data = json.loads((out_dir / "candidates.json").read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("shortlist"):
        return set(data["shortlist"])
    csv_path = out_dir / "candidates.csv"  # v1 runs only stored it in the CSV
    if csv_path.exists():
        with csv_path.open(encoding="utf-8") as f:
            return {r["name"] for r in csv.DictReader(f) if r.get("approved") == "True"}
    return set()


def _table(ws, headers: list[str], rows: list[list], widths: list[int]):
    ws.append(headers)
    for cell in ws[1]:
        cell.fill, cell.font = HEADER, WHITE_BOLD
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _link(cell, url: str, text: str | None = None):
    cell.value, cell.hyperlink, cell.font = (
        text or url,
        url,
        Font(color="0563C1", underline="single"),
    )


def export_run(out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    candidates, facts = load_run(out_dir)
    scored = sorted(
        (c for c in candidates if c.get("scores")), key=lambda c: -(c.get("total") or 0)
    )
    shortlist = _shortlist(out_dir)
    robust = rank_robustness(scored)
    verification = _read_json(out_dir / "verification.json")
    status = {(c["claim"], c["url"]): c["status"] for c in verification.get("claims", [])}
    crit = list(SCORING)
    wb = Workbook()

    # Weights (inputs)
    ws_w = wb.active
    ws_w.title = "Weights"
    _table(
        ws_w,
        ["Criterion", "Weight", "What a 5 looks like"],
        [[k, w, d] for k, (w, d) in SCORING.items()],
        [18, 10, 70],
    )
    for row in range(2, len(crit) + 2):
        ws_w.cell(row, 2).fill, ws_w.cell(row, 2).number_format = INPUT, "0%"
    ws_w.cell(
        len(crit) + 3, 1, "Change the yellow cells: scores and ranks on 'Market map' recalculate."
    ).font = Font(italic=True)

    # Market map (formulas)
    ws = wb.create_sheet("Market map", 0)
    headers = [
        "Rank",
        "Company",
        "Country",
        "Stage",
        "Founded",
        "Funding (M)",
        "Currency",
        *[k.replace("_", " ").title() for k in crit],
        "Score (0-100)",
        "Top-3 in % of weight scenarios",
        "Rank range",
        "Shortlisted",
        "Website",
    ]
    _table(ws, headers, [], [7, 20, 14, 14, 9, 12, 9, *[11] * len(crit), 12, 14, 11, 11, 30])
    n = len(scored)
    first_score = 8  # column H
    score_col = get_column_letter(first_score + len(crit))
    weight_refs = [f"Weights!$B${i + 2}" for i in range(len(crit))]
    for i, c in enumerate(scored, start=2):
        cells = [get_column_letter(first_score + j) + str(i) for j in range(len(crit))]
        weighted = "+".join(f"{cell}*{w}" for cell, w in zip(cells, weight_refs, strict=True))
        r = robust.get(c["name"], {})
        ws.append(
            [
                f"=RANK({score_col}{i},${score_col}$2:${score_col}${n + 1})",
                c["name"],
                c["country"],
                c.get("stage") or "",
                c.get("founded"),
                round(c["funding_amount"] / 1e6, 1) if c.get("funding_amount") else None,
                c.get("funding_currency") if c.get("funding_amount") else "",
                *[c["scores"][k]["score"] for k in crit],
                f"=ROUND(({weighted})/SUM(Weights!$B$2:$B${len(crit) + 1})*20,1)",
                r.get("top_k_share"),
                f"{r.get('best_rank')}-{r.get('worst_rank')}" if r else "",
                "yes" if c["name"] in shortlist else "",
                None,
            ]
        )
        if c.get("website"):
            _link(ws.cell(i, len(headers)), c["website"])
        ws.cell(i, len(headers) - 3).number_format = "0%"
    if n:
        ws.conditional_formatting.add(
            f"{score_col}2:{score_col}{n + 1}",
            ColorScaleRule(
                start_type="min",
                start_color="F8696B",
                mid_type="percentile",
                mid_value=50,
                mid_color="FFEB84",
                end_type="max",
                end_color="63BE7B",
            ),
        )

    # Rationales
    ws_r = wb.create_sheet("Score rationale")
    _table(
        ws_r,
        ["Company", "Criterion", "Score", "Rationale"],
        [
            [c["name"], k, v["score"], v["rationale"]]
            for c in scored
            for k, v in c["scores"].items()
        ],
        [20, 16, 8, 100],
    )

    # Evidence with verification
    ws_e = wb.create_sheet("Evidence")
    rows = [
        [
            c["name"],
            e["claim"],
            status.get((e["claim"], e.get("source_url")), "not checked"),
            e.get("source_url"),
        ]
        for c in candidates
        for e in c.get("evidence", [])
    ]
    rows += [
        [
            "(market)",
            f["claim"],
            status.get((f["claim"], f["source_url"]), "not checked"),
            f["source_url"],
        ]
        for f in facts
    ]
    _table(ws_e, ["Company", "Claim", "Verified on page?", "Source"], rows, [20, 80, 16, 50])
    for i, r in enumerate(rows, start=2):
        if r[3]:
            _link(ws_e.cell(i, 4), r[3])
        ws_e.cell(i, 2).alignment = Alignment(wrap_text=True, vertical="top")

    # Run stats
    ws_s = wb.create_sheet("Run stats")
    stats = {
        **_read_json(out_dir / "run_summary.json"),
        **{f"verification_{k}": v for k, v in verification.get("summary", {}).items()},
    }
    _table(
        ws_s,
        ["Metric", "Value"],
        [[k, str(v) if isinstance(v, (list, dict)) else v] for k, v in stats.items()],
        [34, 20],
    )

    path = out_dir / "market_map.xlsx"
    wb.save(path)
    return path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m scout.export outputs/<run folder>")
    print(f"Written {export_run(Path(sys.argv[1]))}")
