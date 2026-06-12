from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from src.backtest.engine import BacktestEngine
from src.backtest.scan import parameter_scan
from src.execution.okx_broker import OKXBroker
from src.execution.paper_broker import PaperBroker
from src.logger.setup import setup_logging
from src.market_data.okx_client import OKXRestClient
from src.market_data.service import MarketDataService
from src.risk_manager.risk_manager import RiskManager
from src.strategy.trend_following import TrendFollowingStrategy
from src.ui.server import run_ui_server
from src.utils.config import load_config
from src.utils.types import TradingMode


def run_paper(args: argparse.Namespace) -> None:
    config, credentials = load_config(args.config)
    logger = setup_logging(config.log_level, config.log_directory)
    client = OKXRestClient(config, credentials)
    market = MarketDataService(client)
    strategy = TrendFollowingStrategy(config)
    risk = RiskManager(config, config.paper_starting_equity)
    broker = PaperBroker(config=config, risk_manager=risk, logger=logger)
    try:
        while True:
            for symbol in config.symbols:
                signal = strategy.generate_signal(
                    symbol=symbol,
                    signal_candles=market.candles(symbol, config.timeframe_signal, 120),
                    entry_candles=market.candles(symbol, config.timeframe_entry, 120),
                    snapshot=market.snapshot(symbol),
                )
                if signal:
                    plan = risk.build_order_plan(signal, broker.get_positions())
                    broker.place_order(plan)
                else:
                    logger.info("no signal symbol=%s", symbol)
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        client.close()


def run_live(args: argparse.Namespace) -> None:
    config, credentials = load_config(args.config)
    if config.trading_mode != TradingMode.LIVE:
        raise RuntimeError("run-live requires trading_mode=live.")
    logger = setup_logging(config.log_level, config.log_directory)
    client = OKXRestClient(config, credentials)
    market = MarketDataService(client)
    strategy = TrendFollowingStrategy(config)
    risk = RiskManager(config, config.paper_starting_equity)
    broker = OKXBroker(config=config, client=client, risk_manager=risk, logger=logger)
    try:
        while True:
            okx_positions = client.get_positions()
            positions = []
            for symbol in config.symbols:
                signal = strategy.generate_signal(
                    symbol=symbol,
                    signal_candles=market.candles(symbol, config.timeframe_signal, 120),
                    entry_candles=market.candles(symbol, config.timeframe_entry, 120),
                    snapshot=market.snapshot(symbol),
                )
                if signal:
                    plan = risk.build_order_plan(signal, positions)
                    broker.place_order(plan, positions)
                else:
                    logger.info("no signal symbol=%s positions=%s", symbol, len(okx_positions))
            time.sleep(args.interval)
    finally:
        client.close()


def run_backtest(args: argparse.Namespace) -> None:
    config, _ = load_config(args.config)
    data = pd.read_csv(args.csv)
    result = BacktestEngine(config).run(data, symbol=args.symbol)
    print("Backtest result")
    print(f"total_return: {result.total_return:.2%}")
    print(f"net_pnl: {result.net_pnl:.2f}")
    print(f"win_rate: {result.win_rate:.2%}")
    print(f"profit_factor: {result.profit_factor:.3f}")
    print(f"max_drawdown: {result.max_drawdown:.2%}")
    print(f"sharpe_ratio: {result.sharpe_ratio:.3f}")
    print(f"trade_count: {result.trade_count}")
    print(f"fees: {result.fees:.2f}")
    print(f"slippage_cost: {result.slippage_cost:.2f}")


def run_scan(args: argparse.Namespace) -> None:
    config, _ = load_config(args.config)
    data = pd.read_csv(args.csv)
    results = parameter_scan(
        config,
        data,
        symbol=args.symbol,
        stop_loss_atr_values=[float(x) for x in args.stop_loss_atr.split(",")],
        take_profit_rr_values=[float(x) for x in args.take_profit_rr.split(",")],
    )
    print("Parameter scan, sorted by lower drawdown then higher profit factor")
    for cfg, result in results:
        print(
            "sl_atr={:.2f} rr={:.2f} return={:.2%} mdd={:.2%} pf={:.3f} trades={}".format(
                cfg.stop_loss_atr_multiplier,
                cfg.take_profit_rr,
                result.total_return,
                result.max_drawdown,
                result.profit_factor,
                result.trade_count,
            )
        )


def run_ui(args: argparse.Namespace) -> None:
    config, credentials = load_config(args.config)
    logger = setup_logging(config.log_level, config.log_directory)
    run_ui_server(config, credentials, logger, args.host, args.port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OKX risk-first auto trader")
    sub = parser.add_subparsers(required=True)

    paper = sub.add_parser("run-paper")
    paper.add_argument("--config", default="config.yaml")
    paper.add_argument("--interval", type=int, default=60)
    paper.add_argument("--once", action="store_true")
    paper.set_defaults(func=run_paper)

    live = sub.add_parser("run-live")
    live.add_argument("--config", default="config.yaml")
    live.add_argument("--interval", type=int, default=60)
    live.set_defaults(func=run_live)

    backtest = sub.add_parser("backtest")
    backtest.add_argument("--config", default="config.yaml")
    backtest.add_argument("--csv", type=Path, required=True)
    backtest.add_argument("--symbol", default="BTC-USDT")
    backtest.set_defaults(func=run_backtest)

    scan = sub.add_parser("scan")
    scan.add_argument("--config", default="config.yaml")
    scan.add_argument("--csv", type=Path, required=True)
    scan.add_argument("--symbol", default="BTC-USDT")
    scan.add_argument("--stop-loss-atr", default="1.0,1.2,1.5")
    scan.add_argument("--take-profit-rr", default="1.5,1.8,2.0")
    scan.set_defaults(func=run_scan)

    ui = sub.add_parser("ui")
    ui.add_argument("--config", default="config.yaml")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8787)
    ui.set_defaults(func=run_ui)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
