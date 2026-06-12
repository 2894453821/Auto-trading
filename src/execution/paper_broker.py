from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import uuid4

from src.risk_manager.risk_manager import RiskManager
from src.utils.config import TradingConfig
from src.utils.types import OrderPlan, Position


@dataclass
class PaperOrder:
    order_id: str
    plan: OrderPlan
    status: str = "filled"


@dataclass
class PaperBroker:
    config: TradingConfig
    risk_manager: RiskManager
    logger: logging.Logger
    positions: list[Position] = field(default_factory=list)
    orders: list[PaperOrder] = field(default_factory=list)

    def place_order(self, plan: OrderPlan, ignore_sizing_limits: bool = False) -> PaperOrder:
        self.risk_manager.validate_order(
            plan,
            self.positions,
            ignore_sizing_limits=ignore_sizing_limits,
        )
        self._log_order_summary(plan)
        order = PaperOrder(order_id=str(uuid4()), plan=plan)
        self.orders.append(order)
        self.positions.append(
            Position(
                symbol=plan.symbol,
                direction=plan.direction,
                quantity=plan.quantity,
                entry_price=plan.entry_price,
                stop_loss=plan.stop_loss,
                take_profit=plan.take_profit,
                leverage=plan.leverage,
            )
        )
        self.logger.info("paper order filled order_id=%s symbol=%s", order.order_id, plan.symbol)
        return order

    def cancel_all(self) -> None:
        for order in self.orders:
            if order.status == "open":
                order.status = "cancelled"
        self.logger.warning("paper broker cancelled all open orders")

    def emergency_stop(self) -> None:
        self.risk_manager.trigger_emergency_stop()
        self.cancel_all()
        self.logger.critical("emergency_stop triggered; new positions disabled")

    def get_positions(self) -> list[Position]:
        return list(self.positions)

    def get_open_orders(self) -> list[PaperOrder]:
        return [order for order in self.orders if order.status == "open"]

    def close_position(self, symbol: str, price: float, quantity: float | None = None) -> float:
        if quantity is not None and quantity <= 0:
            raise ValueError("close quantity must be positive")
        remaining: list[Position] = []
        realized_pnl = 0.0
        closed_count = 0
        remaining_to_close = quantity
        for pos in self.positions:
            if pos.symbol != symbol:
                remaining.append(pos)
                continue
            close_qty = pos.quantity if remaining_to_close is None else min(pos.quantity, remaining_to_close)
            if close_qty <= 0:
                remaining.append(pos)
                continue
            pnl = (
                (price - pos.entry_price) * close_qty
                if pos.direction == "long"
                else (pos.entry_price - price) * close_qty
            )
            fee = (pos.entry_price + price) * close_qty * self.config.taker_fee
            realized = pnl - fee
            realized_pnl += realized
            closed_count += 1
            self.risk_manager.record_trade_result(realized)
            self.logger.info(
                "paper position manually closed symbol=%s qty=%.8f pnl=%.4f fee=%.4f",
                symbol,
                close_qty,
                pnl,
                fee,
            )
            if close_qty < pos.quantity:
                pos.quantity -= close_qty
                remaining.append(pos)
            if remaining_to_close is not None:
                remaining_to_close -= close_qty
        self.positions = remaining
        if closed_count == 0:
            raise ValueError(f"no paper position found for {symbol}")
        return realized_pnl

    def update_stops(self, symbol: str, stop_loss: float, take_profit: float) -> int:
        if stop_loss <= 0 or take_profit <= 0:
            raise ValueError("stop loss and take profit must be positive")
        updated = 0
        for pos in self.positions:
            if pos.symbol != symbol:
                continue
            if pos.direction == "long" and not (stop_loss < pos.entry_price < take_profit):
                raise ValueError("long position requires stop < entry < take profit")
            if pos.direction == "short" and not (take_profit < pos.entry_price < stop_loss):
                raise ValueError("short position requires take profit < entry < stop")
            pos.stop_loss = stop_loss
            pos.take_profit = take_profit
            updated += 1
        if updated == 0:
            raise ValueError(f"no paper position found for {symbol}")
        self.logger.info(
            "paper position stops updated symbol=%s stop_loss=%.4f take_profit=%.4f",
            symbol,
            stop_loss,
            take_profit,
        )
        return updated

    def mark_price(
        self,
        symbol: str,
        price: float,
        high: float | None = None,
        low: float | None = None,
    ) -> None:
        mark_high = price if high is None else high
        mark_low = price if low is None else low
        remaining: list[Position] = []
        for pos in self.positions:
            if pos.symbol != symbol:
                remaining.append(pos)
                continue
            exit_price = None
            exit_reason = ""
            if pos.direction == "long":
                if mark_low <= pos.stop_loss:
                    exit_price = pos.stop_loss
                    exit_reason = "stop_loss"
                elif mark_high >= pos.take_profit:
                    exit_price = pos.take_profit
                    exit_reason = "take_profit"
            else:
                if mark_high >= pos.stop_loss:
                    exit_price = pos.stop_loss
                    exit_reason = "stop_loss"
                elif mark_low <= pos.take_profit:
                    exit_price = pos.take_profit
                    exit_reason = "take_profit"
            if exit_price is None:
                remaining.append(pos)
                continue
            pnl = (
                (exit_price - pos.entry_price) * pos.quantity
                if pos.direction == "long"
                else (pos.entry_price - exit_price) * pos.quantity
            )
            fee = (pos.entry_price + exit_price) * pos.quantity * self.config.taker_fee
            self.risk_manager.record_trade_result(pnl - fee)
            self.logger.info(
                "paper position closed symbol=%s reason=%s pnl=%.4f fee=%.4f",
                symbol,
                exit_reason,
                pnl,
                fee,
            )
        self.positions = remaining

    def _log_order_summary(self, plan: OrderPlan) -> None:
        self.logger.info(
            "order summary symbol=%s direction=%s qty=%.8f entry=%.4f tp=%.4f sl=%.4f max_loss=%.2f",
            plan.symbol,
            plan.direction,
            plan.quantity,
            plan.entry_price,
            plan.take_profit,
            plan.stop_loss,
            plan.max_loss,
        )
