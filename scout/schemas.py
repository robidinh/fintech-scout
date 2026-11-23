"""Tool definitions sent to the Messages API (the forms Claude fills in)."""

from .config import SCORING

STAGES = ["seed", "early", "growth", "profitable/bootstrapped", "acquired", "public", None]

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}


def _obj(required: list[str], **properties) -> dict:
    return {"type": "object", "required": required, "properties": properties}


_EVIDENCE = _obj(["claim", "source_url"], claim=_STR, source_url=_STR)
_SCORE = _obj(
    ["score", "rationale"],
    score={"type": "integer", "minimum": 1, "maximum": 5},
    rationale=_STR,
)
_SEGMENT = _obj(["segment", "companies"], segment=_STR, companies=_STR_LIST)

TOOL_SCHEMAS = [
    {
        "name": "add_candidate",
        "description": (
            "Record a startup found in research (re-add with the same name to update it). "
            "Only cite URLs that appeared in your web_search results. Every evidence URL and the "
            "funding source URL must also be in `sources`."
        ),
        "input_schema": _obj(
            ["name", "website", "country", "description", "sources"],
            name=_STR,
            website=_STR,
            country=_STR,
            founded={"type": ["integer", "null"]},
            stage={"type": ["string", "null"], "enum": STAGES},
            description={**_STR, "description": "What they do and for whom, 1-3 sentences"},
            funding_amount={
                "type": ["number", "null"],
                "description": "Total disclosed funding across all rounds, as a full number in the "
                "original currency, e.g. 33100000 for EUR 33.1M (not 33.1). A single round is not the "
                "total: add up every round you found and list them in the evidence. Null if unknown.",
            },
            funding_currency={**_STR, "description": "ISO code, e.g. USD, EUR, PLN"},
            funding_source_url={"type": ["string", "null"]},
            sources=_STR_LIST,
            evidence={"type": "array", "items": _EVIDENCE},
        ),
    },
    {
        "name": "add_fact",
        "description": (
            "Record a market-level fact you will use in the memo (market size, regulation, "
            "funding totals, deals involving non-candidates) with the search-result URL that "
            "supports it."
        ),
        "input_schema": _obj(["claim", "source_url"], claim=_STR, source_url=_STR),
    },
    {
        "name": "score_candidate",
        "description": "Score a candidate 1-5 on every criterion: "
        + "; ".join(f"{k} = {d}" for k, (_, d) in SCORING.items()),
        "input_schema": _obj(
            ["name", "scores"],
            name=_STR,
            scores=_obj(list(SCORING), **{k: _SCORE for k in SCORING}),
        ),
    },
    {
        "name": "validate_candidates",
        "description": "Run the data-quality checks on all candidates (sources, funding "
        "evidence, plausibility, scoring).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "drop_candidate",
        "description": "Remove a candidate that is out of scope or cannot be verified.",
        "input_schema": _obj(["name", "reason"], name=_STR, reason=_STR),
    },
    {
        "name": "request_human_review",
        "description": "Ask the human analyst to approve, edit or reject your proposed "
        "shortlist. Only after validation passes.",
        "input_schema": _obj(
            ["shortlist"],
            shortlist=_STR_LIST,
            note={**_STR, "description": "Why this shortlist; open questions"},
        ),
    },
    {
        "name": "write_memo",
        "description": (
            "Write the final memo. Only after the human approved a shortlist. A critic model "
            "checks it against the recorded evidence and sends unsupported claims back to you."
        ),
        "input_schema": _obj(
            ["title", "executive_summary", "key_trends", "market_map", "recommendations"],
            title=_STR,
            executive_summary=_STR,
            key_trends=_STR_LIST,
            market_map={"type": "array", "items": _SEGMENT},
            recommendations=_STR_LIST,
        ),
    },
]
