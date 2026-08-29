import unittest
from datetime import datetime, timezone
from decimal import Decimal

from kta.domain import Bar
from kta.market import completed_daily_bars


class CompletedDailyBarsTests(unittest.TestCase):
    def test_current_session_is_excluded_until_regular_bar_is_final(self):
        bar = Bar(
            "TEST",
            datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc),
            Decimal("10"),
            Decimal("11"),
            Decimal("9"),
            Decimal("10"),
            Decimal("100"),
        )

        before_close = datetime(2026, 8, 28, 19, 0, tzinfo=timezone.utc)
        after_finalization = datetime(2026, 8, 28, 20, 20, tzinfo=timezone.utc)

        self.assertEqual(completed_daily_bars([bar], before_close), [])
        self.assertEqual(completed_daily_bars([bar], after_finalization), [bar])


if __name__ == "__main__":
    unittest.main()
