import os
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from kta.clock import MarketClock, MarketSession
from kta.config import Settings
from kta.domain import utc_now
from kta.market import SyntheticMarketData
from kta.runtime import build_components
from kta.service import DaemonService


class MutableClock(MarketClock):
    def __init__(self, now, is_open=False):
        self.now = now
        self.is_open = is_open

    def session(self):
        return MarketSession(
            timestamp=self.now,
            is_open=self.is_open,
            next_open=self.now + timedelta(hours=1),
            next_close=self.now + timedelta(hours=7),
            source="test",
        )


class DaemonServiceTests(unittest.TestCase):
    def test_discovery_eligibility_rejects_untradable_symbols(self):
        class Directory:
            def asset_metadata(self, symbol):
                return {
                    "symbol": symbol,
                    "verified": True,
                    "asset_class": "us_equity",
                    "status": "active",
                    "tradable": symbol == "GOOD",
                }

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "KTA_UNIVERSE": "GOOD",
                "KTA_DATABASE_PATH": str(Path(directory) / "service.db"),
            },
            clear=True,
        ):
            settings = Settings.from_env(Path("/missing"))
            runtime = build_components(settings)
            runtime = replace(
                runtime,
                market=SyntheticMarketData(utc_now() - timedelta(days=1)),
                asset_directory=Directory(),
            )
            service = DaemonService(settings, runtime)
            candidates = [
                {"symbol": "GOOD", "status": "active", "metadata": {}},
                {"symbol": "BAD", "status": "active", "metadata": {}},
            ]

            eligible = service._validate_discovery_candidates(candidates)

            self.assertEqual(eligible, 1)
            self.assertEqual(candidates[0]["status"], "active")
            self.assertEqual(candidates[1]["status"], "rejected")
            self.assertIn("not tradable", candidates[1]["metadata"]["eligibility_reason"])

    def test_suggestion_is_reviewed_off_hours_revalidated_at_open_and_executed(self):
        now = utc_now()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "KTA_UNIVERSE": "TEST1",
                "KTA_DATABASE_PATH": str(Path(directory) / "service.db"),
                "KTA_AGENT_MODE": "heuristic",
                "KTA_RESEARCH_MODE": "none",
            },
            clear=True,
        ):
            settings = Settings.from_env(Path("/missing"))
            runtime = build_components(settings)
            clock = MutableClock(now, is_open=False)
            runtime = replace(runtime, market=SyntheticMarketData(now), market_clock=clock)
            service = DaemonService(settings, runtime)

            scan = service._handle_scan({"symbols": ["TEST1"], "cause": "test"})
            suggestion = service.journal.trade_suggestions(1)[0]

            self.assertEqual(scan["suggestions"], 1)
            self.assertEqual(suggestion["status"], "proposed")
            self.assertEqual(len(runtime.broker.orders), 0)

            off_hours = service._handle_review_suggestion(
                {"suggestion_id": suggestion["suggestion_id"], "revalidate": False}
            )
            self.assertEqual(off_hours["status"], "approved_awaiting_open")
            self.assertEqual(len(runtime.broker.orders), 0)

            clock.is_open = True
            at_open = service._handle_review_suggestion(
                {"suggestion_id": suggestion["suggestion_id"], "revalidate": True}
            )
            self.assertEqual(at_open["status"], "approved_ready")

            executed = service._handle_execute_suggestion(
                {"suggestion_id": suggestion["suggestion_id"]}
            )
            self.assertEqual(executed["status"], "executed")
            self.assertEqual(len(runtime.broker.orders), 1)
            self.assertEqual(
                service.journal.trade_suggestion(suggestion["suggestion_id"])["status"],
                "executed",
            )
            stops = [
                item for item in service.journal.price_triggers(10)
                if item["source"] == "protective-stop"
            ]
            self.assertEqual(len(stops), 1)
            self.assertEqual(stops[0]["comparison"], "below")


if __name__ == "__main__":
    unittest.main()
