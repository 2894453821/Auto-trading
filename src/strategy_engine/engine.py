from __future__ import annotations

from src.indicator_engine.engine import IndicatorEngine
from src.market_data.service import MarketDataService
from src.utils.config import TradingConfig
from src.utils.types import CandidateSignal, IndicatorSnapshot, MarketSnapshot


class StrategyEngine:
    def __init__(self, config: TradingConfig, market: MarketDataService):
        self.config = config
        self.market = market
        self.indicators = IndicatorEngine()

    def analyze(self, symbol: str) -> CandidateSignal:
        if symbol not in self.config.allowed_symbols:
            return self._no_trade(symbol, [], [f"{symbol} 不在允许交易列表内"], {})
        snapshot = self.market.snapshot(symbol)
        trend_candles = self.market.candles(symbol, self.config.timeframe_trend, 120)
        signal_candles = self.market.candles(symbol, self.config.timeframe_signal, 120)
        entry_candles = self.market.candles(symbol, self.config.timeframe_entry, 120)
        trend = self.indicators.calculate(symbol, self.config.timeframe_trend, trend_candles, snapshot)
        signal = self.indicators.calculate(symbol, self.config.timeframe_signal, signal_candles, snapshot)
        entry = self.indicators.calculate(symbol, self.config.timeframe_entry, entry_candles, snapshot)
        indicator_map = {
            self.config.timeframe_trend: trend,
            self.config.timeframe_signal: signal,
            self.config.timeframe_entry: entry,
        }
        blockers: list[str] = []
        reasons: list[str] = []
        if snapshot.spread_pct > self.config.spread_limit:
            blockers.append("买卖价差超过 0.05%")
        if self.indicators.atr_is_anomalous(entry_candles, self.config.atr_anomaly_multiplier):
            blockers.append("ATR 异常放大，禁止追单")
        volume_missing = entry.volume <= entry.volume_avg20
        if volume_missing and self.config.require_volume_confirmation:
            blockers.append("当前成交量没有超过 20 根均量")

        long_conditions = [
            (trend.ema20 >= trend.ema60, "1H 趋势不为空头"),
            (signal.ema20 > signal.ema60, "15m EMA20 > EMA60"),
            (entry.close > entry.ema20, "5m 价格站上 EMA20"),
            (50 <= entry.rsi <= 70, "RSI 位于 50 到 70"),
        ]
        short_conditions = [
            (trend.ema20 <= trend.ema60, "1H 趋势不为多头"),
            (signal.ema20 < signal.ema60, "15m EMA20 < EMA60"),
            (entry.close < entry.ema20, "5m 价格跌破 EMA20"),
            (30 <= entry.rsi <= 50, "RSI 位于 30 到 50"),
        ]

        long_score = sum(1 for ok, _ in long_conditions if ok)
        short_score = sum(1 for ok, _ in short_conditions if ok)
        if long_score >= self.config.strategy_min_score and not blockers:
            reasons.extend(text for ok, text in long_conditions if ok)
            if volume_missing:
                reasons.append("成交量未放大，信号强度下调")
            return self._trade(symbol, "long", snapshot, entry, reasons, indicator_map)

        short_allowed = bool(
            (self.config.enable_futures and self.config.enable_short)
            or self.config.paper_futures_enabled
        )
        if short_allowed:
            if short_score >= self.config.strategy_min_score and not blockers:
                reasons.extend(text for ok, text in short_conditions if ok)
                if volume_missing:
                    reasons.append("成交量未放大，信号强度下调")
                return self._trade(symbol, "short", snapshot, entry, reasons, indicator_map)
        elif short_score >= 3:
            blockers.append("空头条件接近成立，但合约/做空未开启")

        not_ready = []
        if long_score < self.config.strategy_min_score:
            not_ready.append(f"多头条件只满足 {long_score}/4")
        if short_allowed and short_score < self.config.strategy_min_score:
            not_ready.append(f"空头条件只满足 {short_score}/4")
        return self._no_trade(symbol, reasons, blockers + not_ready, indicator_map)

    def _trade(
        self,
        symbol: str,
        direction: str,
        snapshot: MarketSnapshot,
        entry: IndicatorSnapshot,
        reasons: list[str],
        indicators: dict[str, IndicatorSnapshot],
    ) -> CandidateSignal:
        distance = self.config.stop_loss_atr_multiplier * entry.atr
        if direction == "long":
            stop_loss = snapshot.last_price - distance
            take_profit = snapshot.last_price + distance * self.config.take_profit_rr
        else:
            stop_loss = snapshot.last_price + distance
            take_profit = snapshot.last_price - distance * self.config.take_profit_rr
        return CandidateSignal(
            symbol=symbol,
            decision="trade",
            direction=direction,  # type: ignore[arg-type]
            entry_price=snapshot.last_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=entry.atr,
            signal_strength="强" if len(reasons) >= 4 and "成交量未放大，信号强度下调" not in reasons else "中",
            reasons=reasons,
            blockers=[],
            indicators=indicators,
        )

    def _no_trade(
        self,
        symbol: str,
        reasons: list[str],
        blockers: list[str],
        indicators: dict[str, IndicatorSnapshot],
    ) -> CandidateSignal:
        return CandidateSignal(
            symbol=symbol,
            decision="no_trade",
            direction=None,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            atr=None,
            signal_strength="弱",
            reasons=reasons,
            blockers=blockers,
            indicators=indicators,
        )
