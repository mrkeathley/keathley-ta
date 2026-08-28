"""Deterministic signal generation derived from the original Pine strategy."""

from decimal import Decimal
from typing import List, Optional

from .domain import Bar, Position, Signal, SignalAction, decimal


class TrendPullbackStrategy:
    version = "trend-pullback-atr-v3"

    def __init__(
        self,
        short_window: int = 10,
        long_window: int = 200,
        atr_window: int = 10,
        atr_multiplier: Decimal = Decimal("4"),
        trail_window: int = 63,
    ):
        self.short_window = short_window
        self.long_window = long_window
        self.atr_window = atr_window
        self.atr_multiplier = atr_multiplier
        self.trail_window = trail_window

    @staticmethod
    def _average(values: List[Decimal]) -> Decimal:
        return sum(values, Decimal("0")) / Decimal(len(values))

    def _atr(self, bars: List[Bar]) -> Decimal:
        ranges: List[Decimal] = []
        for index in range(1, len(bars)):
            current = bars[index]
            previous_close = bars[index - 1].close
            ranges.append(
                max(
                    current.high - current.low,
                    abs(current.high - previous_close),
                    abs(current.low - previous_close),
                )
            )
        return self._average(ranges[-self.atr_window :])

    def analyze(self, bars: List[Bar], position: Optional[Position]) -> Optional[Signal]:
        required = max(self.long_window, self.atr_window + 1, self.trail_window)
        if len(bars) < required:
            return None
        ordered = sorted(bars, key=lambda bar: bar.timestamp)
        close = ordered[-1].close
        short_average = self._average([bar.close for bar in ordered[-self.short_window :]])
        long_average = self._average([bar.close for bar in ordered[-self.long_window :]])
        atr = self._atr(ordered)
        if atr <= 0:
            return None
        highest_close = max(bar.close for bar in ordered[-self.trail_window :])
        stop = highest_close - (atr * self.atr_multiplier)
        if position and position.quantity > 0:
            if close < stop:
                return Signal(
                    symbol=ordered[-1].symbol,
                    action=SignalAction.EXIT,
                    as_of=ordered[-1].timestamp,
                    close=close,
                    short_average=short_average,
                    long_average=long_average,
                    atr=atr,
                    stop_price=stop,
                    confidence=Decimal("1"),
                    strategy_version=self.version,
                    explanation="Close fell below the rolling ATR trailing stop.",
                )
            return None
        if close > long_average and close < short_average:
            trend_strength = (close - long_average) / long_average
            confidence = min(Decimal("0.85"), Decimal("0.60") + max(Decimal("0"), trend_strength))
            return Signal(
                symbol=ordered[-1].symbol,
                action=SignalAction.ENTER,
                as_of=ordered[-1].timestamp,
                close=close,
                short_average=short_average,
                long_average=long_average,
                atr=atr,
                stop_price=stop,
                confidence=decimal(confidence.quantize(Decimal("0.0001"))),
                strategy_version=self.version,
                explanation="Price is above the long-term regime filter and below the short-term mean.",
            )
        return None

