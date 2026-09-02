"""Context budget — port of llm_wiki src/lib/context-budget.ts.

Units are CHARACTERS, not tokens (the desktop app's choice, kept for
behavioral parity). Allocation: 15% response reserve, 5% index, 50%
wiki pages, remainder (~30%) headroom for history + system prompt.
"""

from __future__ import annotations

DEFAULT_MAX_CTX = 204_800
RESPONSE_RESERVE_FRAC = 0.15
INDEX_BUDGET_FRAC = 0.05
PAGE_BUDGET_FRAC = 0.5
PER_PAGE_FRAC = 0.3
PER_PAGE_FLOOR = 5_000


def compute_context_budget(max_context_size: int | None) -> dict:
    """Port of computeContextBudget (context-budget.ts:68-100)."""
    max_ctx = max_context_size if max_context_size else DEFAULT_MAX_CTX
    response_reserve = max_ctx * RESPONSE_RESERVE_FRAC
    index_budget = max_ctx * INDEX_BUDGET_FRAC
    page_budget = max_ctx * PAGE_BUDGET_FRAC
    # Per-page cap: floor 5000 chars so small budgets still fit one page,
    # ceiling 30% of the page budget so a single huge page can't starve
    # every other candidate.
    max_page_size = min(page_budget, max(PER_PAGE_FLOOR, int(page_budget * PER_PAGE_FRAC)))
    return {
        "maxCtx": int(max_ctx),
        "responseReserve": int(response_reserve),
        "indexBudget": int(index_budget),
        "pageBudget": int(page_budget),
        "maxPageSize": int(max_page_size),
    }
