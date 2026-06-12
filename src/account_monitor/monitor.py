from __future__ import annotations

from src.execution.paper_broker import PaperBroker
from src.market_data.okx_client import OKXRestClient
from src.risk_manager.risk_manager import RiskManager
from src.utils.config import TradingConfig
from src.utils.types import AccountSnapshot, Position, TradingMode


class AccountMonitor:
    def __init__(
        self,
        config: TradingConfig,
        client: OKXRestClient,
        risk_manager: RiskManager,
        paper_broker: PaperBroker,
    ):
        self.config = config
        self.client = client
        self.risk_manager = risk_manager
        self.paper_broker = paper_broker

    def snapshot(self) -> AccountSnapshot:
        if self.config.trading_mode == TradingMode.LIVE:
            return self._live_snapshot()
        positions = self.paper_broker.get_positions()
        exposure = sum(pos.quantity * pos.entry_price for pos in positions)
        margin_used = sum(
            pos.quantity * pos.entry_price / max(pos.leverage, 1) for pos in positions
        )
        equity = self.risk_manager.state.equity
        return AccountSnapshot(
            equity=equity,
            available_balance=max(equity - margin_used, 0.0),
            daily_pnl=self.risk_manager.state.daily_pnl,
            risk_exposure=exposure,
            positions=positions,
            open_order_count=len(self.paper_broker.get_open_orders()),
        )

    def _live_snapshot(self) -> AccountSnapshot:
        balances = self.client.get_balance()
        raw_positions = self.client.get_positions()
        equity = 0.0
        available = 0.0
        if balances:
            detail = balances[0].get("details", [])
            equity = sum(float(item.get("eq", 0) or 0) for item in detail)
            available = sum(float(item.get("availBal", 0) or 0) for item in detail)
        positions: list[Position] = []
        exposure = 0.0
        for item in raw_positions:
            qty = abs(float(item.get("pos", 0) or 0))
            avg_px = float(item.get("avgPx", 0) or 0)
            exposure += qty * avg_px
        return AccountSnapshot(
            equity=equity,
            available_balance=available,
            daily_pnl=self.risk_manager.state.daily_pnl,
            risk_exposure=exposure,
            positions=positions,
            open_order_count=len(self.client.get_open_orders()),
        )
