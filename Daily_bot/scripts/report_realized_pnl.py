from __future__ import annotations

import argparse
from pathlib import Path

from Daily_bot.reporting.performance import summarize_realized_performance


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(Path("Daily_bot") / "bot.sqlite3"))
    parser.add_argument("--date", default=None, help="Session date in YYYY-MM-DD local time")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = summarize_realized_performance(args.db, session_date=args.date)

    print(
        {
            "trade_count": summary.trade_count,
            "gross_pnl_krw": summary.gross_pnl_krw,
            "wins": summary.wins,
            "losses": summary.losses,
            "breakeven": summary.breakeven,
            "open_buy_count": summary.open_buy_count,
            "open_buy_cost_krw": summary.open_buy_cost_krw,
        }
    )
    for trade in summary.trades:
        print(
            {
                "ticker": trade.ticker,
                "quantity": trade.quantity,
                "buy_price": trade.buy_price,
                "sell_price": trade.sell_price,
                "pnl_krw": trade.pnl_krw,
                "return_percent": round(trade.return_percent, 4),
                "buy_filled_at": trade.buy_filled_at,
                "sell_filled_at": trade.sell_filled_at,
            }
        )


if __name__ == "__main__":
    main()
