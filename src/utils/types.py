from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


Side = Literal["buy", "sell"]
Direction = Literal["long", "short"]


class TradingMode(str, Enum):
    DEMO = "demo"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_pct(self) -> float:
        if self.mid <= 0:
            return 1.0
        return (self.best_ask - self.best_bid) / self.mid


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    last_price: float
    spread_pct: float
    volume_24h: float = 0.0
    funding_rate: float | None = None


@dataclass(frozen=True)
class Signal:
    symbol: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    reason: str


@dataclass(frozen=True)
class OrderPlan:
    symbol: str
    side: Side
    direction: Direction
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float
    max_loss: float
    leverage: int
    reduce_only: bool = False


@dataclass
class Position:
    symbol: str
    direction: Direction
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float
    leverage: int = 1
    unrealized_pnl: float = 0.0


@dataclass(frozen=True)
class BacktestResult:
    total_return: float
    net_pnl: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float
    trade_count: int
    fees: float
    slippage_cost: float


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    available_balance: float
    daily_pnl: float
    risk_exposure: float
    positions: list[Position]
    open_order_count: int


@dataclass(frozen=True)
class IndicatorSnapshot:
    symbol: str
    timeframe: str
    close: float
    ema20: float
    ema60: float
    rsi: float
    macd: float
    macd_signal: float
    macd_histogram: float
    atr: float
    volume: float
    volume_avg20: float
    support: float
    resistance: float
    volatility: float
    recent_high: float
    recent_low: float
    spread_pct: float


@dataclass(frozen=True)
class CandidateSignal:
    symbol: str
    decision: Literal["trade", "no_trade"]
    direction: Direction | None
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    atr: float | None
    signal_strength: str
    reasons: list[str]
    blockers: list[str]
    indicators: dict[str, IndicatorSnapshot]


@dataclass(frozen=True)
class TradingAdvice:
    symbol: str
    action: str
    signal_strength: str
    risk_level: str
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    quantity: float | None
    max_loss: float | None
    risk_reward: float | None
    reasons: list[str]
    risk_warnings: list[str]
    no_trade_reasons: list[str]
    worst_case_loss: float | None
    requires_user_confirmation: bool
    text: str
