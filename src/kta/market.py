"""Point-in-time daily market data providers."""

import math
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List

from .domain import Bar, decimal, utc_now
from .http import request_json


def completed_daily_bars(bars: List[Bar], as_of: datetime) -> List[Bar]:
    """Exclude the current session until its regular daily bar is final."""

    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    current = as_of.astimezone(eastern)
    current_date = current.date()
    regular_session_complete = (current.hour, current.minute) >= (16, 15)
    result = []
    for bar in sorted(bars, key=lambda item: item.timestamp):
        bar_date = bar.timestamp.astimezone(eastern).date()
        if bar_date < current_date or (bar_date == current_date and regular_session_complete):
            result.append(bar)
    return result


def parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class MarketDataProvider(ABC):
    @abstractmethod
    def daily_bars(self, symbol: str, lookback: int) -> List[Bar]:
        raise NotImplementedError

    def latest_price(self, symbol: str) -> Decimal:
        bars = self.daily_bars(symbol, 1)
        if not bars:
            raise RuntimeError("No market price is available for {}".format(symbol))
        return bars[-1].close


class AlpacaMarketData(MarketDataProvider):
    def __init__(self, base_url: str, api_key: str, api_secret: str, feed: str = "iex"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }
        self.feed = feed

    def daily_bars(self, symbol: str, lookback: int) -> List[Bar]:
        # Calendar padding ensures enough trading sessions for the 200-day regime filter.
        start = (utc_now() - timedelta(days=max(lookback * 2, 500))).date().isoformat()
        payload = request_json(
            "GET",
            "{}/v2/stocks/{}/bars".format(self.base_url, symbol.upper()),
            headers=self.headers,
            query={
                "timeframe": "1Day",
                "start": start,
                "limit": min(max(lookback, 1), 10000),
                "adjustment": "all",
                "feed": self.feed,
                "sort": "desc",
            },
        )
        bars = [
            Bar(
                symbol=symbol.upper(),
                timestamp=parse_timestamp(item["t"]),
                open=decimal(item["o"]),
                high=decimal(item["h"]),
                low=decimal(item["l"]),
                close=decimal(item["c"]),
                volume=decimal(item.get("v", 0)),
            )
            for item in payload.get("bars", [])
        ]
        return sorted(bars, key=lambda item: item.timestamp)[-lookback:]

    def latest_price(self, symbol: str) -> Decimal:
        payload = request_json(
            "GET",
            "{}/v2/stocks/{}/trades/latest".format(self.base_url, symbol.upper()),
            headers=self.headers,
            query={"feed": self.feed},
        )
        trade = payload.get("trade") or {}
        if trade.get("p") is None:
            raise RuntimeError("Alpaca returned no latest trade for {}".format(symbol))
        return decimal(trade["p"])


class SyntheticMarketData(MarketDataProvider):
    """Deterministic feed for dry runs; the final bars form an uptrend pullback."""

    def __init__(self, now: datetime = None):
        self.now = now or utc_now()

    def daily_bars(self, symbol: str, lookback: int) -> List[Bar]:
        count = max(lookback, 240)
        symbol_bias = (sum(ord(char) for char in symbol.upper()) % 17) / 10.0
        closes: List[float] = []
        for index in range(count):
            close = 60.0 + symbol_bias + (index * 0.24) + math.sin(index / 7.0) * 1.1
            closes.append(close)
        # A controlled pullback leaves price well above the 200-day mean but below the 10-day mean.
        for offset, drop in enumerate([0.8, 1.8, 3.0, 4.2, 5.4], start=1):
            closes[-6 + offset] -= drop
        bars: List[Bar] = []
        first_day = self.now - timedelta(days=count - 1)
        previous = closes[0]
        for index, close in enumerate(closes):
            timestamp = (first_day + timedelta(days=index)).replace(
                hour=21, minute=0, second=0, microsecond=0
            )
            high = max(previous, close) + 1.0
            low = min(previous, close) - 1.0
            bars.append(
                Bar(
                    symbol=symbol.upper(),
                    timestamp=timestamp,
                    open=decimal(round(previous, 4)),
                    high=decimal(round(high, 4)),
                    low=decimal(round(low, 4)),
                    close=decimal(round(close, 4)),
                    volume=Decimal("1000000"),
                )
            )
            previous = close
        return bars[-lookback:]


class StaticMarketData(MarketDataProvider):
    def __init__(self, bars_by_symbol: Dict[str, List[Bar]]):
        self.bars_by_symbol = {key.upper(): list(value) for key, value in bars_by_symbol.items()}

    def daily_bars(self, symbol: str, lookback: int) -> List[Bar]:
        return self.bars_by_symbol.get(symbol.upper(), [])[-lookback:]
