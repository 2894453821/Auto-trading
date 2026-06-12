from __future__ import annotations

import logging

from src.utils.config import TradingConfig
from src.utils.types import TradingAdvice


class Notifier:
    def __init__(self, config: TradingConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger

    def publish(self, advice: TradingAdvice) -> None:
        self.logger.info(
            "advisor notification symbol=%s action=%s risk=%s max_loss=%s requires_confirm=%s",
            advice.symbol,
            advice.action,
            advice.risk_level,
            advice.max_loss,
            advice.requires_user_confirmation,
        )
        if self.config.enable_telegram:
            self.logger.warning("telegram notification is configured but not implemented in v1")
        if self.config.enable_email:
            self.logger.warning("email notification is configured but not implemented in v1")

