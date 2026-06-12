from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.utils.types import Position


@dataclass(frozen=True)
class DailyReport:
    date: str
    total_pnl: float
    fees: float
    win_rate: float
    max_drawdown: float
    positions: list[Position]

    def to_markdown(self) -> str:
        lines = [
            f"# Daily Trading Report {self.date}",
            "",
            f"- Total PnL: {self.total_pnl:.2f}",
            f"- Fees: {self.fees:.2f}",
            f"- Win rate: {self.win_rate:.2%}",
            f"- Max drawdown: {self.max_drawdown:.2%}",
            f"- Open positions: {len(self.positions)}",
        ]
        for pos in self.positions:
            lines.append(
                f"- {pos.symbol} {pos.direction} qty={pos.quantity:.8f} entry={pos.entry_price:.4f}"
            )
        return "\n".join(lines) + "\n"


def write_daily_report(report: DailyReport, directory: str) -> Path:
    path = Path(directory) / f"daily-report-{report.date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.to_markdown(), encoding="utf-8")
    return path

