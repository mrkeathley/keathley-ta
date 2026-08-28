import unittest
from datetime import datetime, timezone

from kta.domain import SignalAction
from kta.market import SyntheticMarketData
from kta.strategy import TrendPullbackStrategy


class TrendPullbackStrategyTests(unittest.TestCase):
    def test_synthetic_uptrend_pullback_creates_entry(self):
        now = datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc)
        bars = SyntheticMarketData(now).daily_bars("TEST1", 260)

        signal = TrendPullbackStrategy().analyze(bars, None)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, SignalAction.ENTER)
        self.assertGreater(signal.close, signal.long_average)
        self.assertLess(signal.close, signal.short_average)
        self.assertGreater(signal.stop_price, 0)

    def test_insufficient_history_fails_closed(self):
        now = datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc)
        bars = SyntheticMarketData(now).daily_bars("TEST1", 260)[-100:]

        self.assertIsNone(TrendPullbackStrategy().analyze(bars, None))


if __name__ == "__main__":
    unittest.main()
