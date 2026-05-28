from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bot.config import load_config
from bot.backtest import BacktestSettings, WeeklyBacktester
from bot.data.csv_provider import CsvMarketDataProvider
from bot.data.live_provider import LiveKrxMarketDataProvider
from bot.execution.dry_run import DryRunExecutor
from bot.execution.kiwoom_real import KiwoomRealExecutor
from bot.runtime import BotRuntime


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    if path.exists():
        return path.resolve()
    return ROOT / path


def build_runtime(args: argparse.Namespace) -> BotRuntime:
    config_path = _resolve_path(args.config)
    log_dir = _resolve_path(args.log_dir)

    config = load_config(config_path)
    if str(args.data).lower() == "live":
        data_provider = LiveKrxMarketDataProvider()
    else:
        data_path = _resolve_path(args.data)
        data_provider = CsvMarketDataProvider(data_path)
    if args.real:
        executor = KiwoomRealExecutor(log_dir=log_dir)
    else:
        executor = DryRunExecutor(available_cash=args.cash, log_dir=log_dir)
    return BotRuntime(config=config, data_provider=data_provider, executor=executor, log_dir=log_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="KOSPI200 Weekly Pullback Bot v0.1")
    parser.add_argument("command", choices=["scan", "buy", "monitor", "friday-liquidate", "backtest"])
    parser.add_argument("--config", default="config/strategy.yaml")
    parser.add_argument("--data", default="live")
    parser.add_argument("--cash", type=int, default=1_000_000)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--real", action="store_true", help="Submit real orders via Kiwoom REST API")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode")
    parser.add_argument("--once", action="store_true", help="Run monitor only once instead of looping until monitor_end_time")
    parser.add_argument("--start", help="Backtest start date in YYYY-MM-DD format")
    parser.add_argument("--end", help="Backtest end date in YYYY-MM-DD format")
    parser.add_argument("--source", default="auto", choices=["auto", "fdr", "yfinance"], help="Historical data source for backtest")
    parser.add_argument("--signal-weekday", default="friday", choices=["monday", "tuesday", "wednesday", "thursday", "friday"], help="Signal generation weekday for backtest")
    parser.add_argument("--entry-offset-days", type=int, default=1, help="Number of trading days after the signal day to enter in backtest")
    parser.add_argument("--liquidation-offset-days", type=int, default=0, help="Number of extra trading days after Friday before forced liquidation")
    parser.add_argument("--approx-monday-10am", action="store_true", help="Approximate Monday 10:00 signal by using Monday open versus prior close while keeping prior trend indicators")
    parser.add_argument("--monday-approx-price-mode", default="open", choices=["open", "mid", "weighted"], help="Price proxy to use for Monday 10:00 approximation")
    parser.add_argument("--monday-approx-max-gap-pct", type=float, default=2.0, help="Use Monday 10:00 approximation only when Monday open gap versus prior close stays within this percent")
    parser.add_argument("--collision-tp-ratio", type=float, default=0.75, help="When both TP and SL touch on the same day in backtest, assume TP with this probability-like ratio")
    parser.add_argument("--buy-slippage-bps", type=float, default=0.0, help="Backtest buy slippage in basis points")
    parser.add_argument("--sell-slippage-bps", type=float, default=0.0, help="Backtest sell slippage in basis points")
    parser.add_argument("--buy-fee-bps", type=float, default=0.0, help="Backtest buy fee in basis points")
    parser.add_argument("--sell-fee-bps", type=float, default=0.0, help="Backtest sell fee in basis points")
    parser.add_argument("--sell-tax-bps", type=float, default=0.0, help="Backtest sell-side tax in basis points")
    args = parser.parse_args()

    if args.real and args.dry_run:
        parser.error("--real and --dry-run cannot be used together.")

    if not args.real and not args.dry_run and os.getenv("TRADING_MODE", "").lower() == "real":
        args.real = True

    if args.command == "backtest":
        if not args.start or not args.end:
            parser.error("--start and --end are required for backtest.")
        config = load_config(_resolve_path(args.config))
        backtester = WeeklyBacktester(
            config=config,
            settings=BacktestSettings(
                start=args.start,
                end=args.end,
                initial_cash=args.cash,
                data_source=args.source,
                signal_weekday=args.signal_weekday,
                entry_offset_trading_days=args.entry_offset_days,
                liquidation_offset_trading_days=args.liquidation_offset_days,
                approximate_monday_10am=args.approx_monday_10am,
                monday_approx_price_mode=args.monday_approx_price_mode,
                monday_approx_max_gap_pct=args.monday_approx_max_gap_pct,
                collision_take_profit_ratio=args.collision_tp_ratio,
                buy_slippage_bps=args.buy_slippage_bps,
                sell_slippage_bps=args.sell_slippage_bps,
                buy_fee_bps=args.buy_fee_bps,
                sell_fee_bps=args.sell_fee_bps,
                sell_tax_bps=args.sell_tax_bps,
                output_dir=_resolve_path(args.log_dir) / "backtests",
            ),
        )
        artifacts = backtester.run()
        print(artifacts.summary.to_string(index=False))
        print(f"backtest_output_dir={artifacts.output_dir}")
        return

    runtime = build_runtime(args)

    if args.command == "scan":
        candidates = runtime.scan_candidates()
        print(f"selected_candidates={len(candidates)}")
        for idx, candidate in enumerate(candidates, start=1):
            s = candidate.snapshot
            print(f"{idx}. {s.code} {s.name} price={s.current_price} change={s.change_pct}% score={candidate.score:.2f}")
    elif args.command == "buy":
        order_ids = runtime.monday_buy()
        print(f"submitted_buy_orders={len(order_ids)}")
        for order_id in order_ids:
            print(order_id)
    elif args.command == "monitor":
        order_ids = runtime.monitor_exits() if args.once else runtime.monitor_exits_loop()
        print(f"submitted_exit_orders={len(order_ids)}")
        for order_id in order_ids:
            print(order_id)
    elif args.command == "friday-liquidate":
        order_ids = runtime.friday_liquidate()
        print(f"submitted_liquidation_orders={len(order_ids)}")
        for order_id in order_ids:
            print(order_id)


if __name__ == "__main__":
    main()
