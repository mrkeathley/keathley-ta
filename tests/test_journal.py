import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from kta.domain import ApiUsage
from kta.journal import Journal


class JournalTests(unittest.TestCase):
    def test_order_claim_is_idempotent_across_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.db")
            first_run = journal.start_run("simulation", {})
            second_run = journal.start_run("simulation", {})

            first = journal.claim_order(first_run, "intent-one", "kta-intent-one", {"value": 1})
            second = journal.claim_order(second_run, "intent-one", "kta-intent-one", {"value": 2})

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertTrue(journal.has_order("intent-one"))

    def test_api_usage_is_costed_by_month(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.db")
            run_id = journal.start_run("simulation", {})
            usage = ApiUsage(
                service="openrouter",
                operation="scout",
                model="test-model",
                input_tokens=1000,
                output_tokens=100,
                request_count=1,
                cost_usd=Decimal("0.0123"),
            )

            journal.record_api_usage(run_id, usage)

            self.assertEqual(
                journal.api_cost_since(datetime(2020, 1, 1, tzinfo=timezone.utc)),
                Decimal("0.0123"),
            )
            self.assertEqual(journal.usage_for_run(run_id)[0]["operation"], "scout")

    def test_completed_decision_trigger_is_throttled_and_failed_trigger_can_retry(self):
        now = datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.db")
            first_run = journal.start_run("simulation", {})
            second_run = journal.start_run("simulation", {})

            self.assertTrue(
                journal.claim_decision_trigger(
                    first_run, "trigger", "TEST1", "v1", "enter", now, cooldown_hours=168
                )
            )
            journal.finish_decision_trigger("trigger", now, success=True)
            self.assertFalse(
                journal.claim_decision_trigger(
                    second_run,
                    "trigger",
                    "TEST1",
                    "v1",
                    "enter",
                    now + timedelta(hours=1),
                    cooldown_hours=168,
                )
            )
            self.assertTrue(
                journal.claim_decision_trigger(
                    second_run,
                    "trigger",
                    "TEST1",
                    "v1",
                    "enter",
                    now + timedelta(hours=169),
                    cooldown_hours=168,
                )
            )
            journal.finish_decision_trigger("trigger", now + timedelta(hours=169), success=False)
            self.assertTrue(
                journal.claim_decision_trigger(
                    first_run,
                    "trigger",
                    "TEST1",
                    "v1",
                    "enter",
                    now + timedelta(hours=170),
                    cooldown_hours=168,
                )
            )


if __name__ == "__main__":
    unittest.main()
