from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.utils.config import TradingConfig
from src.utils.types import OrderPlan, Position, Signal


@dataclass
class RiskState:
    starting_equity: float
    equity: float
    daily_start_equity: float
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    emergency_stopped: bool = False
    current_day: date = field(default_factory=date.today)


class RiskManager:
    def __init__(self, config: TradingConfig, starting_equity: float):
        self.config = config
        self.state = RiskState(
            starting_equity=starting_equity,
            equity=starting_equity,
            daily_start_equity=starting_equity,
        )

    def trigger_emergency_stop(self) -> None:
        self.state.emergency_stopped = True

    def set_paper_equity(self, equity: float) -> None:
        if equity <= 0:
            raise ValueError("paper equity must be positive")
        self.state.equity = equity
        self.state.starting_equity = equity
        self.state.daily_start_equity = equity
        self.state.daily_pnl = 0.0
        self.state.consecutive_losses = 0

    def reset_day_if_needed(self) -> None:
        today = date.today()
        if self.state.current_day != today:
            self.state.current_day = today
            self.state.daily_start_equity = self.state.equity
            self.state.daily_pnl = 0.0

    def can_open_new_position(self, positions: list[Position]) -> tuple[bool, str]:
        self.reset_day_if_needed()
        if self.state.emergency_stopped:
            return False, "emergency_stop is active"
        if self.state.consecutive_losses >= self.config.stop_after_consecutive_losses:
            return False, "three consecutive losses reached"
        if len(positions) >= self.config.max_positions:
            return False, "max positions reached"
        daily_loss_limit = self.state.daily_start_equity * self.config.max_daily_loss
        if self.state.daily_pnl <= -daily_loss_limit:
            return False, "daily loss limit reached"
        return True, "ok"

    def build_order_plan(
        self,
        signal: Signal,
        positions: list[Position],
        equity: float | None = None,
        leverage: int | None = None,
    ) -> OrderPlan:
        allowed, reason = self.can_open_new_position(positions)
        if not allowed:
            raise ValueError(reason)
        if signal.symbol not in self.config.allowed_symbols:
            raise ValueError("symbol not allowed")
        if signal.direction == "short" and not self._short_allowed():
            raise ValueError("short signals require futures mode")
        order_leverage = int(leverage if leverage is not None else self.config.leverage)
        max_leverage = self.config.paper_max_leverage if self.config.paper_futures_enabled else 1
        if order_leverage < 1:
            raise ValueError("leverage must be >= 1")
        if order_leverage > max_leverage:
            raise ValueError("leverage exceeds risk cap")
        if self.config.enable_futures and not self.config.isolated_margin:
            raise ValueError("futures must use isolated margin")

        account_equity = float(equity if equity is not None else self.state.equity)
        max_loss = account_equity * self.config.max_risk_per_trade
        risk_per_unit = abs(signal.entry_price - signal.stop_loss)
        if risk_per_unit <= 0:
            raise ValueError("stop loss must be away from entry")
        quantity = max_loss / risk_per_unit
        notional = quantity * signal.entry_price
        max_notional = account_equity * order_leverage * 0.5
        if notional > max_notional:
            quantity = max_notional / signal.entry_price
            max_loss = quantity * risk_per_unit
        if quantity <= 0:
            raise ValueError("computed quantity is zero")
        self.validate_stops(signal)
        side = "buy" if signal.direction == "long" else "sell"
        return OrderPlan(
            symbol=signal.symbol,
            side=side,
            direction=signal.direction,
            quantity=quantity,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            max_loss=max_loss,
            leverage=order_leverage,
        )

    def build_amount_order_plan(
        self,
        signal: Signal,
        positions: list[Position],
        margin_amount: float,
        leverage: int,
        equity: float | None = None,
        ignore_sizing_limits: bool = False,
    ) -> OrderPlan:
        allowed, reason = self.can_open_new_position(positions)
        if not allowed:
            raise ValueError(reason)
        if margin_amount <= 0:
            raise ValueError("margin amount must be positive")
        if signal.symbol not in self.config.allowed_symbols:
            raise ValueError("symbol not allowed")
        if signal.direction == "short" and not self._short_allowed():
            raise ValueError("short signals require futures mode")
        order_leverage = int(leverage)
        if order_leverage < 1:
            raise ValueError("leverage must be >= 1")
        if order_leverage > self.config.paper_max_leverage:
            raise ValueError("leverage exceeds risk cap")
        account_equity = float(equity if equity is not None else self.state.equity)
        margin_cap = account_equity * 0.5
        if margin_amount > margin_cap and not ignore_sizing_limits:
            raise ValueError("margin amount exceeds 50% account cap")
        risk_per_unit = abs(signal.entry_price - signal.stop_loss)
        if risk_per_unit <= 0:
            raise ValueError("stop loss must be away from entry")
        notional = margin_amount * order_leverage
        quantity = notional / signal.entry_price
        max_loss = quantity * risk_per_unit
        if (
            max_loss > account_equity * self.config.max_risk_per_trade + 1e-9
            and not ignore_sizing_limits
        ):
            raise ValueError("amount would exceed per-trade risk")
        self.validate_stops(signal)
        side = "buy" if signal.direction == "long" else "sell"
        return OrderPlan(
            symbol=signal.symbol,
            side=side,
            direction=signal.direction,
            quantity=quantity,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            max_loss=max_loss,
            leverage=order_leverage,
        )

    def validate_order(
        self,
        order: OrderPlan,
        positions: list[Position],
        ignore_sizing_limits: bool = False,
    ) -> None:
        allowed, reason = self.can_open_new_position(positions)
        if not allowed:
            raise ValueError(reason)
        if order.symbol not in self.config.allowed_symbols:
            raise ValueError("symbol not allowed")
        if order.quantity <= 0:
            raise ValueError("quantity must be positive")
        if (
            order.max_loss > self.state.equity * self.config.max_risk_per_trade + 1e-9
            and not ignore_sizing_limits
        ):
            raise ValueError("order max loss exceeds per-trade risk")
        if order.direction == "long":
            if not (order.stop_loss < order.entry_price < order.take_profit):
                raise ValueError("long order requires stop < entry < take profit")
        else:
            if not (order.take_profit < order.entry_price < order.stop_loss):
                raise ValueError("short order requires take profit < entry < stop")
            if not self._short_allowed():
                raise ValueError("short order is disabled")
        if order.leverage > self.config.paper_max_leverage:
            raise ValueError("order leverage exceeds paper leverage cap")

    def validate_stops(self, signal: Signal) -> None:
        if signal.direction == "long" and not (
            signal.stop_loss < signal.entry_price < signal.take_profit
        ):
            raise ValueError("invalid long stop/take-profit")
        if signal.direction == "short" and not (
            signal.take_profit < signal.entry_price < signal.stop_loss
        ):
            raise ValueError("invalid short stop/take-profit")

    def record_trade_result(self, pnl: float) -> None:
        self.reset_day_if_needed()
        self.state.equity += pnl
        self.state.daily_pnl += pnl
        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

    def _short_allowed(self) -> bool:
        return bool(
            (self.config.enable_short and self.config.enable_futures)
            or self.config.paper_futures_enabled
        )
