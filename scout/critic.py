"""Fact-check the memo with a separate model call.

The critic gets only the memo and the recorded evidence: no search, no conversation history.
It has no earlier answer to defend, which is the point.
"""

from __future__ import annotations

import json
import re

from .config import MODEL
from .usage import track_usage

CRITIC_PROMPT = """You are a strict fact-checker for a strategy memo.

Below is the EVIDENCE the research agent recorded, then the MEMO.
List every factual statement in the memo (numbers, dates, amounts, rankings, deals,
licences, named events) that is NOT supported by the evidence.

Check the score rationales under "Company notes" too: the facts in them need evidence.

Ignore: opinions, recommendations, the 1-5 scores themselves, and statements that only
restate or round numbers that are in the evidence.

Return ONLY a JSON array, no prose. Each item:
{"claim": "<exact quote>", "reason": "<why unsupported>", "where": "rationale" or "prose"}.
Return [] if everything is supported.

EVIDENCE:
{evidence}

MEMO:
{memo}"""


def _parse(text: str) -> list[dict]:
    """Pull the JSON array out of the reply, even if the model added text around it."""
    m = re.search(r"\[.*\]", text, flags=re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [i for i in items if isinstance(i, dict) and i.get("claim")]


def make_critic(client, state=None, model: str = MODEL):
    def critic(memo_md: str, evidence: str) -> list[dict]:
        prompt = CRITIC_PROMPT.replace("{evidence}", evidence).replace("{memo}", memo_md)
        resp = client.messages.create(
            model=model, max_tokens=3000, messages=[{"role": "user", "content": prompt}]
        )
        if state is not None:
            track_usage(state, resp)
        text = "".join(
            b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
            for b in resp.content
        )
        return _parse(text)

    return critic
