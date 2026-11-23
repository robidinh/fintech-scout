"""Data-quality rules for a candidate. No model involved: same input, same result."""

from __future__ import annotations

from datetime import date

from .config import MIN_SOURCES
from .state import domain, norm_url


def check_candidate(c: dict, is_grounded=lambda url: True) -> list[str]:
    """Return a list of problems with a candidate; an empty list means it is clean."""
    issues = []
    sources = {norm_url(u) for u in c["sources"]}
    domains = {domain(u) for u in c["sources"]}

    if len(domains) < MIN_SOURCES:
        issues.append(f"needs sources from >= {MIN_SOURCES} different domains (has {len(domains)})")
    own_site = domain(c["website"]) if c["website"] else None
    if own_site and domains and domains <= {own_site}:
        issues.append("only the company's own website is cited; add an independent source")

    ungrounded = [u for u in c["sources"] if not is_grounded(u)]
    if ungrounded:
        issues.append(
            f"URLs not found in this run's search results (possibly invented): {ungrounded}"
        )

    amount = c["funding_amount"]
    if amount is not None:
        if not c["funding_source_url"]:
            issues.append("funding amount has no source URL")
        elif norm_url(c["funding_source_url"]) not in sources:
            issues.append("funding source URL is not in the sources list")
        if amount <= 0 or amount > 5e10:
            issues.append("funding amount looks implausible")
        elif amount < 10_000:
            # models sometimes write 33.1 for EUR 33.1M
            issues.append(
                f"funding amount {amount:g} looks like it is in millions; "
                "give the full number (e.g. 33100000, not 33.1)"
            )

    if c["founded"] is not None and not 1990 <= int(c["founded"]) <= date.today().year:
        issues.append("founding year looks implausible")

    for e in c["evidence"]:
        url = e.get("source_url")
        if not url:
            issues.append(f"claim without source: '{e.get('claim', '')[:60]}'")
        elif norm_url(url) not in sources:
            issues.append(f"evidence URL not in sources list: {url}")

    if not c["scores"]:
        issues.append("not scored yet")
    if len(c["description"].split()) < 8:
        issues.append("description too thin")
    return issues
