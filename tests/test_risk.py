import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kta.domain import AccountSnapshot, AssetClass, Side, TradeIntent
from kta.risk import RiskEngine, RiskPolicy


NOW = datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc)


def intent(symbol="TEST1", asset_class=AssetClass.EQUITY, as_of=NOW):
    return TradeIntent.create(
        symbol=symbol,
        asset_class=asset_class,
        side=Side.BUY,
        signal_as_of=as_of,
        strategy_version="test-v1",
        thesis="test",
        confidence=Decimal("0.75"),
        requested_notional=Decimal("1200"),
        stop_price=Decimal("90"),
        generated_by="test",
    )


class RiskEngineTests(unittest.TestCase):
    def setUp(self):
        self.account = AccountSnapshot(
            cash=Decimal("3000"),
            equity=Decimal("3000"),
            buying_power=Decimal("12000"),
            last_equity=Decimal("3000"),
            as_of=NOW,
        )
        self.engine = RiskEngine(
            RiskPolicy(
                max_position_pct=Decimal("0.40"),
                max_gross_exposure_pct=Decimal("1.00"),
                max_new_exposure_per_run_pct=Decimal("0.60"),
                max_orders_per_run=5,
                max_data_age_days=5,
                minimum_confidence=Decimal("0.55"),
                minimum_order_notional=Decimal("25"),
                daily_loss_kill_pct=Decimal("0.20"),
            )
        )

    def test_concentrated_profile_still_clips_aggregate_new_exposure(self):
        intents = [intent("TEST1"), intent("TEST2"), intent("TEST3")]

        decisions = self.engine.evaluate(intents, self.account, [], [], NOW)

        self.assertTrue(decisions[0].approved)
        self.assertEqual(decisions[0].approved_notional, Decimal("1200.00"))
        self.assertTrue(decisions[1].approved)
        self.assertEqual(decisions[1].approved_notional, Decimal("600.00"))
        self.assertFalse(decisions[2].approved)

    def test_duplicate_stale_and_option_intents_fail_closed(self):
        duplicate = intent("TEST1")
        stale = intent("TEST2", as_of=NOW - timedelta(days=10))
        option = intent("TEST1260918C00100000", asset_class=AssetClass.OPTION)

        decisions = self.engine.evaluate(
            [duplicate, stale, option], self.account, [], [duplicate.intent_id], NOW
        )

        self.assertEqual([item.approved for item in decisions], [False, False, False])
        self.assertIn("Duplicate", decisions[0].reason)
        self.assertIn("stale", decisions[1].reason)
        self.assertIn("Options", decisions[2].reason)

    def test_broker_margin_buying_power_is_not_used(self):
        proposal = intent("TEST1")
        low_cash_account = AccountSnapshot(
            cash=Decimal("100"),
            equity=Decimal("3000"),
            buying_power=Decimal("12000"),
            last_equity=Decimal("3000"),
            as_of=NOW,
        )

        decision = self.engine.evaluate([proposal], low_cash_account, [], [], NOW)[0]

        self.assertTrue(decision.approved)
        self.assertEqual(decision.approved_notional, Decimal("100.00"))


if __name__ == "__main__":
    unittest.main()
