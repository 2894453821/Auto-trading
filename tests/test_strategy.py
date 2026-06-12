from __future__ import annotations

from src.strategy.trend_following import TrendFollowingStrategy
from src.utils.config import TradingConfig
from src.utils.types import Candle, MarketSnapshot


def make_candles(count: int, start: float, step: float, volume: float = 100.0) -> list[Candle]:
    candles = []
    price = start
    for i in range(count):
        price += step
        candles.append(
            Candle(
                timestamp=i * 60_000,
                open=price - step,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=volume,
            )
        )
    return candles


def test_long_signal_requires_tight_spread() -> None:
    cfg = TradingConfig()
    strategy = TrendFollowingStrategy(cfg)
    signal_candles = make_candles(90, 100, 1)
    entry_candles = make_candles(90, 100, 0.2, 100)
    entry_candles[-2] = Candle(88 * 60_000, 116, 117, 115, 116, 100)
    entry_candles[-1] = Candle(89 * 60_000, 116, 121, 115, 120, 300)

    wide = MarketSnapshot("BTC-USDT", last_price=120, spread_pct=0.002)
    assert strategy.generate_signal("BTC-USDT", signal_candles, entry_candles, wide) is None


def test_strategy_blocks_disallowed_symbol() -> None:
    cfg = TradingConfig()
    strategy = TrendFollowingStrategy(cfg)
    signal = strategy.generate_signal(
        "DOGE-USDT",
        make_candles(90, 100, 1),
        make_candles(90, 100, 0.2, 300),
        MarketSnapshot("DOGE-USDT", last_price=100, spread_pct=0.0001),
    )
    assert signal is None

