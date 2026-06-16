from __future__ import annotations

import argparse
from pathlib import Path

from Daily_bot.reporting.performance import summarize_trade_edge


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(Path("Daily_bot") / "bot.sqlite3"))
    parser.add_argument("--date", default=None, help="Session date in YYYY-MM-DD local time")
    parser.add_argument("--show-trades", action="store_true")
    return parser.parse_args()


def _round_or_none(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def main() -> None:
    args = parse_args()
    summary = summarize_trade_edge(args.db, session_date=args.date)

    print(
        {
            "trade_count": summary.trade_count,
            "wins": summary.wins,
            "losses": summary.losses,
            "breakeven": summary.breakeven,
            "actual_win_rate_percent": round(summary.actual_win_rate_percent, 4),
            "gross_pnl_krw": summary.gross_pnl_krw,
            "net_pnl_krw": summary.net_pnl_krw,
            "avg_gross_win_percent": round(summary.avg_gross_win_percent, 4),
            "avg_gross_loss_percent": round(summary.avg_gross_loss_percent, 4),
            "avg_net_win_percent": round(summary.avg_net_win_percent, 4),
            "avg_net_loss_percent": round(summary.avg_net_loss_percent, 4),
            "avg_net_win_krw": round(summary.avg_net_win_krw, 4),
            "avg_net_loss_krw": round(summary.avg_net_loss_krw, 4),
            "net_payoff_ratio": _round_or_none(summary.net_payoff_ratio),
            "net_required_win_rate_percent": _round_or_none(summary.net_required_win_rate_percent),
            "net_expectancy_krw": round(summary.net_expectancy_krw, 4),
            "net_expectancy_percent": round(summary.net_expectancy_percent, 4),
        }
    )

    if not args.show_trades:
        return

    for trade in summary.trades:
        print(
            {
                "ticker": trade.ticker,
                "quantity": trade.quantity,
                "buy_price": trade.buy_price,
                "sell_price": trade.sell_price,
                "gross_pnl_krw": trade.pnl_krw,
                "net_pnl_krw": round(trade.net_pnl_krw, 4),
                "gross_return_percent": round(trade.return_percent, 4),
                "net_return_percent": round(trade.net_return_percent, 4),
                "buy_fee_krw": round(trade.buy_fee_krw, 4),
                "sell_fee_krw": round(trade.sell_fee_krw, 4),
                "sell_tax_krw": round(trade.sell_tax_krw, 4),
                "buy_filled_at": trade.buy_filled_at,
                "sell_filled_at": trade.sell_filled_at,
            }
        )


if __name__ == "__main__":
    main()
