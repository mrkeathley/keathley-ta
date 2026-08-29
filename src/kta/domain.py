"""Dependency-free domain types shared by every adapter.

Money and quantity values use Decimal. Agent output is never passed directly to a
broker: it is parsed into these types and then evaluated by the risk engine.
"""

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import Any, Dict, List, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


class AssetClass(str, Enum):
    EQUITY = "equity"
    ETF = "etf"
    OPTION = "option"


@dataclass(frozen=True)
class OptionContract:
    """Option identity is modeled now; broker execution remains capability-gated."""

    symbol: str
    underlying_symbol: str
    expiration: str
    strike: Decimal
    option_type: str
    multiplier: int = 100


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class SignalAction(str, Enum):
    ENTER = "enter"
    EXIT = "exit"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: Decimal
    market_value: Decimal
    average_entry_price: Decimal
    asset_class: AssetClass = AssetClass.EQUITY


@dataclass(frozen=True)
class AccountSnapshot:
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    last_equity: Decimal
    as_of: datetime


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: SignalAction
    as_of: datetime
    close: Decimal
    short_average: Decimal
    long_average: Decimal
    atr: Decimal
    stop_price: Optional[Decimal]
    confidence: Decimal
    strategy_version: str
    explanation: str


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    symbol: str
    source: str
    title: str
    summary: str
    url: str
    published_at: Optional[datetime]
    retrieved_at: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TradeIntent:
    intent_id: str
    symbol: str
    asset_class: AssetClass
    side: Side
    signal_as_of: datetime
    strategy_version: str
    thesis: str
    confidence: Decimal
    requested_notional: Optional[Decimal] = None
    requested_quantity: Optional[Decimal] = None
    entry_reference_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    evidence_ids: List[str] = field(default_factory=list)
    generated_by: str = "unknown"

    @classmethod
    def create(
        cls,
        symbol: str,
        asset_class: AssetClass,
        side: Side,
        signal_as_of: datetime,
        strategy_version: str,
        thesis: str,
        confidence: Decimal,
        requested_notional: Optional[Decimal] = None,
        requested_quantity: Optional[Decimal] = None,
        entry_reference_price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
        evidence_ids: Optional[List[str]] = None,
        generated_by: str = "unknown",
    ) -> "TradeIntent":
        normalized_symbol = symbol.strip().upper()
        identity = "|".join(
            [
                normalized_symbol,
                asset_class.value,
                side.value,
                signal_as_of.astimezone(timezone.utc).date().isoformat(),
                strategy_version,
            ]
        )
        return cls(
            intent_id=sha256(identity.encode("utf-8")).hexdigest()[:32],
            symbol=normalized_symbol,
            asset_class=asset_class,
            side=side,
            signal_as_of=signal_as_of,
            strategy_version=strategy_version,
            thesis=thesis,
            confidence=decimal(confidence),
            requested_notional=(decimal(requested_notional) if requested_notional is not None else None),
            requested_quantity=(decimal(requested_quantity) if requested_quantity is not None else None),
            entry_reference_price=(
                decimal(entry_reference_price) if entry_reference_price is not None else None
            ),
            stop_price=(decimal(stop_price) if stop_price is not None else None),
            evidence_ids=list(evidence_ids or []),
            generated_by=generated_by,
        )


@dataclass(frozen=True)
class CriticDecision:
    intent_id: str
    approved: bool
    reason: str


@dataclass(frozen=True)
class RiskDecision:
    intent_id: str
    approved: bool
    reason: str
    approved_notional: Optional[Decimal] = None
    approved_quantity: Optional[Decimal] = None


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    intent_id: str
    symbol: str
    side: Side
    asset_class: AssetClass
    notional: Optional[Decimal]
    quantity: Optional[Decimal]
    order_type: str = "market"
    time_in_force: str = "day"


@dataclass(frozen=True)
class OrderReceipt:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: Side
    status: str
    submitted_at: datetime
    filled_quantity: Optional[Decimal] = None
    filled_average_price: Optional[Decimal] = None
    filled_at: Optional[datetime] = None


@dataclass(frozen=True)
class ReviewReport:
    summary: str
    observations: List[str]
    hypotheses: List[str]
    suggested_changes: List[Dict[str, Any]]
    reviewer: str


@dataclass(frozen=True)
class ApiUsage:
    service: str
    operation: str
    model: str
    input_tokens: int
    output_tokens: int
    request_count: int
    cost_usd: Optional[Decimal]
    estimated: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    signal_count: int
    proposed_count: int
    approved_count: int
    submitted_count: int
    review: Optional[ReviewReport]
    api_cost_usd: Decimal = Decimal("0")
    errors: List[str] = field(default_factory=list)
