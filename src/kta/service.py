"""Long-running coordinator with durable jobs and independently scheduled loops."""

import json
import logging
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .config import Settings
from .domain import (
    AssetClass,
    CriticDecision,
    OrderRequest,
    RunStatus,
    Side,
    TradeIntent,
    jsonable,
    utc_now,
)
from .runtime import RuntimeComponents, build_components, build_loop
from .market import completed_daily_bars
from .serialization import evidence_from_dict, intent_from_dict, signal_from_dict
from .strategy import TrendPullbackStrategy
from .tool_agent import AgentTool, OpenRouterToolAgent


TERMINAL_SUGGESTION_STATES = {
    "rejected",
    "rejected_budget",
    "stale",
    "executed",
    "risk_rejected",
    "error",
}
logger = logging.getLogger("kta.daemon")


class DaemonService:
    def __init__(self, settings: Settings, components: Optional[RuntimeComponents] = None):
        self.settings = settings
        self.components = components or build_components(settings)
        self.journal = self.components.journal
        self.stop_event = threading.Event()
        self.started_at = utc_now()
        self._threads: List[threading.Thread] = []
        self._agent_lock = threading.Lock()
        self._execution_lock = threading.Lock()
        self._last_trigger_poll = 0.0
        self._last_scheduled_date: Optional[str] = None
        self._last_discovery_bucket: Optional[str] = None
        self._last_outcome_date: Optional[str] = None
        self._scan_started = False
        self._strategy = TrendPullbackStrategy()

    def start_background_loops(self) -> None:
        if self._threads:
            return
        recovered = 0
        for record in self.journal.pending_order_records():
            stored = record.get("payload") or {}
            if not all(key in stored for key in ("order", "receipt", "intent")):
                continue
            self.journal.enqueue_job(
                "reconcile_order",
                {
                    "run_id": record["run_id"],
                    "intent_id": record["intent_id"],
                    "suggestion_id": stored.get("suggestion_id"),
                },
                priority=115,
                dedupe_key="reconcile-recovery:{}".format(record["intent_id"]),
            )
            recovered += 1
        self.journal.service_event("daemon_started", "Daemon background loops started")
        if recovered:
            self.journal.service_event(
                "order_reconciliation_recovered",
                "Recovered pending broker orders for reconciliation",
                {"order_count": recovered},
            )
        recovered_suggestions = self._recover_suggestion_jobs()
        if recovered_suggestions:
            self.journal.service_event(
                "suggestion_jobs_recovered",
                "Recovered nonterminal trade suggestions with no active job",
                {"job_count": recovered_suggestions},
            )
        logger.info("daemon background loops started")
        scheduler = threading.Thread(target=self._scheduler_loop, name="kta-scheduler", daemon=True)
        trigger = threading.Thread(target=self._trigger_loop, name="kta-triggers", daemon=True)
        supervisor = threading.Thread(target=self._supervisor_loop, name="kta-supervisor", daemon=True)
        self._threads.extend([scheduler, trigger, supervisor])
        for index in range(self.settings.daemon_workers):
            self._threads.append(
                threading.Thread(
                    target=self._worker_loop,
                    args=("worker-{}-{}".format(index + 1, uuid.uuid4().hex[:8]),),
                    name="kta-worker-{}".format(index + 1),
                    daemon=True,
                )
            )
        for thread in self._threads:
            thread.start()

    def _recover_suggestion_jobs(self) -> int:
        recovered = 0
        now = utc_now()
        for suggestion in self.journal.trade_suggestions(1000):
            status = suggestion["status"]
            suggestion_id = suggestion["suggestion_id"]
            if status in {"proposed", "pending_revalidation", "approved_awaiting_open"}:
                if self.journal.has_active_job_for_suggestion("review_suggestion", suggestion_id):
                    continue
                available_at = now
                revalidate = status == "pending_revalidation"
                if status == "approved_awaiting_open":
                    revalidate = True
                    raw_time = suggestion.get("execute_after")
                    if raw_time:
                        available_at = max(now, datetime.fromisoformat(raw_time))
                self.journal.enqueue_job(
                    "review_suggestion",
                    {"suggestion_id": suggestion_id, "revalidate": revalidate},
                    available_at=available_at,
                    priority=100 if suggestion["side"] == "sell" else 60,
                    dedupe_key="suggestion-recovery:{}:{}".format(
                        suggestion_id, self.started_at.isoformat()
                    ),
                )
                recovered += 1
            elif status == "approved_ready":
                if self.journal.has_active_job_for_suggestion("execute_suggestion", suggestion_id):
                    continue
                self.journal.enqueue_job(
                    "execute_suggestion",
                    {"suggestion_id": suggestion_id},
                    priority=120 if suggestion["side"] == "sell" else 70,
                    dedupe_key="execution-recovery:{}:{}".format(
                        suggestion_id, self.started_at.isoformat()
                    ),
                )
                recovered += 1
        return recovered

    def stop(self) -> None:
        self.stop_event.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self.journal.service_event("daemon_stopped", "Daemon background loops stopped")
        logger.info("daemon background loops stopped")

    def status(self) -> Dict[str, Any]:
        session: Dict[str, Any]
        try:
            session = jsonable(self.components.market_clock.session())
        except Exception as error:
            session = {"error": "{}: {}".format(type(error).__name__, error)}
        return {
            "status": "stopping" if self.stop_event.is_set() else "running",
            "started_at": self.started_at,
            "uptime_seconds": int((utc_now() - self.started_at).total_seconds()),
            "threads": [
                {"name": thread.name, "alive": thread.is_alive()} for thread in self._threads
            ],
            "jobs": self.journal.job_counts(),
            "market": session,
            "active_triggers": len(self.journal.active_price_triggers()),
            "suggestions": self._suggestion_counts(),
            "agent_tasks": self._agent_task_counts(),
            "universe": {
                "symbols": self._current_universe(),
                "snapshots": self.journal.universe_snapshots(3),
            },
            "recent_runs": self.journal.recent_runs(5),
            "config": self.settings.safe_dict(),
        }

    def enqueue_scan(
        self,
        symbols: Optional[List[str]] = None,
        *,
        cause: str = "manual",
        dedupe_key: Optional[str] = None,
    ) -> str:
        selected = [item.strip().upper() for item in (symbols or self._current_universe()) if item.strip()]
        if not selected:
            raise ValueError("A scan requires at least one configured symbol")
        job_id = self.journal.enqueue_job(
            "scan",
            {"symbols": selected, "cause": cause},
            priority=80 if cause.startswith("price_trigger") else 20,
            dedupe_key=dedupe_key,
        )
        self.journal.service_event(
            "scan_queued", "Scan queued", {"job_id": job_id, "symbols": selected, "cause": cause}
        )
        return job_id

    def enqueue_discovery(self, *, cause: str = "manual", dedupe_key: Optional[str] = None) -> str:
        if not self.settings.discovery_enabled or self.components.discovery is None:
            raise ValueError("Continuous discovery is not enabled")
        if dedupe_key:
            existing = self.journal.job_for_dedupe(dedupe_key)
            if existing:
                return existing
        supervisor_task = self.journal.create_agent_task(
            "supervisor",
            "Direct and verify a research-driven universe refresh",
            model=self.settings.supervisor_model,
        )
        discovery_task = self.journal.create_agent_task(
            "discovery",
            "Find and source new investment targets from the configured mandate",
            model=self.settings.discovery_model,
            parent_task_id=supervisor_task,
        )
        job_id = self.journal.enqueue_job(
            "discover",
            {"cause": cause, "task_id": discovery_task, "supervisor_task_id": supervisor_task},
            priority=30,
            max_attempts=5,
            dedupe_key=dedupe_key,
        )
        self.journal.service_event(
            "discovery_queued",
            "Research-driven universe discovery queued",
            {"job_id": job_id, "task_id": discovery_task, "cause": cause},
        )
        return job_id

    def create_price_trigger(
        self,
        symbol: str,
        comparison: str,
        threshold: Any,
        *,
        source: str = "user",
        one_shot: bool = True,
        rationale: str = "",
    ) -> str:
        normalized = symbol.strip().upper()
        value = Decimal(str(threshold))
        if normalized not in set(self._current_universe()):
            raise ValueError("Price triggers are limited to the current versioned universe")
        if comparison not in {"above", "below"}:
            raise ValueError("comparison must be above or below")
        if value <= 0:
            raise ValueError("threshold must be positive")
        trigger_id = self.journal.create_price_trigger(
            normalized,
            comparison,
            value,
            source=source,
            one_shot=one_shot,
            metadata={"rationale": rationale[:1000]},
            deduplicate_active=source.endswith("agent"),
        )
        self.journal.service_event(
            "price_trigger_created",
            "Price trigger created",
            {
                "trigger_id": trigger_id,
                "symbol": normalized,
                "comparison": comparison,
                "threshold": value,
                "source": source,
            },
        )
        return trigger_id

    def ingest_price(self, symbol: str, price: Any, source: str = "webhook") -> Dict[str, Any]:
        normalized = symbol.strip().upper()
        value = Decimal(str(price))
        if value <= 0:
            raise ValueError("price must be positive")
        fired: List[str] = []
        for trigger in self.journal.active_price_triggers():
            if trigger["symbol"] != normalized:
                continue
            threshold = Decimal(trigger["threshold"])
            previous = Decimal(trigger["last_price"]) if trigger["last_price"] else None
            crossed = (
                value >= threshold and (previous is None or previous < threshold)
                if trigger["comparison"] == "above"
                else value <= threshold and (previous is None or previous > threshold)
            )
            self.journal.record_price_check(trigger["trigger_id"], value, crossed)
            if crossed:
                fired.append(trigger["trigger_id"])
                if trigger.get("metadata", {}).get("kind") == "protective_stop":
                    self.journal.enqueue_job(
                        "protective_exit",
                        {
                            "symbol": normalized,
                            "threshold": trigger["threshold"],
                            "trigger_id": trigger["trigger_id"],
                        },
                        priority=200,
                        dedupe_key="protective-exit:{}".format(trigger["trigger_id"]),
                    )
                else:
                    firing_id = uuid.uuid4().hex
                    cause_prefix = (
                        "agent_price_trigger"
                        if trigger.get("source") in {"agent", "chat-agent"}
                        else "price_trigger"
                    )
                    self.enqueue_scan(
                        [normalized],
                        cause="{}:{}:{}:{}".format(
                            cause_prefix, trigger["trigger_id"], source, firing_id
                        ),
                    )
        self.journal.service_event(
            "price_webhook_received",
            "External price event received",
            {"symbol": normalized, "price": value, "source": source, "fired": fired},
        )
        return {"symbol": normalized, "price": str(value), "fired_trigger_ids": fired}

    def submit_chat(self, conversation_id: str, content: str) -> Dict[str, str]:
        clean = content.strip()
        if not clean:
            raise ValueError("message cannot be empty")
        message_id = self.journal.add_message(conversation_id, "user", clean)
        job_id = self.journal.enqueue_job(
            "chat",
            {"conversation_id": conversation_id, "message_id": message_id, "content": clean},
            priority=10,
        )
        return {"message_id": message_id, "job_id": job_id, "conversation_id": conversation_id}

    def _api_budget_available(self) -> bool:
        now = utc_now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = self.journal.api_cost_since(month_start)
        if spent >= self.settings.monthly_api_soft_budget_usd:
            self.journal.service_event(
                "api_soft_budget_warning",
                "Monthly API soft budget has been reached; work continues",
                {"spent_usd": spent, "soft_budget_usd": self.settings.monthly_api_soft_budget_usd},
                level="warning",
            )
        return not self.settings.enforce_monthly_api_budget or spent < self.settings.monthly_api_budget_usd

    def _agent_origin_budget_available(self) -> bool:
        now = utc_now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = self.journal.agent_origin_cost_since(month_start)
        return spent < self.settings.agent_originated_monthly_budget_usd

    @staticmethod
    def _agent_originated_cause(cause: str) -> bool:
        return cause == "chat_agent" or cause.startswith("agent_price_trigger:")

    def _current_universe(self) -> List[str]:
        generated = self.journal.current_universe()
        return generated or list(self.settings.universe)

    def _agent_task_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in self.journal.agent_tasks(1000):
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return counts

    def _suggestion_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in self.journal.trade_suggestions(1000):
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return counts

    def _scheduler_loop(self) -> None:
        eastern = ZoneInfo("America/New_York")
        while not self.stop_event.wait(1):
            now = utc_now().astimezone(eastern)
            if self.settings.discovery_enabled:
                bucket = int(now.timestamp()) // (self.settings.discovery_interval_hours * 3600)
                bucket_key = str(bucket)
                should_start = self.settings.discovery_on_start and self._last_discovery_bucket is None
                if should_start or self._last_discovery_bucket != bucket_key:
                    self._last_discovery_bucket = bucket_key
                    try:
                        self.enqueue_discovery(
                            cause="daemon_start" if should_start else "scheduled",
                            dedupe_key="discover:{}".format(bucket_key),
                        )
                    except Exception as error:
                        self._emit_error("discovery_scheduler_error", error)
            if self.settings.scan_on_start and not self._scan_started:
                self._scan_started = True
                try:
                    self.enqueue_scan(cause="daemon_start", dedupe_key="scan:start:{}".format(self.started_at))
                except Exception as error:
                    self._emit_error("scheduler_error", error)
            scheduled = now.replace(
                hour=self.settings.daily_scan_hour_et,
                minute=self.settings.daily_scan_minute_et,
                second=0,
                microsecond=0,
            )
            date_key = now.date().isoformat()
            if now.weekday() < 5 and now >= scheduled and self._last_scheduled_date != date_key:
                self._last_scheduled_date = date_key
                try:
                    self.enqueue_scan(cause="daily_schedule", dedupe_key="scan:daily:{}".format(date_key))
                except Exception as error:
                    self._emit_error("scheduler_error", error)
            outcome_time = scheduled.replace(minute=min(59, scheduled.minute + 10))
            if now.weekday() < 5 and now >= outcome_time and self._last_outcome_date != date_key:
                self._last_outcome_date = date_key
                try:
                    self.journal.enqueue_job(
                        "evaluate_outcomes",
                        {"date": date_key},
                        priority=15,
                        dedupe_key="outcomes:{}".format(date_key),
                    )
                except Exception as error:
                    self._emit_error("outcome_scheduler_error", error)

    def _supervisor_loop(self) -> None:
        """Observe durable work without imposing arbitrary cognitive limits."""

        reported = set()
        while not self.stop_event.wait(15):
            now = utc_now()
            for task in self.journal.agent_tasks(500):
                if task["status"] != "running":
                    continue
                heartbeat = datetime.fromisoformat(task["heartbeat_at"])
                stale_seconds = (now - heartbeat).total_seconds()
                threshold = max(3600, self.settings.job_lease_seconds * 2)
                if stale_seconds > threshold and task["task_id"] not in reported:
                    reported.add(task["task_id"])
                    self.journal.service_event(
                        "supervisor_attention",
                        "Agent task has not checkpointed recently",
                        {"task_id": task["task_id"], "stale_seconds": int(stale_seconds)},
                        level="warning",
                    )

    def _trigger_loop(self) -> None:
        while not self.stop_event.wait(1):
            current = monotonic()
            if current - self._last_trigger_poll < self.settings.trigger_poll_seconds:
                continue
            self._last_trigger_poll = current
            for trigger in self.journal.active_price_triggers():
                try:
                    price = self.components.market.latest_price(trigger["symbol"])
                    threshold = Decimal(trigger["threshold"])
                    previous = Decimal(trigger["last_price"]) if trigger["last_price"] else None
                    crossed = (
                        price >= threshold and (previous is None or previous < threshold)
                        if trigger["comparison"] == "above"
                        else price <= threshold and (previous is None or previous > threshold)
                    )
                    if crossed and trigger["last_fired_at"] and not trigger["one_shot"]:
                        last_fired = datetime.fromisoformat(trigger["last_fired_at"])
                        crossed = utc_now() - last_fired >= timedelta(
                            seconds=int(trigger["cooldown_seconds"])
                        )
                    self.journal.record_price_check(trigger["trigger_id"], price, crossed)
                    if crossed:
                        if trigger.get("metadata", {}).get("kind") == "protective_stop":
                            self.journal.enqueue_job(
                                "protective_exit",
                                {
                                    "symbol": trigger["symbol"],
                                    "threshold": trigger["threshold"],
                                    "trigger_id": trigger["trigger_id"],
                                },
                                priority=200,
                                dedupe_key="protective-exit:{}".format(trigger["trigger_id"]),
                            )
                        else:
                            firing_id = uuid.uuid4().hex
                            cause_prefix = (
                                "agent_price_trigger"
                                if trigger.get("source") in {"agent", "chat-agent"}
                                else "price_trigger"
                            )
                            self.enqueue_scan(
                                [trigger["symbol"]],
                                cause="{}:{}:{}".format(
                                    cause_prefix, trigger["trigger_id"], firing_id
                                ),
                                dedupe_key="trigger-fire:{}:{}".format(
                                    trigger["trigger_id"], firing_id
                                ),
                            )
                        self.journal.service_event(
                            "price_trigger_fired",
                            "Price trigger fired",
                            {"trigger": trigger, "price": price},
                        )
                        logger.info(
                            "price trigger fired trigger_id=%s symbol=%s price=%s",
                            trigger["trigger_id"],
                            trigger["symbol"],
                            price,
                        )
                except Exception as error:
                    self._emit_error(
                        "trigger_check_error", error, {"trigger_id": trigger["trigger_id"]}
                    )

    def _worker_loop(self, worker_id: str) -> None:
        self.journal.service_event("worker_started", "Worker started", {"worker_id": worker_id})
        logger.info("worker started worker_id=%s", worker_id)
        while not self.stop_event.is_set():
            job = self.journal.claim_job(worker_id, self.settings.job_lease_seconds)
            if job is None:
                self.stop_event.wait(self.settings.job_poll_seconds)
                continue
            self.journal.service_event(
                "job_started",
                "Job started",
                {"job_id": job["job_id"], "kind": job["kind"], "attempt": job["attempts"]},
            )
            logger.info(
                "job started job_id=%s kind=%s attempt=%s",
                job["job_id"],
                job["kind"],
                job["attempts"],
            )
            try:
                result = self._dispatch(job)
                self.journal.finish_job(job["job_id"], result)
                self.journal.service_event(
                    "job_completed",
                    "Job completed",
                    {"job_id": job["job_id"], "kind": job["kind"], "result": result},
                )
                logger.info("job completed job_id=%s kind=%s", job["job_id"], job["kind"])
            except Exception as error:
                message = "{}: {}".format(type(error).__name__, error)
                delay = min(900, 30 * (2 ** max(0, int(job["attempts"]) - 1)))
                status = self.journal.fail_job(job["job_id"], message, retry_delay_seconds=delay)
                self.journal.service_event(
                    "job_failed",
                    "Job failed",
                    {"job_id": job["job_id"], "kind": job["kind"], "status": status, "error": message},
                    level="error",
                )
                logger.error(
                    "job failed job_id=%s kind=%s status=%s error=%s",
                    job["job_id"],
                    job["kind"],
                    status,
                    message,
                )

    def _dispatch(self, job: Dict[str, Any]) -> Dict[str, Any]:
        kind = job["kind"]
        if kind == "scan":
            return self._handle_scan(job["payload"])
        if kind == "discover":
            return self._handle_discovery(job["payload"])
        if kind == "evaluate_outcomes":
            return self._handle_evaluate_outcomes(job["payload"])
        if kind == "learning_review":
            return self._handle_learning_review(job["payload"])
        if kind == "reconcile_order":
            return self._handle_reconcile_order(job["payload"])
        if kind == "protective_exit":
            return self._handle_protective_exit(job["payload"])
        if kind == "review_suggestion":
            return self._handle_review_suggestion(job["payload"])
        if kind == "execute_suggestion":
            return self._handle_execute_suggestion(job["payload"])
        if kind == "chat":
            return self._handle_chat(job["payload"])
        raise ValueError("Unknown job kind {}".format(kind))

    def _handle_evaluate_outcomes(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now()
        pending = self.journal.pending_intent_outcomes()
        bars_by_symbol: Dict[str, List[Any]] = {}
        marked = 0
        for outcome in pending:
            symbol = str(outcome["symbol"])
            if symbol not in bars_by_symbol:
                bars_by_symbol[symbol] = completed_daily_bars(
                    self.components.market.daily_bars(symbol, 500), now
                )
            signal_at = datetime.fromisoformat(outcome["signal_at"])
            forward = [bar for bar in bars_by_symbol[symbol] if bar.timestamp > signal_at]
            horizon = int(outcome["horizon_sessions"])
            if len(forward) < horizon:
                continue
            window = forward[:horizon]
            baseline = Decimal(outcome["baseline_price"])
            if baseline <= 0:
                continue
            raw_return = (window[-1].close / baseline) - Decimal("1")
            high_return = (max(bar.high for bar in window) / baseline) - Decimal("1")
            low_return = (min(bar.low for bar in window) / baseline) - Decimal("1")
            if outcome["side"] == "sell":
                adjusted = -raw_return
                favorable = -low_return
                adverse = -high_return
            else:
                adjusted = raw_return
                favorable = high_return
                adverse = low_return
            original = outcome.get("metadata") or {}
            stop_value = original.get("stop_price")
            stop_triggered = False
            if stop_value is not None:
                stop = Decimal(str(stop_value))
                stop_triggered = (
                    any(bar.high >= stop for bar in window)
                    if outcome["side"] == "sell"
                    else any(bar.low <= stop for bar in window)
                )
            estimated_round_trip_cost = Decimal("0.0010")
            self.journal.mark_intent_outcome(
                outcome["intent_id"],
                horizon,
                marked_at=window[-1].timestamp,
                mark_price=window[-1].close,
                raw_return=raw_return,
                side_adjusted_return=adjusted,
                maximum_favorable_excursion=favorable,
                maximum_adverse_excursion=adverse,
                metadata={
                    **original,
                    "disposition": outcome["disposition"],
                    "evaluated_at": now,
                    "stop_triggered": stop_triggered,
                    "estimated_round_trip_cost_bps": 10,
                    "net_side_adjusted_return": adjusted - estimated_round_trip_cost,
                },
            )
            marked += 1
        review_job_id = None
        if marked:
            review_job_id = self.journal.enqueue_job(
                "learning_review",
                {"cause": "mature_outcomes", "mark_count": marked},
                priority=12,
                dedupe_key="learning-review:{}".format(str(payload.get("date") or now.date())),
            )
        self.journal.service_event(
            "outcomes_evaluated",
            "Mature counterfactual outcomes evaluated",
            {"pending": len(pending), "marked": marked, "review_job_id": review_job_id},
        )
        return {"pending": len(pending), "marked": marked, "review_job_id": review_job_id}

    def _handle_learning_review(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._api_budget_available():
            return {"status": "budget_blocked"}
        last_review = self.journal.last_review_at()
        if last_review and utc_now() - last_review < timedelta(hours=self.settings.review_min_interval_hours):
            return {"status": "interval_not_elapsed"}
        marks = self.journal.recent_outcome_marks(250)
        if not marks:
            return {"status": "no_mature_outcomes"}
        run_id = self.journal.start_run("daemon-learning-review", self.settings.safe_dict())
        events = [
            {
                "event_type": "outcome_mark",
                "created_at": item["marked_at"],
                "payload": item,
            }
            for item in marks
        ]
        try:
            with self._agent_lock:
                review = self.components.agents.review(events, [])
                self._capture_usage(run_id, self.components.agents)
            self.journal.save_review(run_id, review)
            experiment_ids = self.journal.propose_learning_experiments(
                run_id, review.suggested_changes
            )
            self.journal.event(run_id, "learning_review", review)
            self.journal.event(
                run_id, "learning_experiments_proposed", {"experiment_ids": experiment_ids}
            )
            self.journal.finish_run(run_id, RunStatus.COMPLETE)
            return {"status": "complete", "run_id": run_id, "experiments": len(experiment_ids)}
        except Exception as error:
            self._capture_usage(run_id, self.components.agents)
            self.journal.finish_run(run_id, RunStatus.FAILED, str(error))
            raise

    def _handle_discovery(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.components.discovery is None:
            raise RuntimeError("Discovery engine is not configured")
        task_id = str(payload["task_id"])
        supervisor_task_id = str(payload["supervisor_task_id"])
        mandate = self.settings.safe_dict()["mandate"]
        self.journal.update_agent_task(
            supervisor_task_id,
            status="running",
            progress="Supervising discovery and evidence validation",
            checkpoint={"phase": "delegated", "child_task_id": task_id},
        )
        self.journal.update_agent_task(
            task_id,
            status="running",
            progress="Searching the mandate and expanding the candidate graph",
            checkpoint={"phase": "research", "mandate": mandate},
        )
        run_id = self.journal.start_run("universe-discovery", self.settings.safe_dict())
        try:
            result = self.components.discovery.discover(mandate, self.settings.universe)
            eligible_count = self._validate_discovery_candidates(result.candidates)
            current_task = self.journal.agent_task(task_id)
            if current_task is not None and current_task["status"] == "cancelled":
                self.journal.finish_run(run_id, RunStatus.FAILED, "Cancelled by operator")
                return {"run_id": run_id, "status": "cancelled"}
            if current_task is not None and current_task["status"] == "paused":
                self.journal.finish_run(run_id, RunStatus.FAILED, "Paused by operator")
                return {"run_id": run_id, "status": "paused"}
            self.journal.update_agent_task(
                task_id,
                progress="Validating and recording {} sourced candidates".format(len(result.candidates)),
                checkpoint={
                    "phase": "persist",
                    "candidate_count": len(result.candidates),
                    "eligible_count": eligible_count,
                },
            )
            snapshot_id = self.journal.save_universe_snapshot(
                result.candidates,
                source="agentic-discovery",
                mandate=mandate,
                citations=result.citations,
            )
            for usage in result.usage:
                self.journal.record_api_usage(run_id, usage)
                self.journal.event(run_id, "api_usage", usage)
            self.journal.event(
                run_id,
                "universe_discovered",
                {"snapshot_id": snapshot_id, "candidates": result.candidates, "citations": result.citations},
            )
            self.journal.finish_run(run_id, RunStatus.COMPLETE)
            self.journal.update_agent_task(
                task_id,
                status="complete",
                progress="Created universe snapshot {}".format(snapshot_id),
                checkpoint={"phase": "complete", "snapshot_id": snapshot_id},
            )
            self.journal.update_agent_task(
                supervisor_task_id,
                status="complete",
                progress="Accepted {} eligible candidates from {} sourced names".format(
                    eligible_count, len(result.candidates)
                ),
                checkpoint={
                    "snapshot_id": snapshot_id,
                    "candidate_count": len(result.candidates),
                    "eligible_count": eligible_count,
                },
            )
            scan_job_id = None
            if self.settings.scan_after_discovery and self.journal.current_universe():
                scan_job_id = self.enqueue_scan(
                    cause="universe_discovery",
                    dedupe_key="scan:universe:{}".format(snapshot_id),
                )
            return {
                "run_id": run_id,
                "snapshot_id": snapshot_id,
                "candidates": len(result.candidates),
                "eligible_candidates": eligible_count,
                "scan_job_id": scan_job_id,
            }
        except Exception as error:
            self.journal.finish_run(run_id, RunStatus.FAILED, str(error))
            self.journal.update_agent_task(task_id, status="failed", error=str(error), progress="Discovery failed")
            self.journal.update_agent_task(
                supervisor_task_id, status="failed", error=str(error), progress="Child discovery task failed"
            )
            raise

    def _validate_discovery_candidates(self, candidates: List[Dict[str, Any]]) -> int:
        """Require verified security metadata and completed-bar liquidity before activation."""

        now = utc_now()
        active = 0
        for candidate in candidates:
            if candidate.get("status") != "active":
                continue
            symbol = str(candidate["symbol"]).upper()
            metadata = candidate.setdefault("metadata", {})
            reasons: List[str] = []
            asset: Dict[str, Any] = {"symbol": symbol, "verified": False}
            if self.components.asset_directory is None:
                reasons.append("No verified asset directory is configured")
            else:
                try:
                    asset = self.components.asset_directory.asset_metadata(symbol)
                except Exception as error:
                    reasons.append("Asset lookup failed: {}".format(type(error).__name__))
            if not asset.get("verified"):
                reasons.append("Asset identity is unverified")
            if asset.get("verified") and asset.get("asset_class") != "us_equity":
                reasons.append("Asset is not a US equity or ETF")
            if asset.get("verified") and asset.get("status") != "active":
                reasons.append("Asset is not active")
            if asset.get("verified") and not asset.get("tradable"):
                reasons.append("Asset is not tradable")

            bars = []
            try:
                bars = completed_daily_bars(self.components.market.daily_bars(symbol, 30), now)[-20:]
            except Exception as error:
                reasons.append("Market-data lookup failed: {}".format(type(error).__name__))
            average_dollar_volume = Decimal("0")
            last_close = None
            if len(bars) < 20:
                reasons.append("Fewer than 20 completed daily bars")
            else:
                last_close = bars[-1].close
                average_dollar_volume = sum(
                    (bar.close * bar.volume for bar in bars), Decimal("0")
                ) / Decimal(len(bars))
                if last_close < self.settings.discovery_min_price:
                    reasons.append("Price is below the configured minimum")
                if average_dollar_volume < self.settings.discovery_min_daily_dollar_volume:
                    reasons.append("Daily dollar volume is below the configured minimum")

            metadata["eligibility"] = {
                "checked_at": now,
                "asset": asset,
                "completed_bar_count": len(bars),
                "last_close": last_close,
                "average_daily_dollar_volume": average_dollar_volume,
                "minimum_price": self.settings.discovery_min_price,
                "minimum_daily_dollar_volume": self.settings.discovery_min_daily_dollar_volume,
                "reasons": reasons,
            }
            if reasons:
                candidate["status"] = "rejected"
                metadata["eligibility_reason"] = "; ".join(dict.fromkeys(reasons))
            else:
                active += 1
        return active

    def _handle_scan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        cause = str(payload.get("cause") or "")
        agent_originated = self._agent_originated_cause(cause)
        if agent_originated and not self._agent_origin_budget_available():
            self.journal.service_event(
                "agent_origin_budget_blocked",
                "Agent-originated scan blocked by its separate monthly budget",
                {
                    "cause": cause,
                    "budget_usd": self.settings.agent_originated_monthly_budget_usd,
                },
                level="warning",
            )
            return {"status": "agent_origin_budget_blocked", "cause": cause}
        progress_events: List[str] = []

        def progress(message: str) -> None:
            progress_events.append(message)
            self.journal.service_event("scan_progress", message, {"cause": payload.get("cause")})
            logger.info("scan progress cause=%s stage=%s", payload.get("cause"), message)

        with self._agent_lock:
            result = build_loop(
                self.settings,
                progress=progress,
                defer_execution=True,
                components=self.components,
            ).run(
                [str(item) for item in payload["symbols"]],
                trigger_context=(
                    str(payload.get("cause"))
                    if str(payload.get("cause") or "").startswith(
                        ("price_trigger:", "agent_price_trigger:")
                    )
                    else None
                ),
            )
        if result.status != RunStatus.COMPLETE:
            raise RuntimeError("Scan run {} failed: {}".format(result.run_id, "; ".join(result.errors)))
        if agent_originated:
            self.journal.record_agent_origin_cost(
                result.run_id, cause, Decimal(result.api_cost_usd)
            )
        return {
            "run_id": result.run_id,
            "signals": result.signal_count,
            "suggestions": result.proposed_count,
            "api_cost_usd": result.api_cost_usd,
            "last_stage": progress_events[-1] if progress_events else None,
        }

    def _capture_usage(self, run_id: str, provider) -> Decimal:
        total = Decimal("0")
        for usage in provider.drain_usage():
            self.journal.record_api_usage(run_id, usage)
            self.journal.event(run_id, "api_usage", usage)
            if usage.cost_usd is not None:
                total += usage.cost_usd
        return total

    def _ensure_protective_stop(
        self, run_id: str, intent: TradeIntent, receipt: Any
    ) -> Optional[str]:
        if intent.side == Side.SELL:
            self.journal.disable_protective_stops(intent.symbol)
            return None
        if receipt.status != "filled" or intent.stop_price is None or intent.stop_price <= 0:
            return None
        trigger_id = self.journal.upsert_protective_stop(
            intent.symbol,
            intent.stop_price,
            {
                "kind": "protective_stop",
                "intent_id": intent.intent_id,
                "broker_order_id": receipt.broker_order_id,
                "run_id": run_id,
            },
        )
        self.journal.event(
            run_id,
            "protective_stop_armed",
            {"trigger_id": trigger_id, "symbol": intent.symbol, "stop_price": intent.stop_price},
        )
        return trigger_id

    def _record_order_receipt(
        self,
        run_id: str,
        intent: TradeIntent,
        order: OrderRequest,
        receipt: Any,
        suggestion_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "order": order,
            "receipt": receipt,
            "intent": intent,
            "suggestion_id": suggestion_id,
        }
        self.journal.update_order(intent.intent_id, receipt.status, payload)
        if receipt.status == "filled":
            self.journal.event(run_id, "order_filled", receipt)
            self.journal.update_intent_disposition(intent.intent_id, "filled")
            trigger_id = self._ensure_protective_stop(run_id, intent, receipt)
            return {"status": "filled", "protective_trigger_id": trigger_id}
        terminal = {"canceled", "cancelled", "rejected", "expired", "failed"}
        if receipt.status in terminal:
            self.journal.event(run_id, "order_terminal_unfilled", receipt)
            self.journal.update_intent_disposition(intent.intent_id, "order_{}".format(receipt.status))
            return {"status": receipt.status}
        self.journal.update_intent_disposition(intent.intent_id, "submitted_awaiting_fill")
        job_id = self.journal.enqueue_job(
            "reconcile_order",
            {
                "run_id": run_id,
                "intent_id": intent.intent_id,
                "suggestion_id": suggestion_id,
            },
            available_at=utc_now() + timedelta(seconds=30),
            priority=110,
        )
        return {"status": "submitted_awaiting_fill", "reconcile_job_id": job_id}

    def _handle_reconcile_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        intent_id = str(payload["intent_id"])
        record = self.journal.order_record(intent_id)
        if record is None:
            raise KeyError("Unknown order intent {}".format(intent_id))
        stored = record["payload"]
        intent = intent_from_dict(stored["intent"])
        order_value = stored["order"]
        order = OrderRequest(
            client_order_id=str(order_value["client_order_id"]),
            intent_id=str(order_value["intent_id"]),
            symbol=str(order_value["symbol"]),
            side=Side(str(order_value["side"])),
            asset_class=AssetClass(str(order_value["asset_class"])),
            notional=Decimal(order_value["notional"]) if order_value.get("notional") else None,
            quantity=Decimal(order_value["quantity"]) if order_value.get("quantity") else None,
            order_type=str(order_value.get("order_type") or "market"),
            time_in_force=str(order_value.get("time_in_force") or "day"),
        )
        prior_receipt = stored["receipt"]
        receipt = self.components.broker.order_status(str(prior_receipt["broker_order_id"]))
        state = self._record_order_receipt(
            str(payload["run_id"]), intent, order, receipt, payload.get("suggestion_id")
        )
        suggestion_id = payload.get("suggestion_id")
        if suggestion_id and state["status"] == "filled":
            self.journal.update_trade_suggestion(str(suggestion_id), "executed")
        return {"intent_id": intent_id, **state}

    def _handle_protective_exit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(payload["symbol"]).upper()
        positions = self.components.broker.positions()
        position = next((item for item in positions if item.symbol == symbol), None)
        if position is None or position.quantity <= 0:
            self.journal.disable_protective_stops(symbol)
            return {"symbol": symbol, "status": "no_position"}
        now = utc_now()
        price = self.components.market.latest_price(symbol)
        intent = TradeIntent.create(
            symbol=symbol,
            asset_class=position.asset_class,
            side=Side.SELL,
            signal_as_of=now,
            strategy_version="protective-stop-v1",
            thesis="Deterministic protective stop crossed.",
            confidence=Decimal("1"),
            requested_quantity=position.quantity,
            entry_reference_price=price,
            stop_price=Decimal(str(payload["threshold"])),
            generated_by="deterministic-protective-stop",
        )
        run_id = self.journal.start_run("protective-exit", self.settings.safe_dict())
        self.journal.register_intent_outcomes(run_id, intent, price)
        decision = self.components.risk.evaluate(
            [intent], self.components.broker.account(), positions, [], now=now
        )[0]
        if not decision.approved:
            self.journal.event(run_id, "risk_rejected", decision)
            self.journal.finish_run(run_id, RunStatus.FAILED, decision.reason)
            return {"symbol": symbol, "status": "risk_rejected", "reason": decision.reason}
        order = OrderRequest(
            client_order_id="kta-{}".format(intent.intent_id),
            intent_id=intent.intent_id,
            symbol=symbol,
            side=Side.SELL,
            asset_class=position.asset_class,
            notional=None,
            quantity=decision.approved_quantity,
        )
        if not self.journal.claim_order(run_id, intent.intent_id, order.client_order_id, order):
            self.journal.finish_run(run_id, RunStatus.COMPLETE)
            return {"symbol": symbol, "status": "duplicate"}
        receipt = self.components.broker.submit(order)
        self.journal.event(run_id, "order_submitted", receipt)
        state = self._record_order_receipt(run_id, intent, order, receipt)
        self.journal.finish_run(run_id, RunStatus.COMPLETE)
        return {"symbol": symbol, **state}

    def _handle_review_suggestion(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        suggestion_id = str(payload["suggestion_id"])
        revalidate = bool(payload.get("revalidate"))
        suggestion = self.journal.trade_suggestion(suggestion_id)
        if suggestion is None:
            raise KeyError("Unknown suggestion {}".format(suggestion_id))
        if suggestion["status"] in TERMINAL_SUGGESTION_STATES:
            return {"suggestion_id": suggestion_id, "status": suggestion["status"], "noop": True}
        stored = suggestion["payload"]
        intent = intent_from_dict(stored["intent"])
        signals = [signal_from_dict(item) for item in stored.get("signals") or []]
        evidence = [evidence_from_dict(item) for item in stored.get("evidence") or []]
        if intent.side == Side.BUY and not self._api_budget_available():
            self.journal.update_trade_suggestion(
                suggestion_id,
                "rejected_budget",
                decision={"approved": False, "reason": "Monthly API budget reached."},
                reviewed=True,
                payload=stored,
            )
            return {"suggestion_id": suggestion_id, "status": "rejected_budget"}
        with self._agent_lock, self._execution_lock:
            if revalidate:
                positions = self.components.broker.positions()
                held = next((item for item in positions if item.symbol == intent.symbol), None)
                bars = completed_daily_bars(
                    self.components.market.daily_bars(intent.symbol, 261),
                    self.components.market_clock.session().timestamp,
                )[-260:]
                trail = self.journal.update_position_trail(
                    intent.symbol, bars[-1].high, bool(held and held.quantity > 0)
                )
                fresh_signal = self._strategy.analyze(bars, held, trail)
                if fresh_signal is None or fresh_signal.action.value != intent.side.value.replace("buy", "enter").replace("sell", "exit"):
                    self.journal.update_trade_suggestion(
                        suggestion_id,
                        "stale",
                        decision={"approved": False, "reason": "Signal did not survive market-open revalidation."},
                        reviewed=True,
                    )
                    return {"suggestion_id": suggestion_id, "status": "stale"}
                signals = [fresh_signal]
                evidence = self.components.research.gather(signals)
                self._capture_usage(suggestion["run_id"], self.components.research)
                intent = replace(
                    intent,
                    signal_as_of=fresh_signal.as_of,
                    confidence=min(intent.confidence, fresh_signal.confidence),
                    stop_price=fresh_signal.stop_price,
                    evidence_ids=[item.evidence_id for item in evidence],
                )
                stored = {"intent": intent, "signals": signals, "evidence": evidence}
            if intent.side == Side.SELL:
                critic = CriticDecision(intent.intent_id, True, "Protective exit is deterministically approved.")
            else:
                critic = self.components.agents.criticize([intent], signals, evidence)[0]
                self._capture_usage(suggestion["run_id"], self.components.agents)
        self.journal.event(suggestion["run_id"], "suggestion_reviewed", critic)
        if not critic.approved:
            self.journal.update_intent_disposition(intent.intent_id, "critic_rejected")
            self.journal.update_trade_suggestion(
                suggestion_id,
                "rejected",
                decision=critic,
                reviewed=True,
                payload=stored,
            )
            return {"suggestion_id": suggestion_id, "status": "rejected", "reason": critic.reason}
        session = self.components.market_clock.session()
        if not session.is_open:
            self.journal.update_trade_suggestion(
                suggestion_id,
                "approved_awaiting_open",
                decision=critic,
                execute_after=session.next_open,
                reviewed=True,
                payload=stored,
            )
            self.journal.enqueue_job(
                "review_suggestion",
                {"suggestion_id": suggestion_id, "revalidate": True},
                available_at=session.next_open,
                priority=100 if intent.side == Side.SELL else 60,
                dedupe_key="suggestion-review:{}:{}".format(suggestion_id, session.next_open.isoformat()),
            )
            return {
                "suggestion_id": suggestion_id,
                "status": "approved_awaiting_open",
                "next_review_at": session.next_open,
            }
        self.journal.update_trade_suggestion(
            suggestion_id,
            "approved_ready",
            decision=critic,
            reviewed=True,
            payload=stored,
        )
        self.journal.update_intent_disposition(intent.intent_id, "critic_approved")
        execute_job = self.journal.enqueue_job(
            "execute_suggestion",
            {"suggestion_id": suggestion_id},
            priority=120 if intent.side == Side.SELL else 70,
        )
        return {"suggestion_id": suggestion_id, "status": "approved_ready", "job_id": execute_job}

    def _handle_execute_suggestion(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        suggestion_id = str(payload["suggestion_id"])
        suggestion = self.journal.trade_suggestion(suggestion_id)
        if suggestion is None:
            raise KeyError("Unknown suggestion {}".format(suggestion_id))
        if suggestion["status"] == "executed":
            return {"suggestion_id": suggestion_id, "status": "executed", "noop": True}
        if suggestion["status"] != "approved_ready":
            raise RuntimeError("Suggestion {} is not ready for execution".format(suggestion_id))
        session = self.components.market_clock.session()
        if not session.is_open:
            self.journal.update_trade_suggestion(
                suggestion_id, "approved_awaiting_open", execute_after=session.next_open
            )
            self.journal.enqueue_job(
                "review_suggestion",
                {"suggestion_id": suggestion_id, "revalidate": True},
                available_at=session.next_open,
                priority=100,
                dedupe_key="suggestion-review:{}:{}".format(suggestion_id, session.next_open.isoformat()),
            )
            return {"suggestion_id": suggestion_id, "status": "approved_awaiting_open"}
        last_review = datetime.fromisoformat(suggestion["last_review_at"])
        if session.timestamp - last_review > timedelta(
            minutes=self.settings.suggestion_review_ttl_minutes
        ):
            self.journal.update_trade_suggestion(suggestion_id, "pending_revalidation")
            job_id = self.journal.enqueue_job(
                "review_suggestion",
                {"suggestion_id": suggestion_id, "revalidate": True},
                priority=100,
                dedupe_key="suggestion-review:{}:stale:{}".format(
                    suggestion_id, session.timestamp.replace(second=0, microsecond=0).isoformat()
                ),
            )
            return {"suggestion_id": suggestion_id, "status": "pending_revalidation", "job_id": job_id}
        intent = intent_from_dict(suggestion["payload"]["intent"])
        with self._execution_lock:
            price = self.components.market.latest_price(intent.symbol)
            if hasattr(self.components.broker, "set_price"):
                self.components.broker.set_price(intent.symbol, price)
            account = self.components.broker.account()
            positions = self.components.broker.positions()
            prior = self.journal.execution_totals_for_run(suggestion["run_id"])
            decisions = self.components.risk.evaluate(
                [intent],
                account,
                positions,
                [intent.intent_id] if self.journal.has_order(intent.intent_id) else [],
                now=session.timestamp,
                prior_run_new_exposure=prior["new_exposure_notional"],
                prior_run_order_count=prior["order_count"],
            )
            decision = decisions[0]
            self.journal.event(suggestion["run_id"], "queued_risk_decision", decision)
            if not decision.approved:
                self.journal.update_intent_disposition(intent.intent_id, "risk_rejected")
                self.journal.update_trade_suggestion(
                    suggestion_id, "risk_rejected", decision=decision, reviewed=False
                )
                return {"suggestion_id": suggestion_id, "status": "risk_rejected", "reason": decision.reason}
            order = OrderRequest(
                client_order_id="kta-{}".format(intent.intent_id),
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                side=intent.side,
                asset_class=intent.asset_class,
                notional=decision.approved_notional,
                quantity=decision.approved_quantity,
            )
            if not self.journal.claim_order(
                suggestion["run_id"], intent.intent_id, order.client_order_id, order
            ):
                self.journal.update_trade_suggestion(suggestion_id, "executed")
                return {"suggestion_id": suggestion_id, "status": "executed", "duplicate": True}
            try:
                receipt = self.components.broker.submit(order)
            except Exception as error:
                self.journal.update_order(
                    intent.intent_id, "submission_error", {"error": str(error), "order": order}
                )
                raise
            self.journal.event(suggestion["run_id"], "order_submitted", receipt)
            order_state = self._record_order_receipt(
                suggestion["run_id"], intent, order, receipt, suggestion_id
            )
            self.journal.update_trade_suggestion(
                suggestion_id,
                "executed" if order_state["status"] == "filled" else order_state["status"],
                decision={"critic": suggestion["decision"], "risk": decision},
            )
        public_status = "executed" if order_state["status"] == "filled" else order_state["status"]
        return {
            "suggestion_id": suggestion_id,
            **order_state,
            "status": public_status,
            "receipt_status": order_state["status"],
            "receipt": receipt,
        }

    def _handle_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        conversation_id = str(payload["conversation_id"])
        content = str(payload["content"])
        if self.settings.agent_mode != "openrouter":
            answer = "Service status:\n{}".format(json.dumps(jsonable(self.status()), indent=2))
            message_id = self.journal.add_message(conversation_id, "assistant", answer)
            return {"message_id": message_id, "agent": "heuristic-status"}
        if not self._api_budget_available():
            answer = "The configured monthly API budget has been reached. Status endpoints and deterministic scheduling remain available."
            message_id = self.journal.add_message(conversation_id, "assistant", answer)
            return {"message_id": message_id, "agent": "budget-gate"}
        run_id = self.journal.start_run("daemon-chat", self.settings.safe_dict())
        agent = OpenRouterToolAgent(
            self.settings.openrouter_api_key or "",
            self.settings.reviewer_model or "",
            reasoning_effort=self.settings.openrouter_reasoning_effort,
            max_turns=self.settings.agent_max_turns,
            max_tokens_per_turn=self.settings.agent_max_tokens_per_turn,
        )

        def status_tool(arguments: Dict[str, Any]) -> Dict[str, Any]:
            return self.status()

        def suggestions_tool(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
            return self.journal.trade_suggestions(min(50, int(arguments.get("limit", 10))))

        def triggers_tool(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
            return self.journal.price_triggers(min(100, int(arguments.get("limit", 20))))

        def scan_tool(arguments: Dict[str, Any]) -> Dict[str, Any]:
            symbols = [str(item).upper() for item in arguments.get("symbols") or []]
            invalid = set(symbols) - set(self._current_universe())
            if invalid:
                raise ValueError("Symbols outside current universe: {}".format(sorted(invalid)))
            return {"job_id": self.enqueue_scan(symbols or None, cause="chat_agent")}

        def trigger_tool(arguments: Dict[str, Any]) -> Dict[str, Any]:
            trigger_id = self.create_price_trigger(
                str(arguments["symbol"]),
                str(arguments["comparison"]),
                arguments["threshold"],
                source="chat-agent",
                rationale=str(arguments.get("rationale") or ""),
            )
            return {"trigger_id": trigger_id, "status": "active"}

        tools = [
            AgentTool("get_status", "Get current service, queue, market, and run status.", {"type": "object", "properties": {}}, status_tool),
            AgentTool(
                "list_trade_suggestions",
                "List recent queued, reviewed, rejected, or executed trade suggestions.",
                {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
                suggestions_tool,
            ),
            AgentTool(
                "list_price_triggers",
                "List price triggers and their latest states.",
                {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
                triggers_tool,
            ),
            AgentTool(
                "request_scan",
                "Queue a scan. Symbols must already be in the configured universe.",
                {"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}}},
                scan_tool,
            ),
            AgentTool(
                "create_price_trigger",
                "Create a validated price trigger for a configured symbol. This queues research; it never submits an order.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "symbol": {"type": "string"},
                        "comparison": {"type": "string", "enum": ["above", "below"]},
                        "threshold": {"type": "number", "exclusiveMinimum": 0},
                        "rationale": {"type": "string"},
                    },
                    "required": ["symbol", "comparison", "threshold", "rationale"],
                },
                trigger_tool,
            ),
        ]
        history = [
            {"role": item["role"], "content": item["content"]}
            for item in self.journal.conversation(conversation_id, 20)
            if item["message_id"] != payload["message_id"] and item["role"] in {"user", "assistant"}
        ]
        try:
            with self._agent_lock:
                result = agent.run(
                    (
                        "You are the control-plane assistant for an autonomous paper-trading experiment. "
                        "Use tools for current facts. You may queue scans and validated price triggers when "
                        "the user clearly asks, but you cannot approve trades, change risk policy, add symbols, "
                        "install code, or call the broker. Clearly distinguish queued work from completed work."
                    ),
                    content,
                    tools,
                    history=history,
                    operation="control_chat",
                )
                self._capture_usage(run_id, agent)
            message_id = self.journal.add_message(
                conversation_id,
                "assistant",
                result.content,
                metadata={"turns": result.turns, "tools": [item["tool"] for item in result.tool_calls]},
            )
            self.journal.finish_run(run_id, RunStatus.COMPLETE)
            return {"message_id": message_id, "run_id": run_id, "turns": result.turns}
        except Exception as error:
            self._capture_usage(run_id, agent)
            self.journal.finish_run(run_id, RunStatus.FAILED, str(error))
            raise

    def _emit_error(
        self, event_type: str, error: Exception, payload: Optional[Dict[str, Any]] = None
    ) -> None:
        detail = dict(payload or {})
        detail["error"] = "{}: {}".format(type(error).__name__, error)
        self.journal.service_event(event_type, detail["error"], detail, level="error")
