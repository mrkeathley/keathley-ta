import os
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from kta.config import Settings
from kta.costs import estimate_monthly_cost


class CostEstimateTests(unittest.TestCase):
    def test_default_paid_stack_projection_is_pennies_per_month(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env(Path("/path/that/does/not/exist"))

        estimate = estimate_monthly_cost(settings)

        self.assertEqual(estimate["projected_paid_stack_monthly_usd"], "0.0803")
        self.assertEqual(estimate["projected_paid_stack_annual_usd"], "0.96")
        self.assertEqual(estimate["projected_annual_portfolio_drag_pct"], "0.0321")
        self.assertEqual(estimate["configured_modes_monthly_usd"], "0.0000")

    def test_premium_model_rates_expose_portfolio_drag(self):
        with patch.dict(
            os.environ,
            {
                "KTA_ESTIMATED_LLM_INPUT_USD_PER_MILLION": "5",
                "KTA_ESTIMATED_LLM_OUTPUT_USD_PER_MILLION": "30",
            },
            clear=True,
        ):
            settings = Settings.from_env(Path("/path/that/does/not/exist"))

        estimate = estimate_monthly_cost(settings, signal_cycles=21, reviews=4, portfolio_usd=Decimal("3000"))

        self.assertGreater(Decimal(estimate["projected_paid_stack_monthly_usd"]), Decimal("1"))
        self.assertGreater(Decimal(estimate["projected_annual_portfolio_drag_pct"]), Decimal("0.4"))

    def test_agentic_research_prices_each_bounded_turn_and_search(self):
        with patch.dict(
            os.environ,
            {
                "KTA_AGENTIC_RESEARCH_ENABLED": "true",
                "KTA_AGENT_MODE": "openrouter",
                "KTA_RESEARCH_MODE": "perplexity",
                "KTA_AGENT_MAX_TURNS": "6",
                "KTA_AGENT_MAX_RESEARCH_CALLS": "4",
            },
            clear=True,
        ):
            settings = Settings.from_env(Path("/path/that/does/not/exist"))

        estimate = estimate_monthly_cost(settings)

        self.assertTrue(estimate["assumptions"]["agentic_research_enabled"])
        self.assertEqual(estimate["assumptions"]["director_turns_per_cycle_ceiling"], 6)
        self.assertEqual(estimate["assumptions"]["research_requests_per_cycle_ceiling"], 4)
        self.assertGreater(
            Decimal(estimate["projected_paid_stack_monthly_usd"]), Decimal("0.20")
        )


if __name__ == "__main__":
    unittest.main()
