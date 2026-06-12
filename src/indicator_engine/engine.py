from __future__ import annotations

import pandas as pd

from src.strategy.indicators import atr, candles_to_frame, ema, rsi
from src.utils.types import Candle, IndicatorSnapshot, MarketSnapshot


class IndicatorEngine:
    def calculate(
        self,
        symbol: str,
        timeframe: str,
        candles: list[Candle],
        snapshot: MarketSnapshot,
    ) -> IndicatorSnapshot:
        if len(candles) < 70:
            raise ValueError(f"not enough candles for {symbol} {timeframe}")
        frame = candles_to_frame(candles)
        close = frame["close"]
        frame["ema20"] = ema(close, 20)
        frame["ema60"] = ema(close, 60)
        frame["rsi"] = rsi(close)
        frame["atr"] = atr(frame)
        frame["volume_avg20"] = frame["volume"].rolling(20).mean()
        macd_line = ema(close, 12) - ema(close, 26)
        macd_signal = ema(macd_line, 9)
        returns = close.pct_change()
        recent = frame.tail(20)
        last = frame.iloc[-1]
        return IndicatorSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            close=float(last["close"]),
            ema20=float(last["ema20"]),
            ema60=float(last["ema60"]),
            rsi=float(last["rsi"]),
            macd=float(macd_line.iloc[-1]),
            macd_signal=float(macd_signal.iloc[-1]),
            macd_histogram=float((macd_line - macd_signal).iloc[-1]),
            atr=float(last["atr"]),
            volume=float(last["volume"]),
            volume_avg20=float(last["volume_avg20"]),
            support=float(recent["low"].min()),
            resistance=float(recent["high"].max()),
            volatility=float(returns.tail(20).std() or 0.0),
            recent_high=float(recent["high"].max()),
            recent_low=float(recent["low"].min()),
            spread_pct=float(snapshot.spread_pct),
        )

    def atr_is_anomalous(self, candles: list[Candle], multiplier: float) -> bool:
        frame = candles_to_frame(candles)
        values = atr(frame)
        avg20 = values.rolling(20).mean().iloc[-1]
        current = values.iloc[-1]
        if pd.isna(avg20) or avg20 <= 0:
            return True
        return bool(current > avg20 * multiplier)

