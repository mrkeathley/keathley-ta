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
    CriticDecision,
    OrderRequest,
    RunStatus,
    Side,
    jsonable,
    utc_now,
)
from .runtime import RuntimeComponents, build_components, build_loop
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
        self._scan_started = False
        self._strategy = TrendPullbackStrategy()

    def start_background_loops(self) -> None:
        if self._threads:
            return
        self.journal.service_event("daemon_started", "Daemon background loops started")
        logger.info("daemon background loops started")
        scheduler = threading.Thread(target=self._scheduler_loop, name="kta-scheduler", daemon=True)
        trigger = threading.Thread(target=self._trigger_loop, name="kta-triggers", daemon=True)
        self._threads.extend([scheduler, trigger])
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
        selected = [item.strip().upper() for item in (symbols or self.settings.universe) if item.strip()]
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
        if normalized not in set(self.settings.universe):
            raise ValueError("Price triggers are limited to the configured universe")
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
                firing_id = uuid.uuid4().hex
                self.enqueue_scan(
                    [normalized],
                    cause="price_trigger:{}:{}:{}".format(
                        trigger["trigger_id"], source, firing_id
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
        return self.journal.api_cost_since(month_start) < self.settings.monthly_api_budget_usd

    def _suggestion_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in self.journal.trade_suggestions(1000):
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return counts

    def _scheduler_loop(self) -> None:
        eastern = ZoneInfo("America/New_York")
        while not self.stop_event.wait(1):
            now = utc_now().astimezone(eastern)
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
                        firing_id = uuid.uuid4().hex
                        self.enqueue_scan(
                            [trigger["symbol"]],
                            cause="price_trigger:{}:{}".format(
                                trigger["trigger_id"], firing_id
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
        if kind == "review_suggestion":
            return self._handle_review_suggestion(job["payload"])
        if kind == "execute_suggestion":
            return self._handle_execute_suggestion(job["payload"])
        if kind == "chat":
            return self._handle_chat(job["payload"])
        raise ValueError("Unknown job kind {}".format(kind))

    def _handle_scan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        progress_events: List[str] = []

        def progress(message: str) -> None:
            progress_events.append(message)
            self.journal.service_event("scan_progress", message, {"cause": payload.get("cause")})
            logger.info("scan progress cause=%s stage=%s", payload.get("cause"), message)

        with self._agent_lock, self._execution_lock:
            result = build_loop(
                self.settings,
                progress=progress,
                defer_execution=True,
                components=self.components,
            ).run(
                [str(item) for item in payload["symbols"]],
                trigger_context=(
                    str(payload.get("cause"))
                    if str(payload.get("cause") or "").startswith("price_trigger:")
                    else None
                ),
            )
        if result.status != RunStatus.COMPLETE:
            raise RuntimeError("Scan run {} failed: {}".format(result.run_id, "; ".join(result.errors)))
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
                bars = self.components.market.daily_bars(intent.symbol, 260)
                fresh_signal = self._strategy.analyze(bars, held)
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
            self.journal.update_order(intent.intent_id, receipt.status, receipt)
            self.journal.event(suggestion["run_id"], "order_submitted", receipt)
            self.journal.update_trade_suggestion(
                suggestion_id, "executed", decision={"critic": suggestion["decision"], "risk": decision}
            )
        return {"suggestion_id": suggestion_id, "status": "executed", "receipt": receipt}

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
        )

        def status_tool(arguments: Dict[str, Any]) -> Dict[str, Any]:
            return self.status()

        def suggestions_tool(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
            return self.journal.trade_suggestions(min(50, int(arguments.get("limit", 10))))

        def triggers_tool(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
            return self.journal.price_triggers(min(100, int(arguments.get("limit", 20))))

        def scan_tool(arguments: Dict[str, Any]) -> Dict[str, Any]:
            symbols = [str(item).upper() for item in arguments.get("symbols") or []]
            invalid = set(symbols) - set(self.settings.universe)
            if invalid:
                raise ValueError("Symbols outside configured universe: {}".format(sorted(invalid)))
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
