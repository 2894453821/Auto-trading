from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.utils.types import TradingMode


@dataclass(frozen=True)
class OKXCredentials:
    api_key: str | None
    api_secret: str | None
    passphrase: str | None

    @property
    def complete(self) -> bool:
        return bool(self.api_key and self.api_secret and self.passphrase)


@dataclass(frozen=True)
class TradingConfig:
    trading_mode: TradingMode = TradingMode.PAPER
    live_trading_enabled: bool = False
    symbols: list[str] = field(default_factory=lambda: ["BTC-USDT", "ETH-USDT"])
    allowed_symbols: list[str] = field(
        default_factory=lambda: ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    )
    timeframe_signal: str = "15m"
    timeframe_entry: str = "5m"
    timeframe_trend: str = "1H"
    max_risk_per_trade: float = 0.005
    max_daily_loss: float = 0.02
    max_positions: int = 2
    leverage: int = 1
    stop_after_consecutive_losses: int = 3
    strategy_min_score: int = 3
    require_volume_confirmation: bool = False
    require_manual_confirm: bool = True
    enable_short: bool = False
    enable_futures: bool = False
    paper_futures_enabled: bool = True
    paper_max_leverage: int = 100
    isolated_margin: bool = True
    spread_limit: float = 0.0005
    atr_anomaly_multiplier: float = 2.5
    stop_loss_atr_multiplier: float = 1.2
    take_profit_rr: float = 1.8
    trailing_stop_enabled: bool = True
    trailing_stop_atr_multiplier: float = 1.0
    paper_starting_equity: float = 10_000.0
    taker_fee: float = 0.001
    maker_fee: float = 0.0008
    slippage: float = 0.0005
    okx_rest_base_url: str = "https://www.okx.com"
    okx_public_ws_url: str = "wss://ws.okx.com:8443/ws/v5/public"
    okx_private_ws_url: str = "wss://ws.okx.com:8443/ws/v5/private"
    okx_demo_public_ws_url: str = "wss://wspap.okx.com:8443/ws/v5/public"
    okx_demo_private_ws_url: str = "wss://wspap.okx.com:8443/ws/v5/private"
    okx_use_demo_trading_header: bool = True
    request_timeout_seconds: int = 10
    log_level: str = "INFO"
    log_directory: str = "logs"
    enable_ai_summary: bool = True
    allow_ai_auto_order: bool = False
    enable_telegram: bool = False
    enable_email: bool = False


def load_config(path: str | Path = "config.yaml") -> tuple[TradingConfig, OKXCredentials]:
    config_path = Path(path)
    load_dotenv(config_path.parent / ".env")

    raw: dict[str, Any] = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    cfg = TradingConfig(
        trading_mode=TradingMode(raw.get("trading_mode", "paper")),
        live_trading_enabled=bool(raw.get("live_trading_enabled", False)),
        symbols=list(raw.get("symbols", ["BTC-USDT", "ETH-USDT"])),
        allowed_symbols=list(
            raw.get("allowed_symbols", ["BTC-USDT", "ETH-USDT", "SOL-USDT"])
        ),
        timeframe_signal=str(raw.get("timeframes", {}).get("signal", raw.get("timeframe_signal", "15m"))),
        timeframe_entry=str(raw.get("timeframes", {}).get("entry", raw.get("timeframe_entry", "5m"))),
        timeframe_trend=str(raw.get("timeframes", {}).get("trend", raw.get("timeframe_trend", "1H"))),
        max_risk_per_trade=float(raw.get("risk", {}).get("max_risk_per_trade", raw.get("max_risk_per_trade", 0.005))),
        max_daily_loss=float(raw.get("risk", {}).get("max_daily_loss", raw.get("max_daily_loss", 0.02))),
        max_positions=int(raw.get("risk", {}).get("max_positions", raw.get("max_positions", 2))),
        leverage=int(raw.get("risk", {}).get("max_leverage", raw.get("leverage", 1))),
        stop_after_consecutive_losses=int(
            raw.get("risk", {}).get(
                "stop_after_consecutive_losses",
                raw.get("stop_after_consecutive_losses", 3),
            )
        ),
        strategy_min_score=int(raw.get("strategy", {}).get("min_score", raw.get("strategy_min_score", 3))),
        require_volume_confirmation=bool(
            raw.get("strategy", {}).get(
                "require_volume_confirmation",
                raw.get("require_volume_confirmation", False),
            )
        ),
        require_manual_confirm=bool(raw.get("require_manual_confirm", True)),
        enable_short=bool(raw.get("enable_short", False)),
        enable_futures=bool(raw.get("enable_futures", False)),
        paper_futures_enabled=bool(raw.get("paper", {}).get("futures_enabled", True)),
        paper_max_leverage=int(raw.get("paper", {}).get("max_leverage", 100)),
        isolated_margin=bool(raw.get("isolated_margin", True)),
        spread_limit=float(raw.get("spread_limit", 0.0005)),
        atr_anomaly_multiplier=float(raw.get("atr_anomaly_multiplier", 2.5)),
        stop_loss_atr_multiplier=float(raw.get("stop_loss_atr_multiplier", 1.2)),
        take_profit_rr=float(raw.get("take_profit_rr", 1.8)),
        trailing_stop_enabled=bool(raw.get("trailing_stop_enabled", True)),
        trailing_stop_atr_multiplier=float(raw.get("trailing_stop_atr_multiplier", 1.0)),
        paper_starting_equity=float(raw.get("paper", {}).get("starting_equity", 10000)),
        taker_fee=float(raw.get("fees", {}).get("taker", 0.001)),
        maker_fee=float(raw.get("fees", {}).get("maker", 0.0008)),
        slippage=float(raw.get("slippage", {}).get("default", 0.0005)),
        okx_rest_base_url=str(raw.get("okx", {}).get("rest_base_url", "https://www.okx.com")),
        okx_public_ws_url=str(
            raw.get("okx", {}).get("public_ws_url", "wss://ws.okx.com:8443/ws/v5/public")
        ),
        okx_private_ws_url=str(
            raw.get("okx", {}).get("private_ws_url", "wss://ws.okx.com:8443/ws/v5/private")
        ),
        okx_demo_public_ws_url=str(
            raw.get("okx", {}).get(
                "demo_public_ws_url", "wss://wspap.okx.com:8443/ws/v5/public"
            )
        ),
        okx_demo_private_ws_url=str(
            raw.get("okx", {}).get(
                "demo_private_ws_url", "wss://wspap.okx.com:8443/ws/v5/private"
            )
        ),
        okx_use_demo_trading_header=bool(
            raw.get("okx", {}).get("use_demo_trading_header", True)
        ),
        request_timeout_seconds=int(raw.get("okx", {}).get("request_timeout_seconds", 10)),
        log_level=str(raw.get("logging", {}).get("level", "INFO")),
        log_directory=str(raw.get("logging", {}).get("directory", "logs")),
        enable_ai_summary=bool(raw.get("ai_advisor", {}).get("enable_ai_summary", True)),
        allow_ai_auto_order=bool(raw.get("ai_advisor", {}).get("allow_ai_auto_order", False)),
        enable_telegram=bool(raw.get("notification", {}).get("enable_telegram", False)),
        enable_email=bool(raw.get("notification", {}).get("enable_email", False)),
    )

    credentials = OKXCredentials(
        api_key=os.getenv("OKX_API_KEY") or None,
        api_secret=os.getenv("OKX_API_SECRET") or None,
        passphrase=os.getenv("OKX_API_PASSPHRASE") or None,
    )
    validate_config(cfg, credentials)
    return cfg, credentials


def validate_config(cfg: TradingConfig, credentials: OKXCredentials | None = None) -> None:
    if cfg.trading_mode == TradingMode.LIVE and not cfg.live_trading_enabled:
        raise ValueError("Live trading requires live_trading_enabled=true.")
    if cfg.trading_mode == TradingMode.LIVE and credentials and not credentials.complete:
        raise ValueError("Live trading requires complete OKX credentials in local env.")
    if cfg.enable_futures and cfg.leverage > 2:
        raise ValueError("Futures leverage must not exceed 2.")
    if cfg.paper_max_leverage > 100:
        raise ValueError("paper max leverage must not exceed 100.")
    if cfg.enable_futures and not cfg.isolated_margin:
        raise ValueError("Futures mode must use isolated margin.")
    if cfg.enable_short and not cfg.enable_futures:
        raise ValueError("Shorting requires enable_futures=true.")
    if cfg.max_risk_per_trade > 0.005:
        raise ValueError("max_risk_per_trade must be <= 0.005.")
    if cfg.max_daily_loss > 0.02:
        raise ValueError("max_daily_loss must be <= 0.02.")
    if cfg.max_positions > 2:
        raise ValueError("max_positions must be <= 2.")
    if cfg.allow_ai_auto_order:
        raise ValueError("AI auto order is not allowed.")
    unknown = set(cfg.symbols) - set(cfg.allowed_symbols)
    if unknown:
        raise ValueError(f"Symbols are not in allowed_symbols: {sorted(unknown)}")
