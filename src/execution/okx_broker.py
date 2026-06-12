from __future__ import annotations

import logging

from src.market_data.okx_client import OKXRestClient
from src.risk_manager.risk_manager import RiskManager
from src.utils.config import TradingConfig
from src.utils.types import OrderPlan, Position


class OKXBroker:
    def __init__(
        self,
        config: TradingConfig,
        client: OKXRestClient,
        risk_manager: RiskManager,
        logger: logging.Logger,
    ):
        self.config = config
        self.client = client
        self.risk_manager = risk_manager
        self.logger = logger

    def place_order(self, plan: OrderPlan, positions: list[Position]) -> dict:
        self.risk_manager.validate_order(plan, positions)
        self._confirm_if_required(plan)
        payload = self._to_okx_order_payload(plan)
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
        response = self.client.place_order(payload)
        self.logger.info("okx order submitted symbol=%s direction=%s", plan.symbol, plan.direction)
        return {"response": response, "payload_without_secrets": payload}

    def cancel_all(self) -> None:
        self.client.cancel_all_open_orders()
        self.logger.warning("cancelled all open OKX orders")

    def emergency_stop(self) -> None:
        self.risk_manager.trigger_emergency_stop()
        self.cancel_all()
        self.logger.critical("emergency_stop triggered; new positions disabled")

    def _confirm_if_required(self, plan: OrderPlan) -> None:
        if self.config.require_manual_confirm:
            answer = input(
                f"Confirm LIVE order {plan.symbol} {plan.direction} qty={plan.quantity:.8f}? type YES: "
            )
            if answer != "YES":
                raise RuntimeError("manual confirmation rejected")

    def _to_okx_order_payload(self, plan: OrderPlan) -> dict:
        trade_mode = "isolated" if self.config.enable_futures else "cash"
        payload = {
            "instId": plan.symbol,
            "tdMode": trade_mode,
            "side": plan.side,
            "ordType": "limit",
            "px": f"{plan.entry_price:.8f}",
            "sz": f"{plan.quantity:.8f}",
        }
        if self.config.enable_futures:
            payload["lever"] = str(min(plan.leverage, 2))
            payload["posSide"] = "long" if plan.direction == "long" else "short"
        payload["attachAlgoOrds"] = [
            {
                "tpTriggerPx": f"{plan.take_profit:.8f}",
                "tpOrdPx": "-1",
                "slTriggerPx": f"{plan.stop_loss:.8f}",
                "slOrdPx": "-1",
            }
        ]
        return payload

