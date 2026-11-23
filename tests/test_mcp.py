"""MCP server test: a real MCP client connects in memory and runs the whole workflow.

The "user" answers the shortlist review through an elicitation callback.
Skipped if the mcp package is not installed.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

try:
    import mcp_types as types
    from mcp import Client

    from scout import mcp_server
except ImportError:  # pragma: no cover
    mcp_server = None

from test_offline import GOOD, MEMO, SCORES


def answer(approve: bool, remove: str = "", comment: str = ""):
    async def callback(context, params):
        callback.asked = params.message
        return types.ElicitResult(
            action="accept", content={"approve": approve, "remove": remove, "comment": comment}
        )

    callback.asked = None
    return callback


async def workflow(user):
    async with Client(mcp_server.server, elicitation_callback=user, mode="legacy") as c:

        async def call(name, args=None):
            r = await c.call_tool(name, args or {})
            return json.loads(r.content[0].text)

        out = {"start": await call("start_research", {"theme": "embedded finance in CEE"})}
        for cand in GOOD:
            await call("add_candidate", cand)
            await call("score_candidate", {"name": cand["name"], "scores": SCORES})
        out["memo_too_early"] = await call("write_memo", MEMO)
        out["validate"] = await call("validate_candidates")
        out["review"] = await call(
            "request_human_review", {"shortlist": [c["name"] for c in GOOD], "note": "top 5"}
        )
        out["memo"] = await call("write_memo", MEMO)
        return out


@unittest.skipIf(mcp_server is None, "mcp package not installed")
class TestMcpServer(unittest.TestCase):
    def setUp(self):
        mcp_server.OUT_ROOT = Path(tempfile.mkdtemp())
        mcp_server.FETCH = lambda url: (
            "Alpha raised EUR 12 million" if url == "https://dealroom.co/y" else None
        )

    def test_full_workflow_with_human_approval(self):
        user = answer(approve=True, remove="Echo", comment="Looks good")
        out = asyncio.run(workflow(user))
        self.assertFalse(out["memo_too_early"]["ok"])  # guardrail: no memo before approval
        self.assertTrue(out["validate"]["validated"])
        self.assertIn("Alpha", user.asked)  # the user saw the shortlist
        self.assertEqual(out["review"]["shortlist"], ["Alpha", "Bravo", "Charlie", "Delta"])
        self.assertTrue(out["memo"]["ok"])
        self.assertGreater(out["memo"]["verification"]["verified"], 0)
        folder = Path(out["start"]["output_folder"])
        for f in ("memo.md", "market_map.xlsx", "verification.md", "audit.jsonl"):
            self.assertTrue((folder / f).exists(), f)
        self.assertIn("Looks good", (folder / "memo.md").read_text(encoding="utf-8"))

    def test_client_without_elicitation_cannot_approve(self):
        out = asyncio.run(workflow(None))  # client never asks the user
        self.assertFalse(out["review"]["ok"])
        self.assertIn("Human approval is required", out["review"]["error"])
        self.assertFalse(out["memo"]["ok"])

    def test_rejection_blocks_memo(self):
        out = asyncio.run(workflow(answer(approve=False, comment="Add more Polish companies")))
        self.assertFalse(out["review"]["approved"])
        self.assertEqual(out["review"]["feedback"], "Add more Polish companies")
        self.assertFalse(out["memo"]["ok"])


if __name__ == "__main__":
    unittest.main()
