"""Transparent steady-state API cost model.

The estimator is deliberately parameterized because OpenRouter model prices change.
Actual provider-reported usage is stored separately in the journal.
"""

from decimal import Decimal
from typing import Dict, Optional

from .config import Settings


MILLION = Decimal("1000000")


def _token_cost(input_tokens: int, output_tokens: int, input_rate: Decimal, output_rate: Decimal) -> Decimal:
    return (
        Decimal(input_tokens) * input_rate / MILLION
        + Decimal(output_tokens) * output_rate / MILLION
    )


def _research_cost(model: str, input_tokens: int = 1200, output_tokens: int = 800) -> Decimal:
    # Low-context Sonar rates published 2026-08-27. Unknown models use Sonar Pro as a conservative proxy.
    rates = {
        "sonar": (Decimal("1"), Decimal("1"), Decimal("0.005")),
        "sonar-pro": (Decimal("3"), Decimal("15"), Decimal("0.006")),
        "sonar-reasoning-pro": (Decimal("2"), Decimal("8"), Decimal("0.006")),
    }
    input_rate, output_rate, request_fee = rates.get(
        model, (Decimal("3"), Decimal("15"), Decimal("0.006"))
    )
    return request_fee + _token_cost(input_tokens, output_tokens, input_rate, output_rate)


def estimate_monthly_cost(
    settings: Settings,
    signal_cycles: Optional[int] = None,
    reviews: Optional[int] = None,
    portfolio_usd: Optional[Decimal] = None,
) -> Dict[str, object]:
    cycles = settings.expected_signal_cycles_per_month if signal_cycles is None else signal_cycles
    review_count = settings.expected_reviews_per_month if reviews is None else reviews
    portfolio = settings.initial_simulated_cash if portfolio_usd is None else portfolio_usd

    # One batched scout and critic per signal-bearing cycle; use completion ceilings so reasoning
    # models cannot make the planning estimate look cheaper than the configured request bounds.
    decision_llm = _token_cost(
        7500,
        2800,
        settings.estimated_llm_input_usd_per_million,
        settings.estimated_llm_output_usd_per_million,
    )
    reviewer_llm = _token_cost(
        6000,
        1800,
        settings.estimated_llm_input_usd_per_million,
        settings.estimated_llm_output_usd_per_million,
    )
    research = _research_cost(settings.perplexity_model)
    llm_subtotal = (decision_llm * Decimal(cycles)) + (reviewer_llm * Decimal(review_count))
    openrouter_fee = llm_subtotal * settings.openrouter_credit_fee_pct
    research_subtotal = research * Decimal(cycles)
    paid_stack_monthly = llm_subtotal + openrouter_fee + research_subtotal
    paid_stack_annual = paid_stack_monthly * Decimal("12")
    configured_monthly = Decimal("0")
    if settings.agent_mode == "openrouter":
        configured_monthly += llm_subtotal + openrouter_fee
    if settings.research_mode == "perplexity":
        configured_monthly += research_subtotal
    annual_drag_pct = (
        paid_stack_annual / portfolio * Decimal("100") if portfolio > 0 else Decimal("0")
    )
    return {
        "assumptions": {
            "deterministic_scans_per_month": 21,
            "signal_cycles_per_month": cycles,
            "review_batches_per_month": review_count,
            "candidates_are_batched_per_cycle": True,
            "decision_tokens_per_cycle": {"input": 7500, "output_ceiling": 2800},
            "review_tokens_per_batch": {"input": 6000, "output_ceiling": 1800},
            "llm_input_usd_per_million": str(settings.estimated_llm_input_usd_per_million),
            "llm_output_usd_per_million": str(settings.estimated_llm_output_usd_per_million),
            "perplexity_model": settings.perplexity_model,
            "portfolio_usd": str(portfolio),
        },
        "per_signal_cycle_usd": str((decision_llm + research).quantize(Decimal("0.000001"))),
        "per_review_batch_usd": str(reviewer_llm.quantize(Decimal("0.000001"))),
        "projected_paid_stack_monthly_usd": str(paid_stack_monthly.quantize(Decimal("0.0001"))),
        "projected_paid_stack_annual_usd": str(paid_stack_annual.quantize(Decimal("0.01"))),
        "projected_annual_portfolio_drag_pct": str(annual_drag_pct.quantize(Decimal("0.0001"))),
        "configured_modes_monthly_usd": str(configured_monthly.quantize(Decimal("0.0001"))),
        "application_monthly_budget_usd": str(settings.monthly_api_budget_usd),
        "excludes": ["hosting", "taxes", "regulatory fees", "spreads", "slippage", "paid market data"],
    }
