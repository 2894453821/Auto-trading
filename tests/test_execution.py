from __future__ import annotations

import logging

import pytest

from src.execution.paper_broker import PaperBroker
from src.risk_manager.risk_manager import RiskManager
from src.utils.config import TradingConfig
from src.utils.types import OrderPlan


def test_paper_order_requires_valid_stop_take_profit() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    broker = PaperBroker(cfg, risk, logging.getLogger("test"))
    bad = OrderPlan(
        symbol="BTC-USDT",
        side="buy",
        direction="long",
        quantity=1,
        entry_price=100,
        stop_loss=101,
        take_profit=110,
        max_loss=10,
        leverage=1,
    )
    with pytest.raises(ValueError):
        broker.place_order(bad)


def test_paper_broker_emergency_stop_blocks_next_order() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    broker = PaperBroker(cfg, risk, logging.getLogger("test"))
    broker.emergency_stop()
    good = OrderPlan(
        symbol="BTC-USDT",
        side="buy",
        direction="long",
        quantity=1,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        max_loss=5,
        leverage=1,
    )
    with pytest.raises(ValueError):
        broker.place_order(good)


def test_paper_broker_can_close_position() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    broker = PaperBroker(cfg, risk, logging.getLogger("test"))
    plan = OrderPlan(
        symbol="BTC-USDT",
        side="buy",
        direction="long",
        quantity=1,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        max_loss=5,
        leverage=1,
    )
    broker.place_order(plan)
    pnl = broker.close_position("BTC-USDT", 105)
    assert pnl > 0
    assert broker.get_positions() == []


def test_paper_broker_override_allows_large_max_loss() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    broker = PaperBroker(cfg, risk, logging.getLogger("test"))
    large = OrderPlan(
        symbol="BTC-USDT",
        side="buy",
        direction="long",
        quantity=100,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        max_loss=500,
        leverage=10,
    )
    with pytest.raises(ValueError):
        broker.place_order(large)
    order = broker.place_order(large, ignore_sizing_limits=True)
    assert order.status == "filled"


def test_paper_broker_closes_when_candle_hits_take_profit() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    broker = PaperBroker(cfg, risk, logging.getLogger("test"))
    plan = OrderPlan(
        symbol="BTC-USDT",
        side="buy",
        direction="long",
        quantity=1,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        max_loss=5,
        leverage=1,
    )
    broker.place_order(plan)
    broker.mark_price("BTC-USDT", price=105, high=111, low=104)
    assert broker.get_positions() == []


def test_paper_broker_can_partially_close_position() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    broker = PaperBroker(cfg, risk, logging.getLogger("test"))
    plan = OrderPlan(
        symbol="BTC-USDT",
        side="buy",
        direction="long",
        quantity=2,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        max_loss=10,
        leverage=1,
    )
    broker.place_order(plan)
    pnl = broker.close_position("BTC-USDT", 105, quantity=0.5)
    assert pnl > 0
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == pytest.approx(1.5)


def test_paper_broker_can_update_position_stops() -> None:
    cfg = TradingConfig()
    risk = RiskManager(cfg, starting_equity=10_000)
    broker = PaperBroker(cfg, risk, logging.getLogger("test"))
    plan = OrderPlan(
        symbol="BTC-USDT",
        side="buy",
        direction="long",
        quantity=1,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        max_loss=5,
        leverage=1,
    )
    broker.place_order(plan)
    updated = broker.update_stops("BTC-USDT", stop_loss=96, take_profit=112)
    position = broker.get_positions()[0]
    assert updated == 1
    assert position.stop_loss == 96
    assert position.take_profit == 112
