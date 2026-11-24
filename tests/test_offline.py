"""Offline tests: a scripted fake Claude drives the real agent loop, tools and critic.

Checks the guardrails (no review before validation, no memo before approval),
grounding (URLs must come from search results), the data-quality checks, the
critic loop, scoring maths, cost tracking and the output files. No API key needed.
Run: python -m unittest discover tests
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scout.agent import auto_review, run
from scout.checks import check_candidate
from scout.critic import _parse, make_critic
from scout.state import ScoutState
from scout.tools import add_candidate, run_tool

SCORES = {
    k: {"score": 4, "rationale": "solid evidence from two sources"}
    for k in ["traction", "funding", "differentiation", "theme_fit", "regulatory"]
}


def cand(name, sources, **kw):
    return {
        "name": name,
        "website": f"https://{name.lower()}.example",
        "country": "Poland",
        "stage": "early",
        "description": f"{name} offers embedded payments APIs to mid-size retailers in the region.",
        "sources": sources,
        **kw,
    }


def tool(i, name, args):
    return {"type": "tool_use", "id": f"tu_{i}", "name": name, "input": args}


GOOD = [
    cand(
        n,
        [f"https://{n.lower()}.example", f"https://techcrunch.com/{n}", "https://dealroom.co/y"],
        founded=2019,
        funding_amount=12e6,
        funding_currency="EUR",
        funding_source_url="https://dealroom.co/y",
        evidence=[{"claim": f"{n} raised EUR 12M", "source_url": "https://dealroom.co/y"}],
    )
    for n in ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
]
BAD = cand(
    "Foxtrot", ["https://foxtrot.example"], funding_amount=5e6
)  # own site only, unsourced, not in results

SEARCH_RESULTS = [
    {"type": "web_search_result", "url": u, "title": "t"} for c in GOOD for u in c["sources"]
] + [{"type": "web_search_result", "url": "https://www.vestbee.com/report/", "title": "t"}]
MEMO = {
    "title": "Embedded finance in CEE",
    "executive_summary": "Five players stand out. The market is worth $5bn.",
    "key_trends": ["Banks partner rather than build"],
    "market_map": [{"segment": "Payments", "companies": ["Alpha", "Bravo"]}],
    "recommendations": ["Pilot with Alpha"],
}

SCRIPT = [
    # 1: search, add 6 candidates, score all, try to ask for review too early
    [
        {"type": "text", "text": "Planning segments and searching."},
        {
            "type": "server_tool_use",
            "id": "srv_1",
            "name": "web_search",
            "input": {"query": "embedded finance CEE"},
        },
        {"type": "web_search_tool_result", "tool_use_id": "srv_1", "content": SEARCH_RESULTS},
        *[tool(i, "add_candidate", c) for i, c in enumerate(GOOD + [BAD])],
        *[
            tool(10 + i, "score_candidate", {"name": c["name"], "scores": SCORES})
            for i, c in enumerate(GOOD + [BAD])
        ],
        tool(20, "request_human_review", {"shortlist": ["Alpha"]}),
    ],
    # 2: validate (fails on Foxtrot), try memo early
    [tool(30, "validate_candidates", {}), tool(31, "write_memo", MEMO)],
    # 3: drop Foxtrot, validate again (passes), request review
    [
        tool(40, "drop_candidate", {"name": "Foxtrot", "reason": "cannot verify independently"}),
        tool(41, "validate_candidates", {}),
        tool(
            42,
            "request_human_review",
            {"shortlist": ["Alpha", "Bravo", "Charlie", "Delta", "Echo"], "note": "top 5"},
        ),
    ],
    # 4: write memo -> critic flags the unsourced market size
    [tool(50, "write_memo", MEMO)],
    # 5: try to cite an invented URL, then a real one, then rewrite
    [
        tool(
            60,
            "add_fact",
            {"claim": "Market worth $5bn", "source_url": "https://made-up.example/report"},
        ),
        tool(
            61,
            "add_fact",
            {
                "claim": "CEE embedded finance market worth $5bn",
                "source_url": "https://vestbee.com/report",
            },
        ),
        tool(62, "write_memo", MEMO),
    ],
    # 6: done
    [{"type": "text", "text": "Memo written."}],
]

CRITIC_REPLIES = [
    'Here you go: [{"claim": "The market is worth $5bn", "reason": "no source recorded"}]',
    "[]",
]


def usage(inp, out, searches=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=searches) if searches else None,
    )


class FakeClient:
    def __init__(self):
        self.turn = 0
        self.critic_calls = 0
        self.tool_results = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        if "tools" not in kw:  # critic call
            text = CRITIC_REPLIES[self.critic_calls]
            self.critic_calls += 1
            return SimpleNamespace(content=[{"type": "text", "text": text}], usage=usage(2000, 100))
        last = kw["messages"][-1]
        if last["role"] == "user" and isinstance(last["content"], list):
            self.tool_results.append(
                {r["tool_use_id"]: json.loads(r["content"]) for r in last["content"]}
            )
        content = SCRIPT[self.turn]
        self.turn += 1
        return SimpleNamespace(
            content=content,
            usage=usage(10_000, 1_000, 1 if self.turn == 1 else 0),
            stop_reason="end_turn" if self.turn == len(SCRIPT) else "tool_use",
        )


class TestAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.client = FakeClient()
        self.state = ScoutState(
            theme="embedded finance in CEE", out_dir=self.tmp, ask_human=auto_review
        )
        self.state.critic = make_critic(self.client, self.state)
        run("embedded finance in CEE", self.state, self.client, verbose=False)
        self.r = {k: v for d in self.client.tool_results for k, v in d.items()}

    def test_guardrails(self):
        self.assertFalse(self.r["tu_20"]["ok"])  # review before validation refused
        self.assertFalse(self.r["tu_31"]["ok"])  # memo before approval refused

    def test_grounding(self):
        self.assertIn("warning", self.r["tu_5"])  # Foxtrot's URL never appeared in search results
        report = self.r["tu_30"]["report"]
        self.assertTrue(any("possibly invented" in i for i in report["Foxtrot"]))
        self.assertFalse(self.r["tu_60"]["ok"])  # invented market source rejected
        self.assertTrue(self.r["tu_61"]["ok"])  # real one (www/slash differences) accepted

    def test_validation(self):
        report = self.r["tu_30"]["report"]
        self.assertEqual(report["Alpha"], "ok")
        self.assertTrue(any("own website" in i for i in report["Foxtrot"]))
        self.assertTrue(any("funding" in i for i in report["Foxtrot"]))
        self.assertTrue(self.r["tu_41"]["validated"])

    def test_critic_loop(self):
        self.assertFalse(self.r["tu_50"]["ok"])
        self.assertEqual(
            self.r["tu_50"]["unsupported_claims"][0]["claim"], "The market is worth $5bn"
        )
        self.assertTrue(self.r["tu_62"]["ok"])
        self.assertEqual(self.r["tu_62"]["unresolved_claims"], 0)
        self.assertEqual(self.client.critic_calls, 2)

    def test_scoring_is_deterministic(self):
        self.assertEqual(self.r["tu_10"]["total_score"], 80.0)  # all 4/5 -> 80

    def test_outputs_stats_and_audit(self):
        memo = (self.tmp / "memo.md").read_text()
        self.assertIn("| 1 |", memo)
        self.assertIn("EUR 12.0M", memo)
        self.assertIn("## Market sources", memo)
        self.assertIn("Run stats", memo)
        summary = json.loads((self.tmp / "run_summary.json").read_text())
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["searches"], 1)
        self.assertEqual(summary["critic_flags_first_round"], 1)
        self.assertEqual(summary["unresolved_claims"], 0)
        self.assertEqual(summary["first_pass_clean_rate"], round(5 / 6, 2))
        self.assertGreater(summary["estimated_cost_usd"], 0)
        events = [
            json.loads(line)["event"]
            for line in (self.tmp / "audit.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[0], "run_start")
        self.assertIn("web_search", events)
        self.assertIn("critic", events)
        self.assertEqual(events[-1], "run_end")


class CreditRunsOut(FakeClient):
    """Plays turn 1 of the script, then fails like the real API did when credit ran out."""

    def create(self, **kw):
        if self.turn >= 1:
            raise RuntimeError("Your credit balance is too low to access the Anthropic API.")
        return super().create(**kw)


class TestApiFailure(unittest.TestCase):
    def test_partial_run_is_saved(self):
        tmp = Path(tempfile.mkdtemp())
        client = CreditRunsOut()
        state = ScoutState(theme="t", out_dir=tmp, ask_human=auto_review)
        run("t", state, client, verbose=False)  # must not raise
        summary = json.loads((tmp / "run_summary.json").read_text())
        self.assertEqual(summary["status"], "error: RuntimeError")
        self.assertFalse(summary["memo_written"])
        self.assertEqual(summary["candidates"], 6)
        saved = json.loads((tmp / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved["candidates"]), 6)
        events = [
            json.loads(line)["event"] for line in (tmp / "audit.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[-2:], ["error", "run_end"])


class TestResume(unittest.TestCase):
    def test_resume_after_crash_finishes_the_run(self):
        from scout.agent import load_checkpoint

        tmp = Path(tempfile.mkdtemp())
        state = ScoutState(theme="t", out_dir=tmp, ask_human=auto_review)
        run("t", state, CreditRunsOut(), verbose=False)  # crashes on the 2nd API call
        self.assertFalse((tmp / "memo.md").exists())

        client = FakeClient()
        client.turn = 1  # the model continues where it stopped
        state, messages = load_checkpoint(tmp, ask_human=auto_review)
        self.assertEqual(len(state.candidates), 6)  # research from turn 1 was kept
        state.critic = make_critic(client, state)
        run("t", state, client, verbose=False, messages=messages)

        self.assertTrue((tmp / "memo.md").exists())
        summary = json.loads((tmp / "run_summary.json").read_text())
        self.assertEqual(summary["status"], "completed")
        # 1 call before the crash, 5 after it, plus 2 critic calls
        self.assertEqual(summary["api_calls"], 1 + 5 + 2)
        events = [
            json.loads(line)["event"] for line in (tmp / "audit.jsonl").read_text().splitlines()
        ]
        self.assertIn("run_resumed", events)


class TestBudget(unittest.TestCase):
    def test_stops_at_budget(self):
        import scout.agent as agent

        tmp = Path(tempfile.mkdtemp())
        state = ScoutState(theme="t", out_dir=tmp, ask_human=auto_review)
        old, agent.BUDGET_USD = agent.BUDGET_USD, 0.01  # turn 1 alone costs more than this
        try:
            run("t", state, FakeClient(), verbose=False)
        finally:
            agent.BUDGET_USD = old
        summary = json.loads((tmp / "run_summary.json").read_text())
        self.assertEqual(summary["status"], "budget_exceeded")
        self.assertEqual(summary["api_calls"], 1)
        self.assertTrue((tmp / "candidates.json").exists())


class TestSearchCap(unittest.TestCase):
    def test_agent_told_when_search_budget_used(self):
        import scout.agent as agent

        tmp = Path(tempfile.mkdtemp())
        client = FakeClient()
        state = ScoutState(theme="t", out_dir=tmp, ask_human=auto_review)
        old, agent.MAX_SEARCHES = agent.MAX_SEARCHES, 1  # the scripted turn 1 makes one search
        try:
            run("t", state, client, verbose=False)
        finally:
            agent.MAX_SEARCHES = old
        events = [
            json.loads(line)["event"] for line in (tmp / "audit.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events.count("search_cap_reached"), 1)


class StuckClient(FakeClient):
    """Only ever replies with text, like the live run that waited for a human decision."""

    def create(self, **kw):
        self.turn += 1
        return SimpleNamespace(
            content=[{"type": "text", "text": "I am blocked, please decide."}],
            usage=usage(10_000, 100),
            stop_reason="end_turn",
        )


class TestStallAndDeadlock(unittest.TestCase):
    def test_stops_when_agent_stalls(self):
        tmp = Path(tempfile.mkdtemp())
        state = ScoutState(theme="t", out_dir=tmp, ask_human=auto_review)
        run("t", state, StuckClient(), verbose=False)
        summary = json.loads((tmp / "run_summary.json").read_text())
        self.assertEqual(summary["status"], "stalled")
        self.assertEqual(summary["api_calls"], 2)  # not 40

    def test_smaller_shortlist_allowed_after_search_cap(self):
        from scout.tools import validate_candidates

        s = ScoutState(theme="t", out_dir=Path(tempfile.mkdtemp()), track_grounding=False)
        for c in GOOD[:4]:
            add_candidate(s, **c)
            s.candidates[c["name"].lower()]["scores"] = SCORES
        self.assertFalse(validate_candidates(s)["validated"])  # 4 < 5
        s.min_candidates = 3  # what the agent loop sets at the search cap
        self.assertTrue(validate_candidates(s)["validated"])


class TestVerification(unittest.TestCase):
    def test_figures_matched_across_formats(self):
        from scout.verify import check_claim

        page = (
            "The company raised €11 million in 2022 and now serves 8000 merchants. Total: $15.5M."
        )
        self.assertEqual(check_claim("Raised EUR 11M in 2022", page)["status"], "verified")
        self.assertEqual(check_claim("Serves 8,000 merchants", page)["status"], "verified")
        self.assertEqual(check_claim("Total funding USD 15,500,000", page)["status"], "verified")
        self.assertEqual(
            check_claim("Market worth $4.09bn, 8,000 merchants", page)["status"], "partial"
        )
        self.assertEqual(check_claim("Market worth $4.09bn", page)["status"], "not_found")
        self.assertEqual(check_claim("Raised EUR 11M", None)["status"], "unreachable")
        self.assertEqual(
            check_claim("Partnered with a licensed bank", page)["status"], "no_figures"
        )

    def test_years_and_single_digits_are_not_key_figures(self):
        from scout.verify import key_figures

        self.assertEqual(
            key_figures("$280M Series C on 18 Nov 2026 at a $2bn valuation"), {280.0, 18.0}
        )
        self.assertEqual(key_figures("Founded in 2019"), {2019.0})


class TestRobustness(unittest.TestCase):
    def test_dominant_company_always_top(self):
        from scout.robustness import rank_robustness

        def sc(v):
            return {k: {"score": v, "rationale": "x"} for k in SCORES}

        rows = [{"name": "Top", "scores": sc(5)}] + [
            {"name": f"C{i}", "scores": sc(2)} for i in range(5)
        ]
        r = rank_robustness(rows)
        self.assertEqual(r["Top"]["top_k_share"], 1.0)
        self.assertEqual((r["Top"]["best_rank"], r["Top"]["worst_rank"]), (1, 1))
        self.assertEqual(rank_robustness(rows), r)  # fixed seed: reproducible


class TestFinishOutputs(unittest.TestCase):
    def test_footer_singular_and_plural(self):
        from scout.postrun import source_check_line

        for n, text in [(0, "0 pages"), (1, "1 page"), (2, "2 pages")]:
            line = source_check_line({"verified": 3, "checkable": 4, "unreachable": n})
            self.assertIn(f"({text} unreachable)", line)

    def test_verify_and_excel_after_run(self):
        from openpyxl import load_workbook

        from scout.postrun import finish_outputs

        tmp = Path(tempfile.mkdtemp())
        client = FakeClient()
        state = ScoutState(theme="t", out_dir=tmp, ask_human=auto_review)
        state.critic = make_critic(client, state)
        run("t", state, client, verbose=False)
        pages = {"https://dealroom.co/y": "Alpha raised EUR 12 million"}
        s = finish_outputs(tmp, verbose=False, fetch=lambda url: pages.get(url))
        self.assertGreater(s["verified"], 0)
        self.assertEqual(s["not_found"], 0)
        memo = (tmp / "memo.md").read_text(encoding="utf-8")
        self.assertIn("How robust is the ranking?", memo)
        self.assertIn("Source check:", memo)
        self.assertIn("verified_rate", json.loads((tmp / "run_summary.json").read_text()))
        wb = load_workbook(tmp / "market_map.xlsx")
        self.assertEqual(wb.sheetnames[:2], ["Market map", "Weights"])
        self.assertTrue(str(wb["Market map"]["M2"].value).startswith("=ROUND("))  # live formula
        self.assertEqual(wb["Market map"]["P2"].value, "yes")  # shortlisted


class TestCriticRationales(unittest.TestCase):
    """Flags from the example run: three of five were in score rationales."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = ScoutState(theme="t", out_dir=self.tmp, track_grounding=False)
        for c in GOOD:
            add_candidate(self.state, **c)
            self.score(c["name"])
        self.state.approved = [c["name"] for c in GOOD]

    def score(self, name, score=4, text="Direct Visa membership and PCI DSS Level 1 issuing."):
        scores = {k: dict(v) for k, v in SCORES.items()}
        scores["traction"] = {"score": score, "rationale": text}
        return run_tool(self.state, "score_candidate", {"name": name, "scores": scores})

    def test_rationale_flag_points_to_score_candidate(self):
        for c in GOOD[1:]:
            self.score(c["name"], text="Solid evidence from two sources.")
        self.state.critic = lambda memo, evidence: (
            [{"claim": "Alpha: PCI DSS Level 1 issuing", "reason": "not in evidence"}]
            if "PCI DSS" in memo
            else []
        )
        first = run_tool(self.state, "write_memo", MEMO)
        self.assertFalse(first["ok"])
        self.assertIn("score_candidate", first["next"])
        self.assertIn("Alpha / traction", first["next"])

        self.assertTrue(self.score("Alpha", text="Direct Visa membership.")["ok"])
        second = run_tool(self.state, "write_memo", MEMO)
        self.assertEqual(second["unresolved_claims"], 0)
        self.assertNotIn("PCI DSS", (self.tmp / "memo.md").read_text(encoding="utf-8"))

    def test_paraphrased_rationale_flag_uses_critic_label(self):
        flag = {"claim": "holds a card certification", "reason": "x", "where": "rationale"}
        self.state.critic = lambda memo, evidence: [flag]
        self.assertIn("score_candidate", run_tool(self.state, "write_memo", MEMO)["next"])

    def test_prose_flag_does_not_mention_score_candidate(self):
        flag = {"claim": "The market is worth $5bn", "reason": "x", "where": "prose"}
        self.state.critic = lambda memo, evidence: [flag]
        self.assertNotIn("score_candidate", run_tool(self.state, "write_memo", MEMO)["next"])

    def test_scores_are_locked_after_approval(self):
        self.assertFalse(self.score("Alpha", score=5)["ok"])
        self.assertEqual(self.state.candidates["alpha"]["scores"]["traction"]["score"], 4)

    def test_critic_prompt_checks_rationales(self):
        from scout.critic import CRITIC_PROMPT

        ignored = CRITIC_PROMPT.split("Ignore:")[1].split("Return ONLY")[0]
        self.assertNotIn("rationale", ignored)
        self.assertIn("Check the score rationales", CRITIC_PROMPT)

    def test_auto_approval_is_not_called_human(self):
        self.state.ask_human = auto_review
        self.state.validated = True
        run_tool(self.state, "request_human_review", {"shortlist": self.state.approved})
        run_tool(self.state, "write_memo", MEMO)
        memo = (self.tmp / "memo.md").read_text(encoding="utf-8")
        self.assertIn("shortlist approved automatically", memo)
        self.assertNotIn("human reviewer", memo)


class TestHandCheck(unittest.TestCase):
    def test_make_and_score(self):
        from openpyxl import load_workbook

        from scout.handcheck import make_sheet, score_sheet

        tmp = Path(tempfile.mkdtemp())
        claims = [
            {
                "company": "A",
                "claim": "Raised EUR 12M",
                "url": "https://x.example",
                "status": "verified",
                "context": ["A raised EUR 12 million"],
            },
            {
                "company": "B",
                "claim": "Raised EUR 9.5M",
                "url": "https://y.example",
                "status": "verified",
                "context": ["Revenue was 9.5 million"],
            },
            {
                "company": "C",
                "claim": "Has a licence",
                "url": "https://z.example",
                "status": "no_figures",
            },
        ]
        (tmp / "verification.json").write_text(json.dumps({"summary": {}, "claims": claims}))
        make_sheet(tmp)

        wb = load_workbook(tmp / "hand_check.xlsx")
        ws = wb["Hand check"]
        self.assertEqual(ws.max_row, 3)  # only claims with figures
        ws["G2"], ws["G3"], ws["H3"] = "supports", "does not support", "9.5M is revenue"
        wb.save(tmp / "hand_check.xlsx")

        result = score_sheet(tmp)
        self.assertEqual(result["precision"], 0.5)
        self.assertIn("9.5M is revenue", (tmp / "hand_check.md").read_text(encoding="utf-8"))


class TestChecks(unittest.TestCase):
    def test_duplicate_website_rejected(self):
        s = ScoutState(theme="t", out_dir=Path(tempfile.mkdtemp()), track_grounding=False)
        add_candidate(s, **GOOD[0])
        self.assertFalse(add_candidate(s, **dict(GOOD[0], name="Alpha Payments Ltd"))["ok"])

    def test_implausible_year(self):
        c = dict(GOOD[0], founded=1850, scores=SCORES)
        self.assertTrue(any("founding year" in i for i in check_candidate(c)))

    def test_funding_in_millions_flagged(self):
        c = dict(GOOD[0], funding_amount=33, scores=SCORES, funding_currency="EUR")
        self.assertTrue(any("in millions" in i for i in check_candidate(c)))

    def test_funding_source_must_be_listed(self):
        c = dict(GOOD[0], funding_source_url="https://cbinsights.com/x", scores=SCORES)
        self.assertTrue(any("not in the sources list" in i for i in check_candidate(c)))

    def test_critic_parse_tolerates_noise(self):
        self.assertEqual(_parse("nothing to flag"), [])
        self.assertEqual(_parse('ok:\n[{"claim": "x", "reason": "y"}]\nthanks')[0]["claim"], "x")


if __name__ == "__main__":
    unittest.main()
