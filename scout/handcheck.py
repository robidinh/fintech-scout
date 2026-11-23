"""Manual audit of the automatic source check.

The source check only proves a number appears on the cited page. A person has to judge whether
the page actually supports the claim. This builds a sheet for that, and scores it afterwards.

    python -m scout.handcheck make outputs/<run folder>    # writes hand_check.xlsx
    (fill in the "Your verdict" column in Excel and save)
    python -m scout.handcheck score outputs/<run folder>   # writes hand_check.md
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

VERDICTS = ["supports", "partly", "does not support", "can't tell"]
HEADERS = [
    "#",
    "Company",
    "Claim",
    "Automatic result",
    "Page text around the number",
    "Source",
    "Your verdict",
    "Notes",
]


def make_sheet(out_dir: Path) -> Path:
    data = json.loads((out_dir / "verification.json").read_text(encoding="utf-8"))
    claims = [c for c in data["claims"] if c["status"] in ("verified", "partial", "not_found")]

    wb = Workbook()
    ws = wb.active
    ws.title = "Hand check"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3864")
    for i, c in enumerate(claims, 1):
        context = " … ".join(c.get("context") or []) or "(number not found on the page)"
        ws.append([i, c["company"], c["claim"], c["status"], context, c["url"], None, None])
        ws.cell(i + 1, 6).hyperlink = c["url"]
        ws.cell(i + 1, 7).fill = PatternFill("solid", fgColor="FFF2CC")
        for col in (3, 5):
            ws.cell(i + 1, col).alignment = Alignment(wrap_text=True, vertical="top")

    choices = DataValidation(type="list", formula1='"' + ",".join(VERDICTS) + '"')
    ws.add_data_validation(choices)
    choices.add(f"G2:G{len(claims) + 1}")
    for col, width in zip("ABCDEFGH", (4, 14, 50, 12, 70, 30, 16, 30), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"

    note = wb.create_sheet("How to check")
    for line in [
        "For each row, read the claim and the page text around the number.",
        "Open the source link when the snippet is not enough.",
        "Your verdict: does the page support the claim as written?",
        "  supports: the page says this",
        "  partly: some of it is right, or the number refers to something else in part",
        "  does not support: the page does not say this, or the number means something else",
        "  can't tell: page changed, paywalled, or unclear",
        "Then save and run: python -m scout.handcheck score <run folder>",
    ]:
        note.append([line])
    note.column_dimensions["A"].width = 90

    path = out_dir / "hand_check.xlsx"
    wb.save(path)
    return path


def score_sheet(out_dir: Path) -> dict:
    ws = load_workbook(out_dir / "hand_check.xlsx")["Hand check"]
    rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[0] is not None]
    judged = [(r[3], (r[6] or "").strip().lower()) for r in rows if r[6]]
    if not judged:
        raise SystemExit("No verdicts filled in yet.")

    auto_ok = [v for status, v in judged if status == "verified"]
    counts = Counter(auto_ok)
    decided = len(auto_ok) - counts["can't tell"]
    precision = counts["supports"] / decided if decided else None
    result = {
        "claims_judged": len(judged),
        "auto_verified_judged": len(auto_ok),
        "supports": counts["supports"],
        "partly": counts["partly"],
        "does_not_support": counts["does not support"],
        "cant_tell": counts["can't tell"],
        "precision": round(precision, 2) if precision is not None else None,
    }

    md = [
        "# Hand check of the automatic source check",
        "",
        f"I read {len(judged)} claims against their cited pages. Of the "
        f"{len(auto_ok)} the automatic check marked as verified:",
        "",
        f"- {result['supports']} were supported by the page",
        f"- {result['partly']} were partly supported",
        f"- {result['does_not_support']} were not supported (the number was there, "
        "but it did not back the claim)",
        f"- {result['cant_tell']} could not be judged",
        "",
    ]
    if precision is not None:
        md.append(
            f"So when the automatic check says *verified*, the page fully supports the claim "
            f"{precision:.0%} of the time ({result['supports']} of {decided})."
        )
    missed = [v for status, v in judged if status != "verified"]
    if missed:
        result["auto_missed_but_supported"] = missed.count("supports")
        md += [
            "",
            f"The automatic check could not confirm {len(missed)} claim(s); "
            f"{missed.count('supports')} of them turned out to be supported by the page.",
        ]
    notes = [(r[0], r[1], r[6], r[7]) for r in rows if r[7]]
    if notes:
        md += ["", "## Notes", ""]
        md += [f"- #{n} {company} ({verdict}): {text}" for n, company, verdict, text in notes]
    (out_dir / "hand_check.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("make", "score"):
        sys.exit("usage: python -m scout.handcheck make|score outputs/<run folder>")
    folder = Path(sys.argv[2])
    if sys.argv[1] == "make":
        print(f"Written {make_sheet(folder)}. Fill in 'Your verdict', save, then run score.")
    else:
        print(score_sheet(folder))
