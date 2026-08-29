"""Strict reconstruction of journaled domain objects used by durable workers."""

from datetime import datetime
from typing import Any, Dict

from .domain import AssetClass, Evidence, Side, Signal, SignalAction, TradeIntent, decimal


def _datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Expected an ISO datetime string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def signal_from_dict(value: Dict[str, Any]) -> Signal:
    return Signal(
        symbol=str(value["symbol"]).upper(),
        action=SignalAction(str(value["action"])),
        as_of=_datetime(value["as_of"]),
        close=decimal(value["close"]),
        short_average=decimal(value["short_average"]),
        long_average=decimal(value["long_average"]),
        atr=decimal(value["atr"]),
        stop_price=decimal(value["stop_price"]) if value.get("stop_price") is not None else None,
        confidence=decimal(value["confidence"]),
        strategy_version=str(value["strategy_version"]),
        explanation=str(value["explanation"]),
    )


def evidence_from_dict(value: Dict[str, Any]) -> Evidence:
    return Evidence(
        evidence_id=str(value["evidence_id"]),
        symbol=str(value["symbol"]),
        source=str(value["source"]),
        title=str(value["title"]),
        summary=str(value["summary"]),
        url=str(value["url"]),
        published_at=_datetime(value["published_at"]) if value.get("published_at") else None,
        retrieved_at=_datetime(value["retrieved_at"]),
        metadata=dict(value.get("metadata") or {}),
    )


def intent_from_dict(value: Dict[str, Any]) -> TradeIntent:
    return TradeIntent(
        intent_id=str(value["intent_id"]),
        symbol=str(value["symbol"]).upper(),
        asset_class=AssetClass(str(value["asset_class"])),
        side=Side(str(value["side"])),
        signal_as_of=_datetime(value["signal_as_of"]),
        strategy_version=str(value["strategy_version"]),
        thesis=str(value["thesis"]),
        confidence=decimal(value["confidence"]),
        requested_notional=(
            decimal(value["requested_notional"]) if value.get("requested_notional") is not None else None
        ),
        requested_quantity=(
            decimal(value["requested_quantity"]) if value.get("requested_quantity") is not None else None
        ),
        stop_price=decimal(value["stop_price"]) if value.get("stop_price") is not None else None,
        evidence_ids=[str(item) for item in value.get("evidence_ids") or []],
        generated_by=str(value.get("generated_by") or "unknown"),
    )
