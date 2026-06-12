from __future__ import annotations

from dataclasses import replace
from itertools import product
from typing import Iterable

import pandas as pd

from src.backtest.engine import BacktestEngine
from src.utils.config import TradingConfig
from src.utils.types import BacktestResult


def parameter_scan(
    base_config: TradingConfig,
    data: pd.DataFrame,
    symbol: str,
    stop_loss_atr_values: Iterable[float],
    take_profit_rr_values: Iterable[float],
) -> list[tuple[TradingConfig, BacktestResult]]:
    results: list[tuple[TradingConfig, BacktestResult]] = []
    for stop_loss_atr, take_profit_rr in product(stop_loss_atr_values, take_profit_rr_values):
        cfg = replace(
            base_config,
            stop_loss_atr_multiplier=float(stop_loss_atr),
            take_profit_rr=float(take_profit_rr),
        )
        result = BacktestEngine(cfg).run(data, symbol=symbol)
        results.append((cfg, result))
    return sorted(
        results,
        key=lambda item: (
            item[1].max_drawdown,
            -item[1].profit_factor,
            -item[1].total_return,
        ),
    )
