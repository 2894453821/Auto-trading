from __future__ import annotations

from src.ai_advisor.advisor import AIAdvisor
from src.utils.config import TradingConfig
from src.utils.types import AccountSnapshot, CandidateSignal, OrderPlan


def test_ai_advisor_no_trade_does_not_require_confirmation() -> None:
    advisor = AIAdvisor(TradingConfig())
    candidate = CandidateSignal(
        symbol="BTC-USDT",
        decision="no_trade",
        direction=None,
        entry_price=None,
        stop_loss=None,
        take_profit=None,
        atr=None,
        signal_strength="弱",
        reasons=["当前策略条件不足"],
        blockers=["买卖价差过大"],
        indicators={},
    )
    account = AccountSnapshot(10_000, 10_000, 0, 0, [], 0)
    advice = advisor.generate(candidate, account, None)
    assert advice.action == "暂不交易"
    assert advice.requires_user_confirmation is False
    assert "暂不交易" in advice.text


def test_ai_advisor_trade_requires_user_confirmation() -> None:
    advisor = AIAdvisor(TradingConfig())
    candidate = CandidateSignal(
        symbol="BTC-USDT",
        decision="trade",
        direction="long",
        entry_price=100,
        stop_loss=95,
        take_profit=109,
        atr=4,
        signal_strength="强",
        reasons=["15m EMA20 > EMA60", "5m 价格站上 EMA20"],
        blockers=[],
        indicators={},
    )
    account = AccountSnapshot(10_000, 10_000, 0, 0, [], 0)
    plan = OrderPlan("BTC-USDT", "buy", "long", 1, 100, 95, 109, 5, 1)
    advice = advisor.generate(candidate, account, plan)
    assert advice.action == "做多"
    assert advice.requires_user_confirmation is True
    assert "最终操作" in advice.text
