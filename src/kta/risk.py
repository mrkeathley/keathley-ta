"""Deterministic market and operational risk gate."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List

from .domain import AccountSnapshot, AssetClass, Position, RiskDecision, Side, TradeIntent


@dataclass(frozen=True)
class RiskPolicy:
    max_position_pct: Decimal
    max_gross_exposure_pct: Decimal
    max_new_exposure_per_run_pct: Decimal
    max_orders_per_run: int
    max_data_age_days: int
    minimum_confidence: Decimal
    minimum_order_notional: Decimal
    daily_loss_kill_pct: Decimal
    risk_per_trade_pct: Decimal = Decimal("0.01")
    options_enabled: bool = False
    shorting_enabled: bool = False


class RiskEngine:
    def __init__(self, policy: RiskPolicy):
        self.policy = policy

    def evaluate(
        self,
        intents: List[TradeIntent],
        account: AccountSnapshot,
        positions: List[Position],
        existing_intent_ids: List[str],
        now: datetime,
        prior_run_new_exposure: Decimal = Decimal("0"),
        prior_run_order_count: int = 0,
    ) -> List[RiskDecision]:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        positions_by_symbol: Dict[str, Position] = {position.symbol: position for position in positions}
        gross = sum((abs(position.market_value) for position in positions), Decimal("0"))
        reserved = Decimal("0")
        approved_orders = prior_run_order_count
        existing = set(existing_intent_ids)
        decisions: List[RiskDecision] = []
        for intent in sorted(intents, key=lambda item: (item.side != Side.SELL, -item.confidence)):
            if intent.intent_id in existing:
                decisions.append(RiskDecision(intent.intent_id, False, "Duplicate intent already has a ledger entry."))
                continue
            if approved_orders >= self.policy.max_orders_per_run:
                decisions.append(RiskDecision(intent.intent_id, False, "Maximum orders per run reached."))
                continue
            age = now.astimezone(timezone.utc) - intent.signal_as_of.astimezone(timezone.utc)
            if age > timedelta(days=self.policy.max_data_age_days) or age < timedelta(days=-1):
                decisions.append(RiskDecision(intent.intent_id, False, "Signal timestamp is stale or in the future."))
                continue
            if intent.asset_class == AssetClass.OPTION and not self.policy.options_enabled:
                decisions.append(RiskDecision(intent.intent_id, False, "Options execution is disabled for this phase."))
                continue
            if intent.side == Side.SELL:
                position = positions_by_symbol.get(intent.symbol)
                if not position or position.quantity <= 0:
                    decisions.append(RiskDecision(intent.intent_id, False, "A sell would create an unapproved short."))
                    continue
                requested = intent.requested_quantity or position.quantity
                quantity = min(requested, position.quantity)
                decisions.append(
                    RiskDecision(intent.intent_id, True, "Protective exit capped to the held quantity.", approved_quantity=quantity)
                )
                approved_orders += 1
                continue
            if intent.side != Side.BUY:
                decisions.append(RiskDecision(intent.intent_id, False, "Unsupported side."))
                continue
            if intent.confidence < self.policy.minimum_confidence:
                decisions.append(RiskDecision(intent.intent_id, False, "Intent confidence is below policy."))
                continue
            if intent.stop_price is None or intent.stop_price <= 0:
                decisions.append(RiskDecision(intent.intent_id, False, "Entry intent has no valid risk reference price."))
                continue
            if account.last_equity > 0:
                drawdown = (account.last_equity - account.equity) / account.last_equity
                if drawdown >= self.policy.daily_loss_kill_pct:
                    decisions.append(RiskDecision(intent.intent_id, False, "Daily equity kill switch is active."))
                    continue
            requested = intent.requested_notional or Decimal("0")
            if (
                intent.entry_reference_price is not None
                and intent.stop_price is not None
                and intent.entry_reference_price > intent.stop_price
            ):
                risk_per_share = intent.entry_reference_price - intent.stop_price
                risk_notional = (
                    account.equity
                    * self.policy.risk_per_trade_pct
                    * intent.entry_reference_price
                    / risk_per_share
                )
                requested = min(requested, risk_notional)
            current_value = abs(positions_by_symbol.get(intent.symbol).market_value) if intent.symbol in positions_by_symbol else Decimal("0")
            symbol_room = (account.equity * self.policy.max_position_pct) - current_value
            gross_room = (account.equity * self.policy.max_gross_exposure_pct) - gross - reserved
            run_room = (
                (account.equity * self.policy.max_new_exposure_per_run_pct)
                - prior_run_new_exposure
                - reserved
            )
            cash_room = account.cash - reserved
            notional = min(requested, symbol_room, gross_room, run_room, cash_room)
            notional = max(Decimal("0"), notional).quantize(Decimal("0.01"))
            if notional < self.policy.minimum_order_notional:
                decisions.append(RiskDecision(intent.intent_id, False, "Available risk budget is below the minimum order."))
                continue
            decisions.append(
                RiskDecision(
                    intent.intent_id,
                    True,
                    "Approved and clipped to deterministic concentration, gross, run, and cash limits.",
                    approved_notional=notional,
                )
            )
            reserved += notional
            approved_orders += 1
        by_id = {decision.intent_id: decision for decision in decisions}
        return [by_id[intent.intent_id] for intent in intents]
