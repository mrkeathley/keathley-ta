import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from kta.agents import HeuristicAgentSuite
from kta.brokers import Broker, SimulatedBroker
from kta.domain import AccountSnapshot, ApiUsage, AssetClass, Position, RunStatus, utc_now
from kta.journal import Journal
from kta.loop import TradingLoop
from kta.market import SyntheticMarketData
from kta.research import NoResearch
from kta.risk import RiskEngine, RiskPolicy


class TradingLoopTests(unittest.TestCase):
    def test_offline_cycle_is_audited_end_to_end(self):
        now = datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.db")
            broker = SimulatedBroker(Decimal("3000"))
            progress = []
            loop = TradingLoop(
                market_data=SyntheticMarketData(now),
                research=NoResearch(),
                agents=HeuristicAgentSuite(),
                risk=RiskEngine(
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
                ),
                broker=broker,
                journal=journal,
                mode="simulation",
                safe_config={"test": True},
                progress=progress.append,
                clock=lambda: now,
            )

            result = loop.run(["TEST1", "TEST2", "TEST3"])
            events = journal.events(result.run_id)
            event_types = [item["event_type"] for item in events]

            self.assertEqual(result.status, RunStatus.COMPLETE)
            self.assertEqual(result.signal_count, 3)
            self.assertEqual(result.submitted_count, 2)
            self.assertEqual(len(broker.orders), 2)
            self.assertIn("account_snapshot", event_types)
            self.assertIn("risk_rejected", event_types)
            self.assertEqual(event_types.count("order_submitted"), 2)
            self.assertIn("learning_review", event_types)
            self.assertTrue(any(item.startswith("Scanning market data 1/3") for item in progress))
            self.assertTrue(any(item.startswith("Scout evaluating") for item in progress))
            self.assertEqual(progress[-1], "Finalizing audit journal")

    def test_held_symbol_is_scanned_outside_candidate_universe(self):
        now = datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc)

        class HoldingBroker(Broker):
            def account(self):
                return AccountSnapshot(
                    cash=Decimal("900"),
                    equity=Decimal("1000"),
                    buying_power=Decimal("900"),
                    last_equity=Decimal("1000"),
                    as_of=now,
                )

            def positions(self):
                return [
                    Position(
                        symbol="HELD",
                        quantity=Decimal("1"),
                        market_value=Decimal("100"),
                        average_entry_price=Decimal("100"),
                        asset_class=AssetClass.EQUITY,
                    )
                ]

            def submit(self, order):
                raise AssertionError("No order expected")

        class RecordingStrategy:
            version = "recording-v1"

            def __init__(self):
                self.seen = []

            def analyze(self, bars, position):
                self.seen.append((bars[-1].symbol, position.symbol if position else None))
                return None

        with tempfile.TemporaryDirectory() as directory:
            strategy = RecordingStrategy()
            loop = TradingLoop(
                market_data=SyntheticMarketData(now),
                research=NoResearch(),
                agents=HeuristicAgentSuite(),
                risk=RiskEngine(
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
                ),
                broker=HoldingBroker(),
                journal=Journal(Path(directory) / "journal.db"),
                mode="simulation",
                safe_config={"test": True},
                strategy=strategy,
                clock=lambda: now,
            )

            result = loop.run([])

            self.assertEqual(result.status, RunStatus.COMPLETE)
            self.assertEqual(strategy.seen, [("HELD", "HELD")])

    def test_learning_review_failure_is_journaled_without_failing_execution(self):
        now = datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc)

        class FailingReviewer(HeuristicAgentSuite):
            def review(self, run_events, recent_reviews):
                raise RuntimeError("review provider unavailable")

        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.db")
            loop = TradingLoop(
                market_data=SyntheticMarketData(now),
                research=NoResearch(),
                agents=FailingReviewer(),
                risk=RiskEngine(
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
                ),
                broker=SimulatedBroker(Decimal("3000")),
                journal=journal,
                mode="simulation",
                safe_config={"test": True},
                clock=lambda: now,
            )

            result = loop.run(["TEST1"])
            event_types = [event["event_type"] for event in journal.events(result.run_id)]

            self.assertEqual(result.status, RunStatus.COMPLETE)
            self.assertEqual(result.submitted_count, 1)
            self.assertEqual(len(result.errors), 1)
            self.assertIn("Learning review deferred", result.errors[0])
            self.assertIn("learning_review_error", event_types)

    def test_monthly_api_budget_suppresses_entries_but_completes_scan(self):
        now = utc_now()
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.db")
            prior_run = journal.start_run("simulation", {})
            journal.record_api_usage(
                prior_run,
                ApiUsage(
                    service="openrouter",
                    operation="prior",
                    model="test-model",
                    input_tokens=1,
                    output_tokens=1,
                    request_count=1,
                    cost_usd=Decimal("2.00"),
                ),
            )
            loop = TradingLoop(
                market_data=SyntheticMarketData(now),
                research=NoResearch(),
                agents=HeuristicAgentSuite(),
                risk=RiskEngine(
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
                ),
                broker=SimulatedBroker(Decimal("3000")),
                journal=journal,
                mode="simulation",
                safe_config={"test": True},
                monthly_api_budget_usd=Decimal("2.00"),
                paid_api_enabled=True,
                clock=lambda: now,
            )

            result = loop.run(["TEST1", "TEST2", "TEST3"])
            event_types = [event["event_type"] for event in journal.events(result.run_id)]

            self.assertEqual(result.status, RunStatus.COMPLETE)
            self.assertEqual(result.signal_count, 3)
            self.assertEqual(result.proposed_count, 0)
            self.assertEqual(result.submitted_count, 0)
            self.assertIn("api_budget_blocked", event_types)
            self.assertIn("learning_review_skipped", event_types)


if __name__ == "__main__":
    unittest.main()
