from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.account_monitor.monitor import AccountMonitor
from src.ai_advisor.advisor import AIAdvisor
from src.execution.paper_broker import PaperBroker
from src.market_data.okx_client import OKXRestClient
from src.market_data.service import MarketDataService
from src.notification.notifier import Notifier
from src.risk_manager.risk_manager import RiskManager
from src.indicator_engine.engine import IndicatorEngine
from src.strategy.indicators import atr, candles_to_frame
from src.strategy_engine.engine import StrategyEngine
from src.utils.config import OKXCredentials, TradingConfig
from src.utils.types import Candle, OrderPlan, Signal, TradingAdvice, TradingMode


class TradingWorkbench:
    def __init__(
        self,
        config: TradingConfig,
        credentials: OKXCredentials,
        logger: logging.Logger,
    ):
        self.config = config
        self.logger = logger
        self.client = OKXRestClient(config, credentials)
        self.market = MarketDataService(self.client)
        self.risk = RiskManager(config, config.paper_starting_equity)
        self.broker = PaperBroker(config=config, risk_manager=self.risk, logger=logger)
        self.account_monitor = AccountMonitor(config, self.client, self.risk, self.broker)
        self.strategy = StrategyEngine(config, self.market)
        self.indicators = IndicatorEngine()
        self.advisor = AIAdvisor(config)
        self.notifier = Notifier(config, logger)
        self.latest_advice: TradingAdvice | None = None
        self.latest_plan: OrderPlan | None = None
        self.paper_risk_override = False
        self._last_mark_candle_fetch: dict[str, float] = {}
        self.market.start_realtime(self.config.symbols, logger)

    def state(self) -> dict[str, Any]:
        self._refresh_paper_marks()
        positions = self._positions_with_live_pnl()
        allowed, reason = self.risk.can_open_new_position(self.broker.get_positions())
        account = self.account_monitor.snapshot()
        unrealized = sum(float(pos.get("unrealized_pnl", 0.0)) for pos in positions)
        margin_used = sum(float(pos.get("margin_used", 0.0)) for pos in positions)
        available_balance = max(account.equity + unrealized - margin_used, 0.0)
        return {
            "mode": self.config.trading_mode.value,
            "liveTradingEnabled": self.config.live_trading_enabled,
            "paperFuturesEnabled": self.config.paper_futures_enabled,
            "paperMaxLeverage": self.config.paper_max_leverage,
            "paperRiskOverride": self.paper_risk_override,
            "symbols": self.config.symbols,
            "allowedToOpen": allowed,
            "riskReason": reason,
            "equity": account.equity,
            "equityWithUnrealized": account.equity + unrealized,
            "availableBalance": available_balance,
            "dailyPnl": account.daily_pnl,
            "unrealizedPnl": unrealized,
            "marginUsed": margin_used,
            "riskExposure": account.risk_exposure,
            "consecutiveLosses": self.risk.state.consecutive_losses,
            "emergencyStopped": self.risk.state.emergency_stopped,
            "positions": positions,
            "openOrderCount": account.open_order_count,
            "latestAdvice": _to_plain(self.latest_advice) if self.latest_advice else None,
            "hasPendingAdviceOrder": self.latest_plan is not None,
            "realtimeMarket": self.market.realtime_status(),
        }

    def candles(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        return [asdict(candle) for candle in self.market.candles(symbol, timeframe, limit)]

    def snapshot(self, symbol: str) -> dict[str, Any]:
        return asdict(self.market.snapshot(symbol))

    def prediction(self, symbol: str) -> dict[str, Any]:
        snapshot = self.market.snapshot(symbol)
        trend_candles = self.market.candles(symbol, self.config.timeframe_trend, 120)
        signal_candles = self.market.candles(symbol, self.config.timeframe_signal, 120)
        entry_candles = self.market.candles(symbol, self.config.timeframe_entry, 120)
        trend = self.indicators.calculate(symbol, self.config.timeframe_trend, trend_candles, snapshot)
        signal = self.indicators.calculate(symbol, self.config.timeframe_signal, signal_candles, snapshot)
        entry = self.indicators.calculate(symbol, self.config.timeframe_entry, entry_candles, snapshot)
        bull_score = sum(
            [
                trend.ema20 >= trend.ema60,
                signal.ema20 > signal.ema60,
                entry.close > entry.ema20,
                entry.rsi >= 50,
                entry.macd_histogram >= 0,
            ]
        )
        bear_score = sum(
            [
                trend.ema20 <= trend.ema60,
                signal.ema20 < signal.ema60,
                entry.close < entry.ema20,
                entry.rsi <= 50,
                entry.macd_histogram <= 0,
            ]
        )
        if bull_score > bear_score:
            bias = "偏多"
            invalidation = min(entry.ema20, entry.support)
            trigger = "5m继续站上EMA20，且RSI保持50上方"
        elif bear_score > bull_score:
            bias = "偏空"
            invalidation = max(entry.ema20, entry.resistance)
            trigger = "5m继续跌破EMA20，且RSI保持50下方"
        else:
            bias = "震荡"
            invalidation = None
            trigger = "等待价格突破支撑/压力或均线重新排列"
        score_gap = abs(bull_score - bear_score)
        confidence = "高" if score_gap >= 3 else "中" if score_gap == 2 else "低"
        atr_15m = signal.atr
        atr_1h = trend.atr
        return {
            "symbol": symbol,
            "bias": bias,
            "confidence": confidence,
            "bullScore": bull_score,
            "bearScore": bear_score,
            "lastPrice": snapshot.last_price,
            "range15m": {
                "low": snapshot.last_price - atr_15m,
                "high": snapshot.last_price + atr_15m,
            },
            "range1h": {
                "low": snapshot.last_price - atr_1h,
                "high": snapshot.last_price + atr_1h,
            },
            "support": entry.support,
            "resistance": entry.resistance,
            "trigger": trigger,
            "invalidation": invalidation,
            "notes": [
                "预测不是保证盈利，只是基于EMA、RSI、MACD、ATR和支撑压力的概率倾向。",
                "如果价格触发止盈或止损，模拟仓位会自动结束。",
            ],
            "indicators": {
                "trend": _to_plain(trend),
                "signal": _to_plain(signal),
                "entry": _to_plain(entry),
            },
        }

    def analyze(self, symbol: str, leverage: int = 1) -> dict[str, Any]:
        leverage = self._sanitize_leverage(leverage)
        account = self.account_monitor.snapshot()
        candidate = self.strategy.analyze(symbol)
        plan = None
        risk_error = None
        if candidate.decision == "trade" and candidate.direction:
            try:
                signal = Signal(
                    symbol=candidate.symbol,
                    direction=candidate.direction,
                    entry_price=float(candidate.entry_price),
                    stop_loss=float(candidate.stop_loss),
                    take_profit=float(candidate.take_profit),
                    atr=float(candidate.atr),
                    reason="; ".join(candidate.reasons),
                )
                plan = self.risk.build_order_plan(
                    signal,
                    self.broker.get_positions(),
                    leverage=leverage,
                )
            except Exception as exc:
                risk_error = str(exc)
        advice = self.advisor.generate(candidate, account, plan, risk_error=risk_error)
        self.notifier.publish(advice)
        self.latest_advice = advice
        self.latest_plan = plan if advice.requires_user_confirmation else None
        return {
            "account": _to_plain(account),
            "candidate": _to_plain(candidate),
            "advice": _to_plain(advice),
            "orderPlan": _to_plain(plan) if plan else None,
            "riskError": risk_error,
        }

    def confirm_latest_advice(self, symbol: str) -> dict[str, Any]:
        if self.latest_plan is None or self.latest_advice is None:
            raise ValueError("no pending AI advice order to confirm")
        if self.latest_plan.symbol != symbol:
            raise ValueError("pending advice belongs to another symbol")
        order = self.broker.place_order(
            self.latest_plan,
            ignore_sizing_limits=self.paper_risk_override,
        )
        result = {
            "orderId": order.order_id,
            "plan": _to_plain(self.latest_plan),
            "status": order.status,
        }
        self.latest_plan = None
        return result

    def manual_open(
        self,
        symbol: str,
        direction: str,
        leverage: int,
        amount_usdt: float | None = None,
        quantity: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        entry_price: float | None = None,
        order_mode: str = "protected_market",
    ) -> dict[str, Any]:
        if direction not in ("long", "short"):
            raise ValueError("direction must be long or short")
        leverage = self._sanitize_leverage(leverage)
        if direction == "short" and not self.config.paper_futures_enabled:
            raise ValueError("paper short is disabled")
        snapshot = self.market.snapshot(symbol)
        entry_candles = self.market.candles(symbol, self.config.timeframe_entry, 80)
        current_atr = self._latest_atr(entry_candles)
        distance = self.config.stop_loss_atr_multiplier * current_atr
        if order_mode not in ("protected_market", "limit"):
            raise ValueError("order mode must be protected_market or limit")
        entry = entry_price if order_mode == "limit" and entry_price is not None else snapshot.last_price
        if entry <= 0:
            raise ValueError("entry price must be positive")
        has_manual_stop = stop_loss is not None or take_profit is not None
        if has_manual_stop and (stop_loss is None or take_profit is None):
            raise ValueError("manual stop loss and take profit must be set together")
        if direction == "long":
            signal = Signal(
                symbol=symbol,
                direction="long",
                entry_price=entry,
                stop_loss=stop_loss if stop_loss is not None else entry - distance,
                take_profit=take_profit if take_profit is not None else entry + distance * self.config.take_profit_rr,
                atr=current_atr,
                reason=(
                    f"manual paper long, {order_mode}, custom stop/take-profit"
                    if has_manual_stop
                    else f"manual paper long, {order_mode}, ATR stop/take-profit"
                ),
            )
        else:
            signal = Signal(
                symbol=symbol,
                direction="short",
                entry_price=entry,
                stop_loss=stop_loss if stop_loss is not None else entry + distance,
                take_profit=take_profit if take_profit is not None else entry - distance * self.config.take_profit_rr,
                atr=current_atr,
                reason=(
                    f"manual paper short, {order_mode}, custom stop/take-profit"
                    if has_manual_stop
                    else f"manual paper short, {order_mode}, ATR stop/take-profit"
                ),
            )
        if amount_usdt is not None and amount_usdt > 0:
            plan = self.risk.build_amount_order_plan(
                signal,
                self.broker.get_positions(),
                margin_amount=amount_usdt,
                leverage=leverage,
                ignore_sizing_limits=self.paper_risk_override,
            )
        elif quantity is not None and quantity > 0:
            risk_per_unit = abs(signal.entry_price - signal.stop_loss)
            notional = quantity * signal.entry_price
            margin = notional / max(leverage, 1)
            if margin > self.risk.state.equity * 0.5 and not self.paper_risk_override:
                raise ValueError("quantity margin exceeds 50% account cap")
            plan = OrderPlan(
                symbol=symbol,
                side="buy" if direction == "long" else "sell",
                direction=direction,
                quantity=quantity,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                max_loss=quantity * risk_per_unit,
                leverage=leverage,
            )
        else:
            plan = self.risk.build_order_plan(signal, self.broker.get_positions(), leverage=leverage)
        order = self.broker.place_order(
            plan,
            ignore_sizing_limits=self.paper_risk_override,
        )
        return {"orderId": order.order_id, "plan": _to_plain(plan), "status": order.status}

    def close_position(self, symbol: str, quantity: float | None = None) -> dict[str, Any]:
        snapshot = self.market.snapshot(symbol)
        pnl = self.broker.close_position(symbol, snapshot.last_price, quantity=quantity)
        return {"symbol": symbol, "price": snapshot.last_price, "quantity": quantity, "realizedPnl": pnl}

    def update_position_stops(self, symbol: str, stop_loss: float, take_profit: float) -> dict[str, Any]:
        count = self.broker.update_stops(symbol, stop_loss, take_profit)
        return {"symbol": symbol, "updated": count, "stopLoss": stop_loss, "takeProfit": take_profit}

    def reverse_position(
        self,
        symbol: str,
        leverage: int,
        amount_usdt: float | None = None,
        quantity: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        entry_price: float | None = None,
        order_mode: str = "protected_market",
    ) -> dict[str, Any]:
        positions = [pos for pos in self.broker.get_positions() if pos.symbol == symbol]
        if not positions:
            raise ValueError(f"no paper position found for {symbol}")
        current_direction = positions[0].direction
        new_direction = "short" if current_direction == "long" else "long"
        close_result = self.close_position(symbol)
        open_result = self.manual_open(
            symbol,
            new_direction,
            leverage,
            amount_usdt=amount_usdt,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_price=entry_price,
            order_mode=order_mode,
        )
        return {"closed": close_result, "opened": open_result}

    def cancel_all(self) -> dict[str, Any]:
        self.broker.cancel_all()
        return {"cancelled": True}

    def emergency_stop(self) -> dict[str, Any]:
        self.broker.emergency_stop()
        self.latest_plan = None
        return {"emergencyStopped": True}

    def set_available_balance(self, amount: float) -> dict[str, Any]:
        if amount <= 0:
            raise ValueError("available balance must be positive")
        positions = self._positions_with_live_pnl()
        unrealized = sum(float(pos.get("unrealized_pnl", 0.0)) for pos in positions)
        margin_used = sum(float(pos.get("margin_used", 0.0)) for pos in positions)
        new_equity = amount + margin_used - unrealized
        self.risk.set_paper_equity(new_equity)
        self.logger.warning("paper available balance manually set to %.2f", amount)
        return self.state()

    def set_risk_override(self, enabled: bool) -> dict[str, Any]:
        self.paper_risk_override = bool(enabled)
        self.logger.warning("paper risk override set to %s", self.paper_risk_override)
        return self.state()

    def _positions_with_live_pnl(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for pos in self.broker.get_positions():
            try:
                current = self.market.snapshot(pos.symbol).last_price
            except Exception:
                current = pos.entry_price
            if pos.direction == "long":
                pnl = (current - pos.entry_price) * pos.quantity
            else:
                pnl = (pos.entry_price - current) * pos.quantity
            notional = pos.quantity * pos.entry_price
            margin = notional / max(pos.leverage, 1)
            rows.append(
                {
                    **asdict(pos),
                    "current_price": current,
                    "notional": notional,
                    "margin_used": margin,
                    "unrealized_pnl": pnl,
                    "unrealized_pct": pnl / margin if margin > 0 else 0.0,
                }
            )
        return rows

    def _refresh_paper_marks(self) -> None:
        for symbol in {pos.symbol for pos in self.broker.get_positions()}:
            try:
                snapshot = self.market.snapshot(symbol)
                now = time.time()
                latest = None
                if now - self._last_mark_candle_fetch.get(symbol, 0.0) >= 5:
                    recent = self.market.candles(symbol, "1m", 2)
                    latest = recent[-1] if recent else None
                    self._last_mark_candle_fetch[symbol] = now
                self.broker.mark_price(
                    symbol,
                    snapshot.last_price,
                    high=latest.high if latest else None,
                    low=latest.low if latest else None,
                )
            except Exception as exc:
                self.logger.warning("paper mark failed symbol=%s error=%s", symbol, exc)

    def _latest_atr(self, candles: list[Candle]) -> float:
        if len(candles) < 20:
            raise ValueError("not enough candles for ATR")
        frame = candles_to_frame(candles)
        value = float(atr(frame).iloc[-1])
        if value <= 0:
            raise ValueError("invalid ATR")
        return value

    def _sanitize_leverage(self, leverage: int) -> int:
        return max(1, min(int(leverage), self.config.paper_max_leverage))


def run_ui_server(
    config: TradingConfig,
    credentials: OKXCredentials,
    logger: logging.Logger,
    host: str,
    port: int,
) -> None:
    if config.trading_mode == TradingMode.LIVE:
        raise RuntimeError("UI is disabled in live mode. Use paper/demo mode for this workbench.")
    workbench = TradingWorkbench(config, credentials, logger)

    class Handler(WorkbenchHandler):
        app = workbench

    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("UI server listening at http://%s:%s", host, port)
    try:
        server.serve_forever()
    finally:
        workbench.market.stop_realtime()
        workbench.client.close()


class WorkbenchHandler(BaseHTTPRequestHandler):
    app: TradingWorkbench

    def log_message(self, format: str, *args: Any) -> None:
        self.app.logger.info("ui " + format, *args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_html(INDEX_HTML)
                return
            if parsed.path == "/api/state":
                self._send_json(self.app.state())
                return
            if parsed.path == "/api/candles":
                query = parse_qs(parsed.query)
                symbol = query.get("symbol", [self.app.config.symbols[0]])[0]
                timeframe = query.get("timeframe", [self.app.config.timeframe_entry])[0]
                limit = int(query.get("limit", ["220"])[0])
                self._send_json({"candles": self.app.candles(symbol, timeframe, limit)})
                return
            if parsed.path == "/api/snapshot":
                symbol = parse_qs(parsed.query).get("symbol", [self.app.config.symbols[0]])[0]
                self._send_json({
                    "snapshot": self.app.snapshot(symbol),
                    "marketStatus": self.app.market.realtime_status(symbol),
                })
                return
            if parsed.path == "/api/analysis":
                query = parse_qs(parsed.query)
                symbol = query.get("symbol", [self.app.config.symbols[0]])[0]
                leverage = int(query.get("leverage", ["1"])[0])
                self._send_json(self.app.analyze(symbol, leverage))
                return
            if parsed.path == "/api/prediction":
                symbol = parse_qs(parsed.query).get("symbol", [self.app.config.symbols[0]])[0]
                self._send_json(self.app.prediction(symbol))
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
            symbol = str(body.get("symbol") or self.app.config.symbols[0])
            leverage = int(body.get("leverage", 1))
            amount_usdt = float(body.get("amountUsdt", 0) or 0)
            order_quantity = _optional_float(body.get("orderQty"), zero_as_none=True)
            stop_loss = _optional_float(body.get("stopLoss"), field_name="stop loss")
            take_profit = _optional_float(body.get("takeProfit"), field_name="take profit")
            entry_price = _optional_float(body.get("entryPrice"), zero_as_none=True)
            close_quantity = _optional_float(body.get("closeQuantity"), zero_as_none=True)
            order_mode = str(body.get("orderMode") or "protected_market")
            if parsed.path == "/api/advice":
                self._send_json(self.app.analyze(symbol, leverage))
                return
            if parsed.path == "/api/prediction":
                self._send_json(self.app.prediction(symbol))
                return
            if parsed.path == "/api/paper/confirm-advice":
                self._send_json(self.app.confirm_latest_advice(symbol))
                return
            if parsed.path == "/api/paper/manual-open":
                self._send_json(
                    self.app.manual_open(
                        symbol,
                        str(body.get("direction", "long")),
                        leverage,
                        amount_usdt=amount_usdt,
                        quantity=order_quantity,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        entry_price=entry_price,
                        order_mode=order_mode,
                    )
                )
                return
            if parsed.path == "/api/paper/close":
                self._send_json(self.app.close_position(symbol, quantity=close_quantity))
                return
            if parsed.path == "/api/paper/update-stops":
                if stop_loss is None or take_profit is None:
                    raise ValueError("stopLoss and takeProfit are required")
                self._send_json(self.app.update_position_stops(symbol, stop_loss, take_profit))
                return
            if parsed.path == "/api/paper/reverse":
                self._send_json(
                    self.app.reverse_position(
                        symbol,
                        leverage,
                        amount_usdt=amount_usdt,
                        quantity=order_quantity,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        entry_price=entry_price,
                        order_mode=order_mode,
                    )
                )
                return
            if parsed.path == "/api/orders/cancel-all":
                self._send_json(self.app.cancel_all())
                return
            if parsed.path == "/api/emergency-stop":
                self._send_json(self.app.emergency_stop())
                return
            if parsed.path == "/api/paper/set-available":
                self._send_json(self.app.set_available_balance(float(body.get("amount", 0))))
                return
            if parsed.path == "/api/paper/risk-override":
                self._send_json(self.app.set_risk_override(bool(body.get("enabled", False))))
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _to_plain(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


def _optional_float(
    value: Any,
    *,
    field_name: str = "value",
    zero_as_none: bool = False,
) -> float | None:
    if value is None or value == "":
        return None
    parsed = float(value)
    if parsed == 0 and zero_as_none:
        return None
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>郭郭赚大钱！</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #05070a;
      --panel: #0d1117;
      --panel2: #111722;
      --panel3: #151d2a;
      --line: #263241;
      --text: #e8edf4;
      --muted: #8491a3;
      --green: #22b66e;
      --red: #df4b61;
      --amber: #d6a13d;
      --blue: #4aa3ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      overflow-x: hidden;
      overflow-y: auto;
      background: var(--bg);
      color: var(--text);
      font-family: "Segoe UI", Arial, sans-serif;
      font-size: 13px;
      letter-spacing: 0;
    }
    header {
      height: 48px;
      display: grid;
      grid-template-columns: 220px 1fr 320px;
      align-items: center;
      gap: 10px;
      padding: 8px 12px;
      border-bottom: 1px solid var(--line);
      background: #080b10;
    }
    h1 { margin: 0; font-size: 18px; }
    h2 { margin: 0 0 8px; font-size: 13px; color: #cfd8e5; }
    .toolbar, .rightbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .rightbar { justify-content: flex-end; }
    select, button, input {
      height: 30px;
      border: 1px solid var(--line);
      background: var(--panel2);
      color: var(--text);
      border-radius: 5px;
      padding: 0 9px;
      font-size: 13px;
    }
    button {
      cursor: pointer;
      border-color: #334154;
      background: linear-gradient(#192232, #101722);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.06), 0 1px 2px rgba(0,0,0,.35);
      transition: transform .08s ease, border-color .12s ease, filter .12s ease;
    }
    button:hover { border-color: #6c7b92; filter: brightness(1.08); }
    button:active { transform: translateY(1px); box-shadow: inset 0 2px 4px rgba(0,0,0,.35); }
    button.primary { background: linear-gradient(#2ed481, #18a761); border-color: #32d987; color: #03120a; font-weight: 800; }
    button.short { background: linear-gradient(#f05a70, #c9324c); border-color: #f0697d; color: #190408; font-weight: 800; }
    button.warn { background: linear-gradient(#ecc057, #c68a22); border-color: #f0c763; color: #171104; font-weight: 800; }
    button.ghost { background: linear-gradient(#111722, #0c1119); }
    button:disabled { opacity: .45; cursor: not-allowed; }
    main {
      height: calc(100vh - 48px);
      min-height: 640px;
      display: grid;
      grid-template-columns: minmax(680px, 1fr) 330px 360px;
    }
    .left, .book, .trade { min-width: 0; border-right: 1px solid var(--line); background: #06090d; }
    .trade { border-right: 0; background: var(--panel); }
    .ticker {
      min-height: 48px;
      display: grid;
      grid-template-columns: repeat(7, minmax(80px, 1fr));
      gap: 8px;
      padding: 7px 10px;
      border-bottom: 1px solid var(--line);
      background: #090d13;
    }
    .metric span { display: block; color: var(--muted); font-size: 11px; }
    .metric strong { display: block; font-size: 14px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
    .chart-tools {
      height: 36px;
      display: flex;
      gap: 6px;
      align-items: center;
      padding: 5px 10px;
      border-bottom: 1px solid var(--line);
      background: #070a0f;
    }
    .chart-tools button.active { border-color: var(--blue); color: var(--blue); }
    .chart-wrap { height: calc(100vh - 48px - 48px - 36px - 180px); min-height: 330px; position: relative; }
    canvas { display: block; width: 100%; height: 100%; background: #030506; }
    .chart-tooltip {
      position: absolute;
      display: none;
      z-index: 5;
      min-width: 170px;
      padding: 8px 9px;
      border: 1px solid #3a4b61;
      border-radius: 6px;
      background: rgba(8, 12, 18, .94);
      box-shadow: 0 8px 22px rgba(0,0,0,.38);
      color: #dce5f2;
      pointer-events: none;
      font: 13px Consolas, "Microsoft YaHei", sans-serif;
      line-height: 1.45;
    }
    .log {
      height: 180px;
      overflow: auto;
      border-top: 1px solid var(--line);
      padding: 9px 10px;
      background: #05070a;
      font: 13px Consolas, monospace;
      color: #b8c4d4;
    }
    .book { display: grid; grid-template-rows: 1fr 190px; }
    .panel { padding: 10px; border-bottom: 1px solid var(--line); }
    .scroll { overflow: auto; }
    .kv { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; font-size: 13px; color: var(--muted); }
    .kv b { color: var(--text); text-align: right; overflow: hidden; text-overflow: ellipsis; }
    .positions { display: grid; gap: 8px; padding: 10px; }
    .position {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px;
      background: #0b1018;
      font-size: 13px;
      display: grid;
      gap: 5px;
    }
    .trade { display: grid; grid-template-rows: auto auto minmax(220px, 1fr) auto; }
    .tabs { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .tabs button { height: 34px; }
    .form { display: grid; gap: 8px; }
    .inline-form { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
    .form label { display: grid; gap: 4px; color: var(--muted); font-size: 13px; }
    .form input, .form select { width: 100%; }
    .order-line { display: grid; grid-template-columns: 1fr 86px; gap: 8px; }
    .percent-wrap { display: grid; gap: 4px; }
    input[type=range] {
      accent-color: var(--green);
      padding: 0;
      background: transparent;
      border: 0;
      height: 22px;
    }
    .percent-labels {
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      color: var(--muted);
      font-size: 12px;
      text-align: center;
    }
    .check-row { display: flex; align-items: center; gap: 6px; color: var(--text); font-size: 13px; }
    .check-row input { width: auto; height: auto; }
    .stop-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .buy-sell { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .buy-sell button { height: 42px; border-radius: 22px; font-size: 15px; }
    .advice {
      overflow: auto;
      padding: 10px;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 13px;
      line-height: 1.5;
      color: #d8e0ec;
      background: #080c12;
    }
    .empty { color: var(--muted); border: 1px dashed var(--line); padding: 10px; border-radius: 6px; }
    .up { color: var(--green); }
    .down { color: var(--red); }
    .muted { color: var(--muted); }
    @media (max-width: 1100px) {
      body { overflow: auto; }
      header { grid-template-columns: 1fr; height: auto; }
      main { grid-template-columns: 1fr; height: auto; }
      .left, .book { border-right: 0; border-bottom: 1px solid var(--line); }
      .chart-wrap { height: 430px; }
      .ticker { grid-template-columns: repeat(2, minmax(130px, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <h1>郭郭赚大钱！</h1>
    <div class="toolbar">
      <select id="symbol"></select>
      <button data-tf="1m">1m</button>
      <button data-tf="5m" class="active">5m</button>
      <button data-tf="15m">15m</button>
      <button data-tf="1H">1h</button>
      <button id="zoomIn">放大K线</button>
      <button id="zoomOut">缩小K线</button>
      <button id="resetZoom">重置</button>
    </div>
    <div class="rightbar">
      <button id="refresh">刷新</button>
      <button id="toggle" class="ghost">暂停</button>
      <span id="mode" class="muted">paper</span>
    </div>
  </header>
  <main>
    <section class="left">
      <div class="ticker">
        <div class="metric"><span>最新价</span><strong id="last">--</strong></div>
        <div class="metric"><span>买卖价差</span><strong id="spread">--</strong></div>
        <div class="metric"><span>24h成交量</span><strong id="vol">--</strong></div>
        <div class="metric"><span>权益</span><strong id="equity">--</strong></div>
        <div class="metric"><span>浮动盈亏</span><strong id="unrealized">--</strong></div>
        <div class="metric"><span>今日PnL</span><strong id="daily">--</strong></div>
        <div class="metric"><span>占用保证金</span><strong id="margin">--</strong></div>
        <div class="metric"><span>行情源</span><strong id="marketSource">--</strong></div>
      </div>
      <div class="chart-tools">
        <span class="muted">K线缩放只影响图表区域，不会改变右侧面板大小。</span>
      </div>
      <div class="chart-wrap"><canvas id="chart"></canvas><div id="chartTooltip" class="chart-tooltip"></div></div>
      <div class="log" id="log"></div>
    </section>
    <section class="book">
      <div class="scroll">
        <div class="panel">
          <h2>账户与风控</h2>
          <div class="kv">
            <span>开仓状态</span><b id="riskReason">--</b>
            <span>可用资金</span><b id="available">--</b>
            <span>风险暴露</span><b id="exposure">--</b>
            <span>持仓数量</span><b id="posCount">0</b>
            <span>挂单数量</span><b id="openOrders">0</b>
            <span>连续亏损</span><b id="losses">0</b>
            <span>急停状态</span><b id="estop">false</b>
            <span>资金限制</span><b id="riskOverride">正常</b>
          </div>
          <div class="inline-form" style="margin-top:10px">
            <input id="availableInput" type="number" min="1" step="100" placeholder="设置可用资金 USDT">
            <button id="setAvailable" class="warn">设置</button>
          </div>
        </div>
        <div class="panel">
          <h2>指标快照</h2>
          <div class="kv" id="indicatorBox"><span>状态</span><b>暂无</b></div>
        </div>
        <div class="panel">
          <h2>预测面板</h2>
          <button id="predict" class="warn" style="width:100%;margin-bottom:8px">刷新预测</button>
          <div class="kv" id="predictionBox">
            <span>方向</span><b>--</b>
            <span>信心</span><b>--</b>
          </div>
        </div>
        <div class="positions" id="positions"></div>
      </div>
      <div class="panel">
        <h2>控制</h2>
        <div class="form">
          <button id="analysis" class="primary">生成AI建议</button>
          <button id="confirmAdvice" class="warn">确认建议下单</button>
          <button id="riskOverrideBtn" class="short">取消资金限制</button>
          <button id="cancelAll">撤销挂单</button>
          <button id="emergency" class="short">EMERGENCY STOP</button>
        </div>
      </div>
    </section>
    <section class="trade">
      <div class="panel">
        <h2>模拟合约下单</h2>
        <div class="form">
          <label>价格 (USDT)
            <div class="order-line">
              <input id="orderPrice" type="number" step="0.01" placeholder="当前市价">
              <button id="bestPrice">最优价</button>
            </div>
          </label>
          <label>订单类型
            <select id="orderMode">
              <option value="protected_market">市价保护</option>
              <option value="limit">限价模拟</option>
            </select>
          </label>
          <label>数量
            <input id="orderQty" type="number" step="0.000001" placeholder="输入数量，或用金额自动计算">
          </label>
          <div class="percent-wrap">
            <input id="amountPercent" type="range" min="0" max="100" step="25" value="0">
            <div class="percent-labels"><span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span></div>
          </div>
          <label>金额 (USDT)
            <input id="amountUsdt" type="number" min="0" step="1" placeholder="输入保证金金额">
          </label>
          <div class="kv">
            <span>可用</span><b><span id="orderAvailable">0.00</span> USDT</b>
            <span>可买/可卖</span><b id="maxQty">0</b>
            <span>预计盈利</span><b id="expectedProfit">--</b>
            <span>预计亏损</span><b id="expectedLoss">--</b>
            <span>风险收益比</span><b id="expectedRR">--</b>
          </div>
          <label class="check-row"><input id="useStops" type="checkbox" checked> 止盈/止损</label>
          <div class="stop-grid">
            <label>手动止损价
              <input id="manualStopLoss" type="number" min="0" step="0.01" placeholder="留空=ATR自动">
            </label>
            <label>手动止盈价
              <input id="manualTakeProfit" type="number" min="0" step="0.01" placeholder="留空=ATR自动">
            </label>
          </div>
        </div>
      </div>
      <div class="panel form">
        <label>杠杆
          <select id="leverage">
            <option value="1">1x</option>
            <option value="2">2x</option>
            <option value="10">10x</option>
            <option value="50">50x</option>
            <option value="100">100x</option>
          </select>
        </label>
        <label>模式
          <input value="模拟逐仓，ATR自动止盈止损" disabled>
        </label>
        <div class="buy-sell">
          <button id="manualLong" class="primary">买入 / 做多</button>
          <button id="manualShort" class="short">卖出 / 做空</button>
        </div>
        <div class="order-line">
          <input id="closeQty" type="number" min="0" step="0.000001" placeholder="平仓数量，留空=全部">
          <button id="closeHalf">平半仓</button>
        </div>
        <button id="updateStops" class="warn">修改当前仓位止盈止损</button>
        <button id="reversePosition" class="short">一键反手</button>
        <button id="closePosition">平当前币种仓位</button>
      </div>
      <pre class="advice" id="adviceBox">AI建议会显示在这里。当前交易台只执行模拟盘。</pre>
      <div class="panel muted">实盘交易仍然关闭。手动做多/做空使用OKX真实行情价格，但只在本地模拟盘成交。高杠杆仅用于模拟测试。</div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let symbols = [];
    let timeframe = "5m";
    let autoRefresh = true;
    let candles = [];
    let visibleCount = 140;
    let chartView = null;
    let latestAvailable = 0;
    let latestPrice = 0;
    let lastCandleLoad = 0;
    const candleRefreshMs = 3000;

    function log(message, kind = "") {
      const line = document.createElement("div");
      const ts = new Date().toLocaleTimeString();
      line.textContent = `[${ts}] ${message}`;
      if (kind) line.className = kind;
      $("log").prepend(line);
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || response.statusText);
      return data;
    }

    function money(value, digits = 2) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
      return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
    }

    function pct(value) {
      return `${(Number(value || 0) * 100).toFixed(2)}%`;
    }

    async function loadState() {
      const state = await api("/api/state");
      $("mode").textContent = `${state.mode} | 模拟合约 ${state.paperFuturesEnabled ? "开启" : "关闭"}`;
      $("equity").textContent = money(state.equityWithUnrealized);
      $("available").textContent = money(state.availableBalance);
      latestAvailable = Number(state.availableBalance || 0);
      $("orderAvailable").textContent = money(latestAvailable);
      $("availableInput").placeholder = `当前 ${money(state.availableBalance)} USDT`;
      $("daily").textContent = money(state.dailyPnl);
      $("daily").className = Number(state.dailyPnl) >= 0 ? "up" : "down";
      $("unrealized").textContent = money(state.unrealizedPnl);
      $("unrealized").className = Number(state.unrealizedPnl) >= 0 ? "up" : "down";
      $("margin").textContent = money(state.marginUsed);
      $("riskReason").textContent = state.riskReason;
      $("losses").textContent = state.consecutiveLosses;
      $("posCount").textContent = state.positions.length;
      $("exposure").textContent = money(state.riskExposure);
      $("openOrders").textContent = state.openOrderCount;
      $("estop").textContent = state.emergencyStopped;
      $("riskOverride").textContent = state.paperRiskOverride ? "已取消" : "正常";
      $("riskOverride").className = state.paperRiskOverride ? "down" : "up";
      $("riskOverrideBtn").textContent = state.paperRiskOverride ? "恢复资金限制" : "取消资金限制";
      $("riskOverrideBtn").className = state.paperRiskOverride ? "warn" : "short";
      if (state.realtimeMarket) {
        const age = state.realtimeMarket.ageSeconds;
        $("marketSource").textContent = `${state.realtimeMarket.source}${age === null ? "" : " " + age.toFixed(1) + "s"}`;
        $("marketSource").className = state.realtimeMarket.connected ? "up" : "down";
      }
      $("confirmAdvice").disabled = !state.hasPendingAdviceOrder;
      if (!symbols.length) {
        symbols = state.symbols;
        $("symbol").innerHTML = symbols.map(s => `<option value="${s}">${s}</option>`).join("");
      }
      if (state.latestAdvice) $("adviceBox").textContent = state.latestAdvice.text;
      renderPositions(state.positions);
      updateOrderEstimate();
    }

    function renderPositions(positions) {
      const root = $("positions");
      if (!positions.length) {
        root.innerHTML = `<div class="empty">暂无模拟持仓</div>`;
        return;
      }
      root.innerHTML = positions.map(pos => {
        const cls = Number(pos.unrealized_pnl) >= 0 ? "up" : "down";
        return `
          <div class="position">
            <b>${pos.symbol} ${pos.direction === "long" ? "做多" : "做空"} ${pos.leverage}x</b>
            <span>数量 ${money(pos.quantity, 8)} | 入场 ${money(pos.entry_price, 4)} | 标记 ${money(pos.current_price, 4)}</span>
            <span>止损 ${money(pos.stop_loss, 4)} | 止盈 ${money(pos.take_profit, 4)}</span>
            <span>保证金 ${money(pos.margin_used)} | 名义价值 ${money(pos.notional)}</span>
            <span class="${cls}">浮动盈亏 ${money(pos.unrealized_pnl)} (${pct(pos.unrealized_pct)})</span>
          </div>`;
      }).join("");
    }

    async function loadMarket(forceCandles = false) {
      const symbol = $("symbol").value || symbols[0] || "BTC-USDT";
      const shouldLoadCandles = forceCandles || !candles.length || Date.now() - lastCandleLoad >= candleRefreshMs;
      let snap;
      if (shouldLoadCandles) {
        const [k, s] = await Promise.all([
          api(`/api/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=260`),
          api(`/api/snapshot?symbol=${encodeURIComponent(symbol)}`)
        ]);
        candles = k.candles;
        snap = s;
        lastCandleLoad = Date.now();
      } else {
        snap = await api(`/api/snapshot?symbol=${encodeURIComponent(symbol)}`);
      }
      latestPrice = Number(snap.snapshot.last_price || 0);
      $("last").textContent = money(snap.snapshot.last_price, 4);
      if (snap.marketStatus) {
        const age = snap.marketStatus.ageSeconds;
        $("marketSource").textContent = `${snap.marketStatus.source}${age === null ? "" : " " + age.toFixed(1) + "s"}`;
        $("marketSource").className = snap.marketStatus.connected ? "up" : "down";
      }
      if (!$("orderPrice").value) $("orderPrice").value = latestPrice ? latestPrice.toFixed(2) : "";
      $("spread").textContent = `${(snap.snapshot.spread_pct * 100).toFixed(4)}%`;
      $("vol").textContent = money(snap.snapshot.volume_24h, 2);
      updateOrderEstimate();
      drawChart();
    }

    function formatTime(ts) {
      const d = new Date(Number(ts));
      if (timeframe === "1m" || timeframe === "5m" || timeframe === "15m") {
        return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      }
      return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:00`;
    }

    function drawChart() {
      const canvas = $("chart");
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(rect.width * dpr);
      canvas.height = Math.floor(rect.height * dpr);
      const ctx = canvas.getContext("2d");
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, rect.width, rect.height);
      ctx.fillStyle = "#030506";
      ctx.fillRect(0, 0, rect.width, rect.height);
      if (!candles.length) return;
      const data = candles.slice(-visibleCount);
      const pad = { left: 60, right: 78, top: 18, bottom: 38 };
      const w = rect.width - pad.left - pad.right;
      const h = rect.height - pad.top - pad.bottom;
      const highs = data.map(c => c.high);
      const lows = data.map(c => c.low);
      const max = Math.max(...highs);
      const min = Math.min(...lows);
      const range = Math.max(max - min, 1e-9);
      const xStep = w / data.length;
      const y = (price) => pad.top + (max - price) / range * h;
      chartView = { data, pad, w, h, max, min, range, xStep, rect };
      ctx.strokeStyle = "#13202d";
      ctx.lineWidth = 1;
      ctx.font = "12px Segoe UI";
      ctx.fillStyle = "#8290a3";
      for (let i = 0; i <= 4; i++) {
        const yy = pad.top + h * i / 4;
        ctx.beginPath();
        ctx.moveTo(pad.left, yy);
        ctx.lineTo(rect.width - pad.right, yy);
        ctx.stroke();
        const price = max - range * i / 4;
        ctx.fillText(price.toFixed(2), rect.width - pad.right + 8, yy + 4);
      }
      const timeTicks = Math.min(6, data.length);
      for (let i = 0; i < timeTicks; i++) {
        const index = Math.floor(i * (data.length - 1) / Math.max(timeTicks - 1, 1));
        const x = pad.left + index * xStep + xStep / 2;
        ctx.fillStyle = "#8290a3";
        ctx.fillText(formatTime(data[index].timestamp), x - 24, rect.height - 14);
        ctx.strokeStyle = "#101923";
        ctx.beginPath();
        ctx.moveTo(x, pad.top);
        ctx.lineTo(x, rect.height - pad.bottom);
        ctx.stroke();
      }
      data.forEach((c, i) => {
        const x = pad.left + i * xStep + xStep / 2;
        const open = y(c.open), close = y(c.close), high = y(c.high), low = y(c.low);
        const up = c.close >= c.open;
        ctx.strokeStyle = up ? "#22b66e" : "#df4b61";
        ctx.fillStyle = ctx.strokeStyle;
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        ctx.moveTo(x, high);
        ctx.lineTo(x, low);
        ctx.stroke();
        const bodyTop = Math.min(open, close);
        const bodyH = Math.max(Math.abs(close - open), 1.6);
        const bodyW = Math.max(Math.min(xStep * 0.78, 14), 4);
        ctx.fillRect(x - bodyW / 2, bodyTop, bodyW, bodyH);
      });
    }

    function renderChartHover(evt) {
      if (!chartView) return;
      const canvas = $("chart");
      const bounds = canvas.getBoundingClientRect();
      const x = evt.clientX - bounds.left;
      const yPos = evt.clientY - bounds.top;
      const { data, pad, xStep, max, range, rect } = chartView;
      const index = Math.round((x - pad.left - xStep / 2) / xStep);
      const tooltip = $("chartTooltip");
      if (index < 0 || index >= data.length || x < pad.left || x > rect.width - pad.right) {
        tooltip.style.display = "none";
        drawChart();
        return;
      }
      const c = data[index];
      drawChart();
      const ctx = canvas.getContext("2d");
      ctx.save();
      const candleX = pad.left + index * xStep + xStep / 2;
      ctx.strokeStyle = "rgba(120, 150, 190, .55)";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(candleX, pad.top);
      ctx.lineTo(candleX, rect.height - pad.bottom);
      ctx.moveTo(pad.left, yPos);
      ctx.lineTo(rect.width - pad.right, yPos);
      ctx.stroke();
      ctx.restore();
      const hoverPrice = max - ((yPos - pad.top) / Math.max(chartView.h, 1)) * range;
      tooltip.innerHTML = `
        <b>${formatTime(c.timestamp)}</b><br>
        开 ${money(c.open, 4)} 高 ${money(c.high, 4)}<br>
        低 ${money(c.low, 4)} 收 ${money(c.close, 4)}<br>
        量 ${money(c.volume, 2)}<br>
        指针价 ${money(hoverPrice, 4)}
      `;
      tooltip.style.display = "block";
      const left = Math.min(Math.max(candleX + 12, 8), rect.width - 190);
      const top = Math.min(Math.max(yPos + 12, 8), rect.height - 120);
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    }

    function renderIndicators(candidate) {
      const indicators = candidate.indicators || {};
      const entry = indicators["5m"] || indicators["1m"] || Object.values(indicators)[0];
      if (!entry) {
        $("indicatorBox").innerHTML = `<span>状态</span><b>暂无</b>`;
        return;
      }
      $("indicatorBox").innerHTML = `
        <span>EMA20</span><b>${money(entry.ema20, 4)}</b>
        <span>EMA60</span><b>${money(entry.ema60, 4)}</b>
        <span>RSI</span><b>${money(entry.rsi, 2)}</b>
        <span>MACD</span><b>${money(entry.macd, 4)}</b>
        <span>ATR</span><b>${money(entry.atr, 4)}</b>
        <span>量/均量</span><b>${money(entry.volume, 2)} / ${money(entry.volume_avg20, 2)}</b>
        <span>支撑</span><b>${money(entry.support, 4)}</b>
        <span>压力</span><b>${money(entry.resistance, 4)}</b>
        <span>波动率</span><b>${money(entry.volatility * 100, 3)}%</b>
        <span>高/低</span><b>${money(entry.recent_high, 4)} / ${money(entry.recent_low, 4)}</b>
      `;
    }

    function renderPrediction(data) {
      $("predictionBox").innerHTML = `
        <span>方向</span><b>${data.bias}</b>
        <span>信心</span><b>${data.confidence}</b>
        <span>多/空评分</span><b>${data.bullScore} / ${data.bearScore}</b>
        <span>15m区间</span><b>${money(data.range15m.low, 4)} - ${money(data.range15m.high, 4)}</b>
        <span>1h区间</span><b>${money(data.range1h.low, 4)} - ${money(data.range1h.high, 4)}</b>
        <span>支撑</span><b>${money(data.support, 4)}</b>
        <span>压力</span><b>${money(data.resistance, 4)}</b>
        <span>触发</span><b>${data.trigger}</b>
        <span>失效</span><b>${data.invalidation === null ? "--" : money(data.invalidation, 4)}</b>
      `;
    }

    async function refreshAll() {
      try {
        await loadState();
        await loadMarket();
      } catch (err) {
        log(err.message, "down");
      }
    }

    async function postAction(path, body = {}) {
      try {
        const symbol = $("symbol").value;
        const leverage = Number($("leverage").value || 1);
        const amountUsdt = Number($("amountUsdt").value || 0);
        const orderQty = Number($("orderQty").value || 0);
        const orderMode = $("orderMode") ? $("orderMode").value : "protected_market";
        const entryPrice = Number($("orderPrice").value || latestPrice || 0);
        const data = await api(path, { method: "POST", body: JSON.stringify({ symbol, leverage, amountUsdt, orderQty, orderMode, entryPrice, ...body }) });
        log(`${path} ${JSON.stringify(data)}`, "up");
        await refreshAll();
        return data;
      } catch (err) {
        log(err.message, "down");
      }
    }

    function getManualStops(direction) {
      const stopText = $("manualStopLoss").value.trim();
      const takeText = $("manualTakeProfit").value.trim();
      if (!stopText && !takeText) return {};
      if (!stopText || !takeText) {
        log("手动止损价和手动止盈价必须一起填写；不填则使用ATR自动止盈止损", "down");
        return null;
      }
      const stopLoss = Number(stopText);
      const takeProfit = Number(takeText);
      const entry = Number($("orderPrice").value || latestPrice || 0);
      if (!stopLoss || !takeProfit || stopLoss <= 0 || takeProfit <= 0) {
        log("手动止损价和止盈价必须大于0", "down");
        return null;
      }
      if (entry > 0 && direction === "long" && !(stopLoss < entry && entry < takeProfit)) {
        log("做多需要满足：止损价 < 当前价 < 止盈价", "down");
        return null;
      }
      if (entry > 0 && direction === "short" && !(takeProfit < entry && entry < stopLoss)) {
        log("做空需要满足：止盈价 < 当前价 < 止损价", "down");
        return null;
      }
      return { stopLoss, takeProfit };
    }

    function orderEstimate(direction) {
      const price = Number($("orderPrice").value || latestPrice || 0);
      const qty = Number($("orderQty").value || 0);
      const stopLoss = Number($("manualStopLoss").value || 0);
      const takeProfit = Number($("manualTakeProfit").value || 0);
      if (!price || !qty || !stopLoss || !takeProfit) {
        return { profit: null, loss: null, rr: null };
      }
      const profit = direction === "long" ? (takeProfit - price) * qty : (price - takeProfit) * qty;
      const loss = direction === "long" ? (price - stopLoss) * qty : (stopLoss - price) * qty;
      return {
        profit,
        loss,
        rr: loss > 0 ? profit / loss : null,
      };
    }

    function updateOrderEstimate() {
      const leverage = Number($("leverage").value || 1);
      const price = Number($("orderPrice").value || latestPrice || 0);
      const amount = Number($("amountUsdt").value || 0);
      if (price > 0) {
        const maxQty = latestAvailable * leverage / price;
        $("maxQty").textContent = money(maxQty, 8);
        if (amount > 0) $("orderQty").value = (amount * leverage / price).toFixed(8);
      } else {
        $("maxQty").textContent = "0";
      }
      const direction = $("manualShort") && $("manualShort").classList.contains("active") ? "short" : "long";
      const estimate = orderEstimate(direction);
      $("expectedProfit").textContent = estimate.profit === null ? "--" : `${money(Math.max(estimate.profit, 0), 2)} USDT`;
      $("expectedLoss").textContent = estimate.loss === null ? "--" : `${money(Math.max(estimate.loss, 0), 2)} USDT`;
      $("expectedRR").textContent = estimate.rr === null ? "--" : `1:${money(Math.max(estimate.rr, 0), 2)}`;
    }

    function manualOrderSummary(direction, stops) {
      const symbol = $("symbol").value;
      const leverage = Number($("leverage").value || 1);
      const amount = Number($("amountUsdt").value || 0);
      const qty = Number($("orderQty").value || 0);
      const entry = Number($("orderPrice").value || latestPrice || 0);
      const estimate = orderEstimate(direction);
      const stopText = stops.stopLoss ? money(stops.stopLoss, 4) : "ATR自动";
      const takeText = stops.takeProfit ? money(stops.takeProfit, 4) : "ATR自动";
      return [
        "确认模拟下单？",
        `交易对：${symbol}`,
        `方向：${direction === "long" ? "做多" : "做空"}`,
        `订单类型：${$("orderMode").value === "limit" ? "限价模拟" : "市价保护"}`,
        `入场参考：${money(entry, 4)}`,
        `数量：${qty ? money(qty, 8) : "按风控计算"}`,
        `保证金：${amount ? money(amount, 2) + " USDT" : "按风控计算"}`,
        `杠杆：${leverage}x`,
        `止损：${stopText}`,
        `止盈：${takeText}`,
        `预计盈利：${estimate.profit === null ? "--" : money(Math.max(estimate.profit, 0), 2) + " USDT"}`,
        `预计亏损：${estimate.loss === null ? "--" : money(Math.max(estimate.loss, 0), 2) + " USDT"}`,
      ].join("\\n");
    }

    document.querySelectorAll("[data-tf]").forEach(btn => {
      btn.onclick = () => {
        timeframe = btn.dataset.tf;
        document.querySelectorAll("[data-tf]").forEach(b => b.classList.toggle("active", b === btn));
        lastCandleLoad = 0;
        loadMarket(true);
      };
    });
    $("zoomIn").onclick = () => { visibleCount = Math.max(40, Math.floor(visibleCount * 0.75)); drawChart(); };
    $("zoomOut").onclick = () => { visibleCount = Math.min(260, Math.floor(visibleCount * 1.3)); drawChart(); };
    $("resetZoom").onclick = () => { visibleCount = 140; drawChart(); };
    $("refresh").onclick = refreshAll;
    $("bestPrice").onclick = () => {
      if (latestPrice) {
        $("orderPrice").value = latestPrice.toFixed(2);
        updateOrderEstimate();
      }
    };
    $("amountPercent").oninput = () => {
      const pct = Number($("amountPercent").value || 0) / 100;
      $("amountUsdt").value = (latestAvailable * pct).toFixed(2);
      updateOrderEstimate();
    };
    $("amountUsdt").oninput = updateOrderEstimate;
    $("orderPrice").oninput = updateOrderEstimate;
    $("orderQty").oninput = updateOrderEstimate;
    $("manualStopLoss").oninput = updateOrderEstimate;
    $("manualTakeProfit").oninput = updateOrderEstimate;
    $("orderMode").onchange = updateOrderEstimate;
    $("leverage").onchange = updateOrderEstimate;
    $("toggle").onclick = () => {
      autoRefresh = !autoRefresh;
      $("toggle").textContent = autoRefresh ? "暂停" : "继续";
    };
    $("symbol").onchange = () => {
      candles = [];
      lastCandleLoad = 0;
      refreshAll();
    };
    $("analysis").onclick = async () => {
      try {
        const data = await postAction("/api/advice");
        if (!data) return;
        $("adviceBox").textContent = data.advice.text;
        renderIndicators(data.candidate);
        log(`AI建议：${data.advice.action}，风险 ${data.advice.risk_level}`);
      } catch (err) {
        log(err.message, "down");
      }
    };
    $("predict").onclick = async () => {
      try {
        const symbol = $("symbol").value;
        const data = await api("/api/prediction", {
          method: "POST",
          body: JSON.stringify({ symbol })
        });
        renderPrediction(data);
        log(`预测：${data.bias}，信心 ${data.confidence}`);
      } catch (err) {
        log(err.message, "down");
      }
    };
    $("confirmAdvice").onclick = () => postAction("/api/paper/confirm-advice");
    $("manualLong").onclick = () => {
      $("manualLong").classList.add("active");
      $("manualShort").classList.remove("active");
      updateOrderEstimate();
      if (!$("useStops").checked) {
        log("当前系统要求每笔模拟单必须带止盈/止损", "down");
        return;
      }
      const stops = getManualStops("long");
      if (!stops) return;
      if (!confirm(manualOrderSummary("long", stops))) return;
      postAction("/api/paper/manual-open", { direction: "long", ...stops });
    };
    $("manualShort").onclick = () => {
      $("manualShort").classList.add("active");
      $("manualLong").classList.remove("active");
      updateOrderEstimate();
      if (!$("useStops").checked) {
        log("当前系统要求每笔模拟单必须带止盈/止损", "down");
        return;
      }
      const stops = getManualStops("short");
      if (!stops) return;
      if (!confirm(manualOrderSummary("short", stops))) return;
      postAction("/api/paper/manual-open", { direction: "short", ...stops });
    };
    $("closePosition").onclick = () => {
      const closeQuantity = Number($("closeQty").value || 0) || undefined;
      if (!confirm(closeQuantity ? `确认平仓数量 ${closeQuantity}？` : "确认平掉当前币种全部仓位？")) return;
      postAction("/api/paper/close", { closeQuantity });
    };
    $("closeHalf").onclick = () => {
      const symbol = $("symbol").value;
      const rows = Array.from(document.querySelectorAll(".position"));
      const text = rows.map(row => row.textContent || "").find(item => item.includes(symbol)) || "";
      const match = text.match(/数量\\s+([0-9.]+)/);
      if (!match) {
        log("没有找到当前币种持仓数量", "down");
        return;
      }
      const closeQuantity = Number(match[1]) / 2;
      if (!confirm(`确认平半仓：${money(closeQuantity, 8)}？`)) return;
      postAction("/api/paper/close", { closeQuantity });
    };
    $("updateStops").onclick = () => {
      const stopLoss = Number($("manualStopLoss").value || 0);
      const takeProfit = Number($("manualTakeProfit").value || 0);
      if (!stopLoss || !takeProfit) {
        log("请先填写手动止损价和手动止盈价", "down");
        return;
      }
      if (!confirm(`确认修改当前币种仓位止盈止损？\\n止损：${money(stopLoss, 4)}\\n止盈：${money(takeProfit, 4)}`)) return;
      postAction("/api/paper/update-stops", { stopLoss, takeProfit });
    };
    $("reversePosition").onclick = () => {
      const currentShort = $("manualShort").classList.contains("active");
      const newDirection = currentShort ? "long" : "short";
      const stops = getManualStops(newDirection);
      if (!stops) return;
      if (!confirm(`确认一键反手为${newDirection === "long" ? "做多" : "做空"}？\\n系统会先平当前币种仓位，再开反向模拟仓。`)) return;
      postAction("/api/paper/reverse", { ...stops });
    };
    $("cancelAll").onclick = () => postAction("/api/orders/cancel-all");
    $("emergency").onclick = () => postAction("/api/emergency-stop");
    $("riskOverrideBtn").onclick = async () => {
      const enabled = $("riskOverride").textContent !== "已取消";
      await postAction("/api/paper/risk-override", { enabled });
      log(enabled ? "已取消模拟盘资金限制" : "已恢复模拟盘资金限制", enabled ? "down" : "up");
    };
    $("setAvailable").onclick = async () => {
      const amount = Number($("availableInput").value);
      if (!amount || amount <= 0) {
        log("请输入大于0的可用资金", "down");
        return;
      }
      await postAction("/api/paper/set-available", { amount });
      $("availableInput").value = "";
    };

    $("chart").addEventListener("mousemove", renderChartHover);
    $("chart").addEventListener("mouseleave", () => {
      $("chartTooltip").style.display = "none";
      drawChart();
    });

    window.addEventListener("resize", drawChart);
    refreshAll();
    setInterval(() => { if (autoRefresh) refreshAll(); }, 1000);
  </script>
</body>
</html>"""
