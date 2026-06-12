from __future__ import annotations

from src.strategy.indicators import atr, candles_to_frame, ema, rsi
from src.utils.config import TradingConfig
from src.utils.types import Candle, MarketSnapshot, Signal


class TrendFollowingStrategy:
    def __init__(self, config: TradingConfig):
        self.config = config

    def generate_signal(
        self,
        symbol: str,
        signal_candles: list[Candle],
        entry_candles: list[Candle],
        snapshot: MarketSnapshot,
    ) -> Signal | None:
        if symbol not in self.config.allowed_symbols:
            return None
        if len(signal_candles) < 70 or len(entry_candles) < 70:
            return None
        if snapshot.spread_pct > self.config.spread_limit:
            return None

        signal_frame = candles_to_frame(signal_candles)
        entry_frame = candles_to_frame(entry_candles)
        signal_frame["ema20"] = ema(signal_frame["close"], 20)
        signal_frame["ema60"] = ema(signal_frame["close"], 60)
        entry_frame["ema20"] = ema(entry_frame["close"], 20)
        entry_frame["rsi"] = rsi(entry_frame["close"])
        entry_frame["atr"] = atr(entry_frame)
        entry_frame["volume_avg20"] = entry_frame["volume"].rolling(20).mean()
        entry_frame["atr_avg20"] = entry_frame["atr"].rolling(20).mean()

        last_signal = signal_frame.iloc[-1]
        prev_entry = entry_frame.iloc[-2]
        last_entry = entry_frame.iloc[-1]

        current_atr = float(last_entry["atr"])
        atr_avg20 = float(last_entry["atr_avg20"])
        if current_atr <= 0 or atr_avg20 <= 0:
            return None
        if current_atr > atr_avg20 * self.config.atr_anomaly_multiplier:
            return None
        if float(last_entry["volume"]) <= float(last_entry["volume_avg20"]):
            return None

        risk_distance = self.config.stop_loss_atr_multiplier * current_atr
        rr = self.config.take_profit_rr

        trend_up = float(last_signal["ema20"]) > float(last_signal["ema60"])
        crossed_up = (
            float(prev_entry["close"]) <= float(prev_entry["ema20"])
            and float(last_entry["close"]) > float(last_entry["ema20"])
        )
        rsi_long_ok = 50 <= float(last_entry["rsi"]) <= 70
        if trend_up and crossed_up and rsi_long_ok:
            entry = float(snapshot.last_price or last_entry["close"])
            return Signal(
                symbol=symbol,
                direction="long",
                entry_price=entry,
                stop_loss=entry - risk_distance,
                take_profit=entry + risk_distance * rr,
                atr=current_atr,
                reason="15m EMA20>EMA60, 5m close crossed above EMA20, RSI/volume/spread/ATR ok",
            )

        if self.config.enable_short and self.config.enable_futures:
            trend_down = float(last_signal["ema20"]) < float(last_signal["ema60"])
            crossed_down = (
                float(prev_entry["close"]) >= float(prev_entry["ema20"])
                and float(last_entry["close"]) < float(last_entry["ema20"])
            )
            rsi_short_ok = 30 <= float(last_entry["rsi"]) <= 50
            if trend_down and crossed_down and rsi_short_ok:
                entry = float(snapshot.last_price or last_entry["close"])
                return Signal(
                    symbol=symbol,
                    direction="short",
                    entry_price=entry,
                    stop_loss=entry + risk_distance,
                    take_profit=entry - risk_distance * rr,
                    atr=current_atr,
                    reason="15m EMA20<EMA60, 5m close crossed below EMA20, RSI/volume/spread/ATR ok",
                )

        return None

