from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from src.risk_manager.risk_manager import RiskManager
from src.strategy.trend_following import TrendFollowingStrategy
from src.utils.config import TradingConfig
from src.utils.types import BacktestResult, Candle, MarketSnapshot, Position


@dataclass
class ClosedTrade:
    pnl: float
    fee: float
    slippage_cost: float


class BacktestEngine:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.strategy = TrendFollowingStrategy(config)

    def run(self, data: pd.DataFrame, symbol: str = "BTC-USDT") -> BacktestResult:
        frame = data.sort_values("timestamp").reset_index(drop=True).copy()
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = frame[column].astype(float)

        risk = RiskManager(self.config, self.config.paper_starting_equity)
        positions: list[Position] = []
        trades: list[ClosedTrade] = []
        equity_curve = [self.config.paper_starting_equity]

        signal_frame = self._resample(frame, self.config.timeframe_signal)
        entry_frame = self._resample(frame, self.config.timeframe_entry)
        for idx in range(70, len(entry_frame)):
            current_rows = entry_frame.iloc[: idx + 1]
            current_ts = current_rows.iloc[-1]["timestamp"]
            signal_rows = signal_frame[signal_frame["timestamp"] <= current_ts]
            if len(signal_rows) < 70:
                continue

            last_price = float(current_rows.iloc[-1]["close"])
            positions, closed = self._mark_positions(positions, last_price)
            for trade in closed:
                risk.record_trade_result(trade.pnl - trade.fee - trade.slippage_cost)
                trades.append(trade)
                equity_curve.append(risk.state.equity)

            if positions:
                continue
            signal = self.strategy.generate_signal(
                symbol=symbol,
                signal_candles=self._to_candles(signal_rows.tail(100)),
                entry_candles=self._to_candles(current_rows.tail(100)),
                snapshot=MarketSnapshot(symbol=symbol, last_price=last_price, spread_pct=0.0002),
            )
            if not signal:
                continue
            try:
                plan = risk.build_order_plan(signal, positions)
            except ValueError:
                continue
            fee = plan.entry_price * plan.quantity * self.config.taker_fee
            slip = plan.entry_price * plan.quantity * self.config.slippage
            positions.append(
                Position(
                    symbol=plan.symbol,
                    direction=plan.direction,
                    quantity=plan.quantity,
                    entry_price=plan.entry_price * (1 + self.config.slippage),
                    stop_loss=plan.stop_loss,
                    take_profit=plan.take_profit,
                )
            )
            risk.record_trade_result(-(fee + slip))
            equity_curve.append(risk.state.equity)

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        total_fees = sum(t.fee for t in trades)
        total_slip = sum(t.slippage_cost for t in trades)
        net_pnl = risk.state.equity - self.config.paper_starting_equity
        returns = pd.Series(equity_curve).pct_change().dropna()
        sharpe = 0.0
        if len(returns) > 1 and returns.std() > 0:
            sharpe = float((returns.mean() / returns.std()) * math.sqrt(365 * 24 * 12))
        return BacktestResult(
            total_return=net_pnl / self.config.paper_starting_equity,
            net_pnl=net_pnl,
            win_rate=len(wins) / len(trades) if trades else 0.0,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit else 0.0,
            max_drawdown=self._max_drawdown(equity_curve),
            sharpe_ratio=sharpe,
            trade_count=len(trades),
            fees=total_fees,
            slippage_cost=total_slip,
        )

    def _mark_positions(
        self, positions: list[Position], price: float
    ) -> tuple[list[Position], list[ClosedTrade]]:
        remaining: list[Position] = []
        closed: list[ClosedTrade] = []
        for pos in positions:
            exit_price = None
            if pos.direction == "long":
                if price <= pos.stop_loss:
                    exit_price = pos.stop_loss
                elif price >= pos.take_profit:
                    exit_price = pos.take_profit
            else:
                if price >= pos.stop_loss:
                    exit_price = pos.stop_loss
                elif price <= pos.take_profit:
                    exit_price = pos.take_profit
            if exit_price is None:
                remaining.append(pos)
                continue
            pnl = (
                (exit_price - pos.entry_price) * pos.quantity
                if pos.direction == "long"
                else (pos.entry_price - exit_price) * pos.quantity
            )
            fee = (pos.entry_price + exit_price) * pos.quantity * self.config.taker_fee
            slip = exit_price * pos.quantity * self.config.slippage
            closed.append(ClosedTrade(pnl=pnl, fee=fee, slippage_cost=slip))
        return remaining, closed

    def _resample(self, frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        if timeframe == "1m":
            return frame.copy()
        rule = {"5m": "5min", "15m": "15min"}[timeframe]
        df = frame.copy()
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        resampled = (
            df.set_index("datetime")
            .resample(rule)
            .agg(
                {
                    "timestamp": "last",
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
            .reset_index(drop=True)
        )
        return resampled

    def _to_candles(self, frame: pd.DataFrame) -> list[Candle]:
        return [
            Candle(
                timestamp=int(row.timestamp),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
            for row in frame.itertuples(index=False)
        ]

    def _max_drawdown(self, equity_curve: list[float]) -> float:
        peak = equity_curve[0]
        max_dd = 0.0
        for equity in equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
        return max_dd

