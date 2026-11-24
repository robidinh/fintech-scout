# About this example

One run with Claude Sonnet 5.5 on 23 November 2026. Some things to know:

- **The shortlist was not reviewed by a person.** The run used the normal review prompt, but the answer "approve" was piped in automatically. `audit.jsonl` therefore shows an ordinary approval. The memo originally said "approved by a human reviewer"; that line is corrected.
- **The memo was edited by hand after the run:**
  - removed: "Note: the search budget ran out, so this shortlist is smaller than the usual minimum of 5 validated companies." It was wrong (six companies were validated) and came from a bug that is now fixed.
  - changed: "1 pages unreachable" to "1 page unreachable".
  - changed: "approved by a human reviewer" to "approved automatically".
  - added: the line at the top pointing here.
- **`verification.json` is from a later re-run** of the source check, which added the page text around each figure. The counts were the same.
- `hand_check.xlsx` and `hand_check.md` were made after the run.
- The other files are as the run wrote them. The critic, the feedback to the agent and the funding instructions have changed since, so a new run would behave differently.
