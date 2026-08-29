"""The auditable trading run orchestration."""

from datetime import timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Callable, Dict, List, Optional

from .agents import AgentSuite
from .brokers import Broker
from .domain import (
    AssetClass,
    OrderRequest,
    RunResult,
    RunStatus,
    SignalAction,
    utc_now,
)
from .journal import Journal
from .market import MarketDataProvider
from .market import completed_daily_bars
from .research import ResearchProvider
from .risk import RiskEngine
from .strategy import TrendPullbackStrategy


class TradingLoop:
    def __init__(
        self,
        market_data: MarketDataProvider,
        research: ResearchProvider,
        agents: AgentSuite,
        risk: RiskEngine,
        broker: Broker,
        journal: Journal,
        mode: str,
        safe_config: Dict[str, object],
        strategy: Optional[TrendPullbackStrategy] = None,
        decision_cooldown_hours: int = 168,
        review_min_interval_hours: int = 24,
        monthly_api_budget_usd: Decimal = Decimal("2.00"),
        enforce_monthly_api_budget: bool = True,
        paid_api_enabled: bool = False,
        defer_execution: bool = False,
        progress: Optional[Callable[[str], None]] = None,
        clock=None,
    ):
        self.market_data = market_data
        self.research = research
        self.agents = agents
        self.risk = risk
        self.broker = broker
        self.journal = journal
        self.mode = mode
        self.safe_config = safe_config
        self.strategy = strategy or TrendPullbackStrategy()
        self.decision_cooldown_hours = decision_cooldown_hours
        self.review_min_interval_hours = review_min_interval_hours
        self.monthly_api_budget_usd = monthly_api_budget_usd
        self.enforce_monthly_api_budget = enforce_monthly_api_budget
        self.paid_api_enabled = paid_api_enabled
        self.defer_execution = defer_execution
        self.progress = progress or (lambda message: None)
        self.clock = clock or utc_now

    def _status(self, message: str) -> None:
        # Display failures must never change trading behavior or the audit record.
        try:
            self.progress(message)
        except Exception:
            pass

    def _capture_usage(self, run_id: str, provider) -> Decimal:
        total = Decimal("0")
        for usage in provider.drain_usage():
            self.journal.record_api_usage(run_id, usage)
            self.journal.event(run_id, "api_usage", usage)
            if usage.cost_usd is not None:
                total += usage.cost_usd
        return total

    def run(self, universe: List[str], trigger_context: Optional[str] = None) -> RunResult:
        run_id = self.journal.start_run(self.mode, self.safe_config)
        signal_count = 0
        proposed_count = 0
        approved_count = 0
        submitted_count = 0
        review = None
        api_cost_usd = Decimal("0")
        pending_trigger_keys = set()
        errors: List[str] = []
        try:
            self._status("Loading account and positions")
            evaluation_time = self.clock()
            if evaluation_time.tzinfo is None:
                evaluation_time = evaluation_time.replace(tzinfo=timezone.utc)
            account = self.broker.account()
            positions = self.broker.positions()
            if any(position.quantity < 0 for position in positions):
                raise RuntimeError("A short position exists, but this release supports long-only portfolios")
            if any(position.asset_class == AssetClass.OPTION for position in positions):
                raise RuntimeError("An option position exists, but this release cannot manage its lifecycle")
            positions_by_symbol = {position.symbol: position for position in positions}
            self.journal.event(run_id, "account_snapshot", account)
            self.journal.event(run_id, "positions_snapshot", positions)

            signals = []
            requested_symbols = [item.strip().upper() for item in universe if item.strip()]
            # Held positions are always scanned even if a symbol was removed from the candidate universe.
            scan_symbols = dict.fromkeys(requested_symbols + [position.symbol for position in positions])
            symbol_count = len(scan_symbols)
            for index, symbol in enumerate(scan_symbols, start=1):
                self._status("Scanning market data {}/{}: {}".format(index, symbol_count, symbol))
                bars = completed_daily_bars(
                    self.market_data.daily_bars(symbol, 261), evaluation_time
                )[-260:]
                if not bars:
                    self.journal.event(run_id, "market_data_missing", {"symbol": symbol})
                    if symbol in positions_by_symbol:
                        raise RuntimeError("Market data is missing for held position {}".format(symbol))
                    continue
                data_age = evaluation_time.astimezone(timezone.utc) - bars[-1].timestamp.astimezone(timezone.utc)
                if data_age > timedelta(days=self.risk.policy.max_data_age_days) or data_age < timedelta(days=-1):
                    self.journal.event(
                        run_id,
                        "market_data_stale",
                        {"symbol": symbol, "last_timestamp": bars[-1].timestamp, "age": str(data_age)},
                    )
                    if symbol in positions_by_symbol:
                        raise RuntimeError("Market data is stale for held position {}".format(symbol))
                    continue
                if hasattr(self.broker, "set_price"):
                    self.broker.set_price(symbol, bars[-1].close)
                self.journal.event(
                    run_id,
                    "market_snapshot",
                    {
                        "symbol": symbol,
                        "bar_count": len(bars),
                        "first_timestamp": bars[0].timestamp,
                        "last_bar": bars[-1],
                    },
                )
                position = positions_by_symbol.get(symbol)
                if isinstance(self.strategy, TrendPullbackStrategy):
                    trail = self.journal.update_position_trail(
                        symbol, bars[-1].high, bool(position and position.quantity > 0)
                    )
                    signal = self.strategy.analyze(bars, position, trail)
                    if position and position.quantity > 0:
                        trailing_stop = self.strategy.trailing_stop(bars, trail)
                        if trailing_stop is not None and trailing_stop > 0:
                            trigger_id = self.journal.upsert_protective_stop(
                                symbol,
                                trailing_stop,
                                {
                                    "kind": "protective_stop",
                                    "run_id": run_id,
                                    "strategy_version": self.strategy.version,
                                    "last_completed_bar": bars[-1].timestamp,
                                },
                            )
                            self.journal.event(
                                run_id,
                                "protective_stop_ratcheted",
                                {
                                    "trigger_id": trigger_id,
                                    "symbol": symbol,
                                    "stop_price": trailing_stop,
                                },
                            )
                else:
                    signal = self.strategy.analyze(bars, position)
                if signal:
                    signals.append(signal)
                    self.journal.event(run_id, "signal", signal)
                else:
                    self.journal.event(
                        run_id,
                        "no_signal",
                        {"symbol": symbol, "strategy_version": self.strategy.version},
                    )
            signal_count = len(signals)
            self._status("Found {} signal(s); applying cost and cooldown gates".format(signal_count))

            month_start = evaluation_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            month_cost_before_run = self.journal.api_cost_since(month_start)
            budget_blocked = (
                self.enforce_monthly_api_budget
                and self.paid_api_enabled
                and month_cost_before_run >= self.monthly_api_budget_usd
            )
            if budget_blocked:
                entry_count = sum(1 for signal in signals if signal.action == SignalAction.ENTER)
                signals = [signal for signal in signals if signal.action == SignalAction.EXIT]
                self.journal.event(
                    run_id,
                    "api_budget_blocked",
                    {
                        "month_cost_usd": month_cost_before_run,
                        "monthly_budget_usd": self.monthly_api_budget_usd,
                        "entry_signals_suppressed": entry_count,
                        "protective_exits_preserved": len(signals),
                    },
                )

            triggered_signals = []
            for signal in signals:
                if signal.action == SignalAction.EXIT:
                    triggered_signals.append(signal)
                    continue
                trigger_identity = "{}|{}|{}".format(
                    signal.symbol, signal.strategy_version, signal.action.value
                )
                if trigger_context:
                    trigger_identity += "|{}".format(trigger_context)
                trigger_key = sha256(trigger_identity.encode("utf-8")).hexdigest()[:32]
                if self.journal.claim_decision_trigger(
                    run_id,
                    trigger_key,
                    signal.symbol,
                    signal.strategy_version,
                    signal.action.value,
                    evaluation_time,
                    self.decision_cooldown_hours,
                ):
                    triggered_signals.append(signal)
                    pending_trigger_keys.add(trigger_key)
                    self.journal.event(
                        run_id,
                        "decision_trigger_claimed",
                        {
                            "trigger_key": trigger_key,
                            "symbol": signal.symbol,
                            "action": signal.action,
                            "context": trigger_context,
                        },
                    )
                else:
                    self.journal.event(
                        run_id,
                        "decision_trigger_suppressed",
                        {
                            "trigger_key": trigger_key,
                            "symbol": signal.symbol,
                            "action": signal.action,
                            "cooldown_hours": self.decision_cooldown_hours,
                        },
                    )
            signals = triggered_signals

            self._status("Researching {} triggered signal(s)".format(len(signals)))
            evidence = self.research.gather(signals)
            api_cost_usd += self._capture_usage(run_id, self.research)
            for item in evidence:
                self.journal.event(run_id, "evidence", item)

            # Unscored narrative reviews must not recursively persuade the scout.
            # Mature outcome marks are reviewed by the daemon evaluator instead.
            recent_reviews: List[Dict[str, object]] = []
            self._status("Scout evaluating {} triggered signal(s)".format(len(signals)))
            intents = self.agents.scout(signals, evidence, account, positions, recent_reviews)
            api_cost_usd += self._capture_usage(run_id, self.agents)
            proposed_count = len(intents)
            for intent in intents:
                self.journal.event(run_id, "intent_proposed", intent)
                matching_signal = next(
                    (signal for signal in signals if signal.symbol == intent.symbol), None
                )
                if matching_signal is not None:
                    self.journal.register_intent_outcomes(run_id, intent, matching_signal.close)

            if self.defer_execution:
                self._status("Queueing {} trade suggestion(s) for independent review".format(len(intents)))
                for intent in intents:
                    suggestion_id = self.journal.create_trade_suggestion(
                        run_id,
                        intent.intent_id,
                        intent.symbol,
                        intent.side.value,
                        {"intent": intent, "signals": signals, "evidence": evidence},
                    )
                    suggestion = self.journal.trade_suggestion(suggestion_id)
                    if suggestion and suggestion["status"] == "executed":
                        self.journal.event(
                            run_id,
                            "trade_suggestion_duplicate_blocked",
                            {"suggestion_id": suggestion_id, "intent_id": intent.intent_id},
                        )
                        continue
                    self.journal.enqueue_job(
                        "review_suggestion",
                        {"suggestion_id": suggestion_id, "revalidate": False},
                        priority=100 if intent.side.value == "sell" else 50,
                        dedupe_key="suggestion-review:{}:initial:{}".format(
                            suggestion_id, run_id
                        ),
                    )
                    self.journal.event(
                        run_id,
                        "trade_suggestion_queued",
                        {"suggestion_id": suggestion_id, "intent_id": intent.intent_id},
                    )
                for trigger_key in list(pending_trigger_keys):
                    self.journal.finish_decision_trigger(trigger_key, evaluation_time, success=True)
                    pending_trigger_keys.remove(trigger_key)
                self._status("Finalizing audit journal")
                self.journal.finish_run(run_id, RunStatus.COMPLETE)
                return RunResult(
                    run_id=run_id,
                    status=RunStatus.COMPLETE,
                    signal_count=signal_count,
                    proposed_count=proposed_count,
                    approved_count=0,
                    submitted_count=0,
                    review=None,
                    api_cost_usd=api_cost_usd,
                    errors=errors,
                )

            self._status("Critic reviewing {} proposed intent(s)".format(len(intents)))
            critic_decisions = self.agents.criticize(intents, signals, evidence)
            api_cost_usd += self._capture_usage(run_id, self.agents)
            for trigger_key in list(pending_trigger_keys):
                self.journal.finish_decision_trigger(trigger_key, evaluation_time, success=True)
                pending_trigger_keys.remove(trigger_key)
            critic_by_id = {decision.intent_id: decision for decision in critic_decisions}
            critic_approved = []
            for intent in intents:
                decision = critic_by_id.get(intent.intent_id)
                if decision and decision.approved:
                    critic_approved.append(intent)
                    self.journal.event(run_id, "critic_approved", decision)
                    self.journal.update_intent_disposition(intent.intent_id, "critic_approved")
                else:
                    payload = decision or {"intent_id": intent.intent_id, "reason": "Missing critic decision"}
                    self.journal.event(run_id, "critic_rejected", payload)
                    self.journal.update_intent_disposition(intent.intent_id, "critic_rejected")

            existing_ids = [intent.intent_id for intent in critic_approved if self.journal.has_order(intent.intent_id)]
            self._status("Applying deterministic risk controls")
            risk_decisions = self.risk.evaluate(
                critic_approved,
                account,
                positions,
                existing_ids,
                now=evaluation_time,
            )
            risk_by_id = {decision.intent_id: decision for decision in risk_decisions}
            approved_count = sum(1 for decision in risk_decisions if decision.approved)
            approved_to_process = sum(1 for decision in risk_decisions if decision.approved)
            order_index = 0
            for intent in critic_approved:
                decision = risk_by_id[intent.intent_id]
                if not decision.approved:
                    self.journal.event(run_id, "risk_rejected", decision)
                    self.journal.update_intent_disposition(intent.intent_id, "risk_rejected")
                    continue
                self.journal.event(run_id, "risk_approved", decision)
                self.journal.update_intent_disposition(intent.intent_id, "risk_approved")
                client_order_id = "kta-{}".format(intent.intent_id)
                order = OrderRequest(
                    client_order_id=client_order_id,
                    intent_id=intent.intent_id,
                    symbol=intent.symbol,
                    side=intent.side,
                    asset_class=intent.asset_class,
                    notional=decision.approved_notional,
                    quantity=decision.approved_quantity,
                )
                if not self.journal.claim_order(run_id, intent.intent_id, client_order_id, order):
                    self.journal.event(run_id, "order_duplicate_blocked", order)
                    continue
                try:
                    order_index += 1
                    self._status(
                        "Submitting order {}/{}: {} {}".format(
                            order_index, approved_to_process, intent.side.value.upper(), intent.symbol
                        )
                    )
                    receipt = self.broker.submit(order)
                except Exception as error:
                    self.journal.update_order(intent.intent_id, "submission_error", {"error": str(error), "order": order})
                    self.journal.event(run_id, "order_submission_error", {"error": str(error), "order": order})
                    raise
                self.journal.update_order(intent.intent_id, receipt.status, receipt)
                self.journal.event(run_id, "order_submitted", receipt)
                self.journal.update_intent_disposition(
                    intent.intent_id,
                    "filled" if receipt.status == "filled" else "submitted",
                )
                submitted_count += 1

            last_review_at = self.journal.last_review_at()
            if (
                self.enforce_monthly_api_budget
                and self.paid_api_enabled
                and month_cost_before_run + api_cost_usd >= self.monthly_api_budget_usd
            ):
                budget_blocked = True
            review_due = last_review_at is None or (
                evaluation_time - last_review_at >= timedelta(hours=self.review_min_interval_hours)
            )
            material_activity = bool(signals or intents)
            if budget_blocked:
                review_skip_reason = "monthly_api_budget_reached"
            elif not material_activity:
                review_skip_reason = "no_actionable_activity"
            elif not review_due:
                review_skip_reason = "review_interval_not_elapsed"
            else:
                review_skip_reason = None
            if review_skip_reason:
                self._status("Learning review skipped: {}".format(review_skip_reason))
                self.journal.event(
                    run_id,
                    "learning_review_skipped",
                    {
                        "reason": review_skip_reason,
                        "minimum_interval_hours": self.review_min_interval_hours,
                        "last_review_at": last_review_at,
                    },
                )
            else:
                self._status("Learning reviewer analyzing material activity")
                review_events = self.journal.material_events_since(last_review_at, limit=250)
                try:
                    review = self.agents.review(review_events, recent_reviews)
                    api_cost_usd += self._capture_usage(run_id, self.agents)
                    self.journal.save_review(run_id, review)
                    experiment_ids = self.journal.propose_learning_experiments(
                        run_id, review.suggested_changes
                    )
                    self.journal.event(run_id, "learning_review", review)
                    self.journal.event(
                        run_id,
                        "learning_experiments_proposed",
                        {"experiment_ids": experiment_ids},
                    )
                except Exception as error:
                    api_cost_usd += self._capture_usage(run_id, self.agents)
                    warning = "Learning review deferred after {}: {}".format(
                        type(error).__name__, error
                    )
                    errors.append(warning)
                    self.journal.event(run_id, "learning_review_error", {"error": warning})
            self._status("Finalizing audit journal")
            self.journal.finish_run(run_id, RunStatus.COMPLETE)
            return RunResult(
                run_id=run_id,
                status=RunStatus.COMPLETE,
                signal_count=signal_count,
                proposed_count=proposed_count,
                approved_count=approved_count,
                submitted_count=submitted_count,
                review=review,
                api_cost_usd=api_cost_usd,
                errors=errors,
            )
        except Exception as error:
            for trigger_key in list(pending_trigger_keys):
                try:
                    self.journal.finish_decision_trigger(trigger_key, self.clock(), success=False)
                except Exception:
                    pass
            try:
                api_cost_usd += self._capture_usage(run_id, self.research)
                api_cost_usd += self._capture_usage(run_id, self.agents)
            except Exception:
                pass
            message = "{}: {}".format(type(error).__name__, error)
            errors.append(message)
            try:
                self.journal.event(run_id, "run_error", {"error": message})
                self.journal.finish_run(run_id, RunStatus.FAILED, message)
            except Exception:
                pass
            return RunResult(
                run_id=run_id,
                status=RunStatus.FAILED,
                signal_count=signal_count,
                proposed_count=proposed_count,
                approved_count=approved_count,
                submitted_count=submitted_count,
                review=review,
                api_cost_usd=api_cost_usd,
                errors=errors,
            )
