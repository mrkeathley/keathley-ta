"""Market-session clocks used to gate queued execution."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .domain import utc_now
from .http import request_json
from .market import parse_timestamp


@dataclass(frozen=True)
class MarketSession:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime
    source: str


class MarketClock(ABC):
    @abstractmethod
    def session(self) -> MarketSession:
        raise NotImplementedError


class AlpacaMarketClock(MarketClock):
    def __init__(self, trading_url: str, api_key: str, api_secret: str):
        self.url = trading_url.rstrip("/") + "/v2/clock"
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }

    def session(self) -> MarketSession:
        payload = request_json("GET", self.url, headers=self.headers)
        return MarketSession(
            timestamp=parse_timestamp(payload["timestamp"]),
            is_open=bool(payload["is_open"]),
            next_open=parse_timestamp(payload["next_open"]),
            next_close=parse_timestamp(payload["next_close"]),
            source="alpaca",
        )


class WeekdayMarketClock(MarketClock):
    """Dependency-free simulation clock; it intentionally does not model exchange holidays."""

    def __init__(self, now=None):
        self.now = now or utc_now
        self.eastern = ZoneInfo("America/New_York")

    def _next_session_time(self, current: datetime, target: time, include_today: bool) -> datetime:
        candidate = datetime.combine(current.date(), target, self.eastern)
        if not include_today or candidate <= current or candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    def session(self) -> MarketSession:
        current = self.now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        eastern_now = current.astimezone(self.eastern)
        weekday = eastern_now.weekday() < 5
        opened = time(9, 30) <= eastern_now.time() < time(16, 0)
        is_open = weekday and opened
        next_open = self._next_session_time(
            eastern_now,
            time(9, 30),
            include_today=weekday and eastern_now.time() < time(9, 30),
        )
        if is_open:
            next_close = datetime.combine(eastern_now.date(), time(16, 0), self.eastern).astimezone(
                timezone.utc
            )
        else:
            next_close = (next_open.astimezone(self.eastern) + timedelta(hours=6, minutes=30)).astimezone(
                timezone.utc
            )
        return MarketSession(
            timestamp=current.astimezone(timezone.utc),
            is_open=is_open,
            next_open=next_open,
            next_close=next_close,
            source="weekday-simulation",
        )
