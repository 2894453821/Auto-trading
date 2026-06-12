from __future__ import annotations

import pytest

from src.risk_manager.risk_manager import RiskManager
from src.utils.config import TradingConfig
from src.utils.types import Position, Signal


def test_order_plan_caps_risk_per_trade() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    signal = Signal(
        symbol="BTC-USDT",
        direction="long",
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        atr=4,
        reason="test",
    )
    plan = risk.build_order_plan(signal, [])
    assert plan.max_loss <= 50.0000001
    assert plan.stop_loss < plan.entry_price < plan.take_profit


def test_three_losses_pause_trading() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    risk.record_trade_result(-1)
    risk.record_trade_result(-1)
    risk.record_trade_result(-1)
    allowed, reason = risk.can_open_new_position([])
    assert not allowed
    assert "three consecutive" in reason


def test_emergency_stop_blocks_new_orders() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    risk.trigger_emergency_stop()
    allowed, reason = risk.can_open_new_position([])
    assert not allowed
    assert "emergency_stop" in reason


def test_paper_short_is_allowed_with_paper_futures() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    signal = Signal(
        symbol="BTC-USDT",
        direction="short",
        entry_price=100,
        stop_loss=105,
        take_profit=90,
        atr=4,
        reason="test",
    )
    plan = risk.build_order_plan(signal, [])
    assert plan.direction == "short"
    assert plan.side == "sell"


def test_leverage_cap_is_enforced() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    signal = Signal(
        symbol="BTC-USDT",
        direction="long",
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        atr=4,
        reason="test",
    )
    plan = risk.build_order_plan(signal, [], leverage=100)
    assert plan.leverage == 100
    with pytest.raises(ValueError):
        risk.build_order_plan(signal, [], leverage=101)


def test_amount_order_plan_uses_margin_amount_and_leverage() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    signal = Signal(
        symbol="BTC-USDT",
        direction="long",
        entry_price=100,
        stop_loss=99.5,
        take_profit=101,
        atr=1,
        reason="test",
    )
    plan = risk.build_amount_order_plan(signal, [], margin_amount=100, leverage=10)
    assert plan.quantity == pytest.approx(10)
    assert plan.leverage == 10
    assert plan.max_loss == pytest.approx(5)


def test_amount_order_can_ignore_sizing_limits_for_paper_override() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    signal = Signal(
        symbol="BTC-USDT",
        direction="long",
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        atr=4,
        reason="test",
    )
    with pytest.raises(ValueError):
        risk.build_amount_order_plan(signal, [], margin_amount=9_000, leverage=10)
    plan = risk.build_amount_order_plan(
        signal,
        [],
        margin_amount=9_000,
        leverage=10,
        ignore_sizing_limits=True,
    )
    assert plan.quantity == pytest.approx(900)
    assert plan.max_loss == pytest.approx(4_500)


def test_max_positions_enforced() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    positions = [
        Position("BTC-USDT", "long", 1, 100, 95, 110),
        Position("ETH-USDT", "long", 1, 100, 95, 110),
    ]
    allowed, reason = risk.can_open_new_position(positions)
    assert not allowed
    assert "max positions" in reason
