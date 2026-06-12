from __future__ import annotations

from src.strategy_engine.engine import StrategyEngine
from src.utils.config import TradingConfig
from src.utils.types import Candle, MarketSnapshot


class FakeMarket:
    def __init__(self, spread: float = 0.0002):
        self.spread = spread

    def snapshot(self, symbol: str) -> MarketSnapshot:
        return MarketSnapshot(symbol=symbol, last_price=110.0, spread_pct=self.spread)

    def candles(self, symbol: str, timeframe: str, limit: int = 120) -> list[Candle]:
        candles = []
        price = 100.0
        for idx in range(limit):
            step = 0.3 if idx % 3 else -0.1
            price += step
            candles.append(
                Candle(
                    timestamp=idx * 60_000,
                    open=price - step,
                    high=price + 0.8,
                    low=price - 0.8,
                    close=price,
                    volume=100 + idx,
                )
            )
        return candles


def test_strategy_engine_returns_no_trade_when_spread_is_too_wide() -> None:
    cfg = TradingConfig()
    engine = StrategyEngine(cfg, FakeMarket(spread=0.01))  # type: ignore[arg-type]
    candidate = engine.analyze("BTC-USDT")
    assert candidate.decision == "no_trade"
    assert any("价差" in item for item in candidate.blockers)

