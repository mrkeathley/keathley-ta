import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from kta.domain import ApiUsage, AssetClass, Side, TradeIntent
from kta.journal import Journal


class JournalTests(unittest.TestCase):
    def test_protective_stop_only_ratchets_upward(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.db")

            trigger_id = journal.upsert_protective_stop("TEST", Decimal("90"))
            same_id = journal.upsert_protective_stop("TEST", Decimal("85"))
            journal.upsert_protective_stop("TEST", Decimal("95"))

            trigger = journal.price_triggers(1)[0]
            self.assertEqual(same_id, trigger_id)
            self.assertEqual(trigger["threshold"], "95")

    def test_intent_outcomes_track_rejected_counterfactuals_at_each_horizon(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.db")
            run_id = journal.start_run("evaluation", {})
            signal_at = datetime(2026, 8, 28, tzinfo=timezone.utc)
            intent = TradeIntent.create(
                symbol="TEST",
                asset_class=AssetClass.EQUITY,
                side=Side.BUY,
                signal_as_of=signal_at,
                strategy_version="test-v1",
                thesis="counterfactual",
                confidence=Decimal("0.6"),
                requested_notional=Decimal("100"),
                generated_by="fixture",
            )

            journal.register_intent_outcomes(run_id, intent, Decimal("100"))
            journal.update_intent_disposition(intent.intent_id, "critic_rejected")
            pending = journal.pending_intent_outcomes()
            journal.mark_intent_outcome(
                intent.intent_id,
                1,
                marked_at=signal_at + timedelta(days=1),
                mark_price=Decimal("105"),
                raw_return=Decimal("0.05"),
                side_adjusted_return=Decimal("0.05"),
                maximum_favorable_excursion=Decimal("0.06"),
                maximum_adverse_excursion=Decimal("-0.01"),
            )

            self.assertEqual([item["horizon_sessions"] for item in pending], [1, 5, 20, 60])
            self.assertTrue(all(item["disposition"] == "critic_rejected" for item in pending))
            mark = journal.recent_outcome_marks(1)[0]
            self.assertEqual(mark["side_adjusted_return"], "0.05")

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

    def test_durable_job_claim_completion_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.db")
            first = journal.enqueue_job(
                "scan", {"symbols": ["TEST1"]}, dedupe_key="daily:2026-08-28"
            )
            duplicate = journal.enqueue_job(
                "scan", {"symbols": ["TEST2"]}, dedupe_key="daily:2026-08-28"
            )

            self.assertEqual(first, duplicate)
            claimed = journal.claim_job("worker-one", lease_seconds=60)
            self.assertEqual(claimed["job_id"], first)
            self.assertEqual(claimed["payload"], {"symbols": ["TEST1"]})
            self.assertEqual(claimed["attempts"], 1)

            journal.finish_job(first, {"run_id": "run-one"})

            self.assertEqual(journal.job_counts(), {"complete": 1})
            self.assertEqual(journal.recent_jobs(1)[0]["result"], {"run_id": "run-one"})

    def test_versioned_universe_and_supervised_agent_tasks_are_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "journal.db")
            supervisor = journal.create_agent_task("supervisor", "direct research", model="strong")
            worker = journal.create_agent_task(
                "discovery", "find targets", model="cheap", parent_task_id=supervisor
            )
            job = journal.enqueue_job("discover", {"task_id": worker})
            journal.update_agent_task(worker, status="running", progress="searching", checkpoint={"phase": 1})
            snapshot = journal.save_universe_snapshot(
                [
                    {"symbol": "NEW1", "name": "New One", "confidence": "0.8"},
                    {"symbol": "OLD1", "name": "Old One", "confidence": "0.1", "status": "rejected"},
                ],
                source="test",
                mandate={"themes": ["power"]},
                citations=["https://example.test/source"],
            )

            self.assertEqual(journal.current_universe(), ["NEW1"])
            self.assertEqual(
                {item["status"] for item in journal.current_universe_candidates()},
                {"active", "rejected"},
            )
            self.assertEqual(journal.universe_snapshots(1)[0]["snapshot_id"], snapshot)
            self.assertEqual(journal.agent_task(worker)["parent_task_id"], supervisor)

            journal.set_agent_task_status(worker, "paused")
            self.assertEqual(journal.agent_task(worker)["status"], "paused")
            self.assertEqual(journal.recent_jobs(1)[0]["status"], "paused")
            journal.set_agent_task_status(worker, "queued")
            self.assertEqual(journal.recent_jobs(1)[0]["status"], "queued")

            run_id = journal.start_run("learning", {})
            experiment_ids = journal.propose_learning_experiments(
                run_id,
                [{"parameter": "research_prompt", "proposal": "Shadow-test primary-source weighting"}],
            )
            self.assertEqual(journal.learning_experiments(1)[0]["experiment_id"], experiment_ids[0])


if __name__ == "__main__":
    unittest.main()
