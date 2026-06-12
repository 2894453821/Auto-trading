from __future__ import annotations

from src.utils.config import TradingConfig
from src.utils.types import AccountSnapshot, CandidateSignal, OrderPlan, TradingAdvice


class AIAdvisor:
    def __init__(self, config: TradingConfig):
        self.config = config

    def generate(
        self,
        candidate: CandidateSignal,
        account: AccountSnapshot,
        order_plan: OrderPlan | None,
        risk_error: str | None = None,
    ) -> TradingAdvice:
        if candidate.decision != "trade" or order_plan is None:
            reasons = candidate.reasons or ["当前策略条件不足"]
            warnings = candidate.blockers.copy()
            if risk_error:
                warnings.append(risk_error)
            advice = TradingAdvice(
                symbol=candidate.symbol,
                action="暂不交易",
                signal_strength=candidate.signal_strength,
                risk_level="高" if warnings else "中",
                entry_price=None,
                stop_loss=None,
                take_profit=None,
                quantity=None,
                max_loss=None,
                risk_reward=None,
                reasons=reasons,
                risk_warnings=warnings,
                no_trade_reasons=warnings or ["信号强度不足，等待更清晰的入场结构"],
                worst_case_loss=None,
                requires_user_confirmation=False,
                text="",
            )
            return self._with_text(advice)

        risk_reward = abs(order_plan.take_profit - order_plan.entry_price) / abs(
            order_plan.entry_price - order_plan.stop_loss
        )
        risk_level = self._risk_level(candidate, account, order_plan)
        action = "做多" if order_plan.direction == "long" else "做空"
        warnings = [
            "这不是保证盈利，价格可能快速反向触发止损。",
            "若行情跳空或盘口变薄，实际亏损可能超过预估最大亏损。",
        ]
        if account.risk_exposure > account.equity * 0.4:
            warnings.append("当前风险暴露偏高，不建议继续增加仓位。")
        advice = TradingAdvice(
            symbol=candidate.symbol,
            action=action,
            signal_strength=candidate.signal_strength,
            risk_level=risk_level,
            entry_price=order_plan.entry_price,
            stop_loss=order_plan.stop_loss,
            take_profit=order_plan.take_profit,
            quantity=order_plan.quantity,
            max_loss=order_plan.max_loss,
            risk_reward=risk_reward,
            reasons=candidate.reasons,
            risk_warnings=warnings,
            no_trade_reasons=candidate.blockers
            or ["如果价格重新跌回/升回入场均线、成交量衰减或价差放大，应放弃交易。"],
            worst_case_loss=order_plan.max_loss,
            requires_user_confirmation=True,
            text="",
        )
        return self._with_text(advice)

    def _risk_level(
        self, candidate: CandidateSignal, account: AccountSnapshot, order_plan: OrderPlan
    ) -> str:
        exposure_after = account.risk_exposure + order_plan.quantity * order_plan.entry_price
        if exposure_after > account.equity * 0.5:
            return "高"
        if candidate.signal_strength == "强" and order_plan.max_loss <= account.equity * 0.003:
            return "低"
        return "中"

    def _with_text(self, advice: TradingAdvice) -> TradingAdvice:
        text = self._render(advice)
        return TradingAdvice(
            symbol=advice.symbol,
            action=advice.action,
            signal_strength=advice.signal_strength,
            risk_level=advice.risk_level,
            entry_price=advice.entry_price,
            stop_loss=advice.stop_loss,
            take_profit=advice.take_profit,
            quantity=advice.quantity,
            max_loss=advice.max_loss,
            risk_reward=advice.risk_reward,
            reasons=advice.reasons,
            risk_warnings=advice.risk_warnings,
            no_trade_reasons=advice.no_trade_reasons,
            worst_case_loss=advice.worst_case_loss,
            requires_user_confirmation=advice.requires_user_confirmation,
            text=text,
        )

    def _render(self, advice: TradingAdvice) -> str:
        def fmt(value: float | None, digits: int = 4) -> str:
            return "--" if value is None else f"{value:.{digits}f}"

        lines = [
            "【AI 交易建议】",
            f"交易对：{advice.symbol}",
            f"方向：{advice.action}",
            f"信号强度：{advice.signal_strength}",
            f"风险等级：{advice.risk_level}",
            f"建议入场价：{fmt(advice.entry_price)}",
            f"建议止损价：{fmt(advice.stop_loss)}",
            f"建议止盈价：{fmt(advice.take_profit)}",
            f"建议仓位：{fmt(advice.quantity, 8)}",
            f"最大亏损：{fmt(advice.max_loss, 2)}",
            f"风险收益比：{fmt(advice.risk_reward, 2)}",
            "",
            "【交易理由】",
        ]
        lines.extend(f"{idx}. {item}" for idx, item in enumerate(advice.reasons, 1))
        lines.extend(["", "【风险提示】"])
        lines.extend(f"{idx}. {item}" for idx, item in enumerate(advice.risk_warnings, 1))
        lines.extend(["", "【不做/放弃交易的理由】"])
        lines.extend(f"{idx}. {item}" for idx, item in enumerate(advice.no_trade_reasons, 1))
        lines.extend(
            [
                "",
                f"最坏情况下可能亏损：{fmt(advice.worst_case_loss, 2)}",
                "这不是保证盈利，最终是否下单必须由用户确认。",
                "",
                "【最终操作】",
                "1. 确认下单",
                "2. 放弃交易",
                "3. 只设置提醒",
                "4. 重新分析",
            ]
        )
        return "\n".join(lines)

