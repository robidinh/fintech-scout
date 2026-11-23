# Fintech Scout

A research agent that maps fintech startups for a given theme and writes a short strategy memo for a bank, for example on *embedded finance startups in Central and Eastern Europe*.

Claude does the research: it searches the web, picks companies and rates them. Ordinary Python code checks that work. Cited links must come from the agent's own search results, every company needs independent sources, a person approves the shortlist, a second model call fact-checks the memo, and afterwards each cited page is downloaded to confirm the numbers are on it. Every step is logged.

I built it to see how far you can trust an LLM with analyst-style research if the checking is done outside the model.

```
python -m scout "embedded finance startups in Central and Eastern Europe"
```

## Example

A full run is in [`examples/embedded-finance-cee/`](examples/embedded-finance-cee/): the [memo](examples/embedded-finance-cee/memo.md), the [Excel market map](examples/embedded-finance-cee/market_map.xlsx), the [source check](examples/embedded-finance-cee/verification.md) and the [audit log](examples/embedded-finance-cee/audit.jsonl).

| Claude Sonnet 5.5, one run, Nov 2026 | |
|---|---|
| Companies validated / shortlisted | 6 / 5 |
| Clean on first validation | 80% |
| Cited links not found in search results | 0 |
| Claims found on the cited page | 23 of 24 |
| Claims the critic could not match to evidence | 5, listed in the memo |
| Top 3 unchanged under ±50% weight changes | 100% of 1,000 scenarios |
| Searches, turns, time | 15, 8, 3.5 min |
| Cost | about $0.64 |

This is a single run, not an average. `python -m scout.evaluate` runs several themes for comparison.

## How a run works

1. Claude plans a few segments and searches the web (15 searches per run).
2. It records each startup with its sources (`add_candidate`) and any market figures it wants to use (`add_fact`).
3. It rates each company 1–5 on traction, funding, differentiation, theme fit and regulatory position. The weighted score is calculated in code.
4. `validate_candidates` runs fixed data checks: at least two independent sources, links that appeared in the search results, funding with a cited source, plausible numbers. Claude has to fix or drop what fails.
5. The shortlist is shown in the terminal. I can approve it, remove companies, or reject it with feedback.
6. Claude writes the memo. A separate model call that only sees the memo and the recorded evidence lists any claim it cannot match. Claude gets one round to fix those; anything left is printed in the memo as an open item.
7. After the run, each cited page is downloaded and the claim's figures are looked up on it. Then the Excel file is built.

The order is enforced in code, not just in the prompt: review is refused until validation passes, and the memo is refused until someone approves.

More detail on each step, and its limits, is in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Output

Each run writes a folder under `outputs/`:

- `memo.md`: summary, trends, market map, ranked shortlist, notes per company with sources, recommendations, open fact-check items
- `market_map.xlsx`: the ranking with scores as formulas tied to a Weights sheet, so changing a weight re-ranks the list; also rationales, evidence with its check status, and run stats
- `verification.md`: each claim and whether its figures were found on the cited page
- `candidates.csv` / `candidates.json`: all researched companies
- `run_summary.json`: tokens, searches, estimated cost, time and quality metrics
- `audit.jsonl`: every search, tool call, critic verdict and API call

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
python -m scout "embedded finance startups in CEE" --n 8
```

Options: `--n` (number of startups), `--auto-approve` (skip the review, for demos), `--no-critic`, `--resume <run folder>`.

Environment variables: `SCOUT_MODEL`, `SCOUT_MAX_SEARCHES`, `SCOUT_BUDGET_USD` (per-run cap, default 1.00).

A run stops when its estimated cost reaches the cap. A checkpoint is saved before every model call, so a run that crashed or hit the cap can be continued with `--resume` without paying for the earlier turns again. For cheap test runs use `SCOUT_MODEL=claude-haiku-5-5`; a 5-company test run cost about $0.13.

The source check and the Excel export also work on their own, on any run folder, without API calls:

```bash
python -m scout.verify outputs/<run folder>
python -m scout.export outputs/<run folder>
```

## MCP server

The same tools are available as an MCP server, so Claude Code, Claude Desktop or another MCP client can do the research with its own model:

```bash
python -m scout.mcp_server
```

The `.mcp.json` in this repo registers it for Claude Code. The checks stay in the server. The shortlist approval is sent to the user through MCP elicitation; if the client can't ask the user, the server refuses, so the model can't approve its own shortlist. The server can't see the client's search results, so the link check is replaced by the check on the cited pages, and there is no critic step.

## Tests

```bash
python -m unittest discover tests
```

The tests replace Claude with a scripted fake, so they run offline in a couple of seconds. They cover the ordering rules, the link and data checks, the critic loop, the scoring maths, the budget cap, crash recovery and resume, the source check, the Excel output, and the MCP server (a real MCP client connects to it in memory).

## What went wrong in live runs

- The API ran out of credit halfway through a run and everything was lost. Runs now save their data and a status on any failure, and can be resumed.
- The agent used all 15 searches in its first turn. When validation then flagged three companies, it had no searches left to fix them, dropped them, fell below the minimum of five, and was stuck. The loop kept telling it to continue for 34 turns, which cost about $0.30 for nothing. Now at most 10 searches are allowed per turn, the minimum drops to three once searches run out, and two turns without a tool call end the run.
- A cheaper model recorded EUR 33.1M of funding as "33". The source check caught it because 33 wasn't on the page. Validation now rejects amounts that look like millions.
- The first runs were expensive because every turn resends the whole conversation, search results included. Prompt caching cut the cost of that repeated input to about 5%.
- The critic finds real problems, but the agent tended to reword a flagged claim rather than remove it. The instruction now says to source it or delete it.

## Limitations

- Which companies get found, and how they are rated, depends on the model. Two runs on the same theme can give different lists. The checks, scoring maths and ranking test are reproducible; the research is not.
- The checks show that a claim can be traced to a source, not that the source is right.
- The source check matches numbers. It can't check claims without figures, and a number on the page might refer to something else.
- The ranking test varies the weights but not the ratings, which are probably the bigger source of uncertainty.
