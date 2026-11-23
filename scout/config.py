"""Settings. Anything with an env var can be changed per run without editing code."""

import os

MODEL = os.environ.get("SCOUT_MODEL", "claude-sonnet-5-5")
MAX_TOKENS = 8000
MAX_TURNS = 40
MAX_IDLE_TURNS = 2  # replies in a row without a tool call before the run is stopped
MAX_CRITIC_ROUNDS = 2
BUDGET_USD = float(os.environ.get("SCOUT_BUDGET_USD", "1.00"))  # estimated, per run

# Search. max_uses applies per API request, so it is set below the run total to leave
# searches for fixing validation issues later in the run.
MAX_SEARCHES = int(os.environ.get("SCOUT_MAX_SEARCHES", "15"))
SEARCHES_PER_TURN = max(3, MAX_SEARCHES * 2 // 3)
# The 20260209 version filters results before they reach the context. Haiku only has the older one.
_SEARCH_TYPE = "web_search_20250305" if "haiku" in MODEL else "web_search_20260209"
WEB_SEARCH_TOOL = {"type": _SEARCH_TYPE, "name": "web_search", "max_uses": SEARCHES_PER_TURN}

# Scoring framework: criterion -> (weight, what a 5 looks like)
SCORING = {
    "traction": (0.25, "clear evidence of customers, volume or revenue growth"),
    "funding": (0.20, "recent sizeable round from credible investors, or profitable at scale"),
    "differentiation": (0.20, "product or model that is hard to copy"),
    "theme_fit": (0.20, "squarely inside the research theme and geography"),
    "regulatory": (0.15, "licensed or partnered with a licensed institution, no open issues"),
}

# Validation
MIN_SOURCES = 2  # different domains per candidate
MIN_CANDIDATES = 5
MIN_CANDIDATES_AFTER_SEARCH_CAP = 3

# List prices in USD per million tokens (Nov 2026). Override with SCOUT_PRICE_IN / _OUT.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
_in, _out = PRICES.get(MODEL, PRICES["claude-sonnet-5-5"])
PRICE_INPUT_PER_MTOK = float(os.environ.get("SCOUT_PRICE_IN", _in))
PRICE_OUTPUT_PER_MTOK = float(os.environ.get("SCOUT_PRICE_OUT", _out))
PRICE_PER_1K_SEARCHES = float(os.environ.get("SCOUT_PRICE_SEARCH", "10"))
CACHE_READ_FACTOR = 0.05  # cached input costs 5% of the normal input price
CACHE_WRITE_FACTOR = 1.25  # writing to the cache costs 25% extra
