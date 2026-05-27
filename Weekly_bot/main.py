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
    parser.add_argument("command", choices=["scan", "buy", "monitor", "friday-liquidate"])
    parser.add_argument("--config", default="config/strategy.yaml")
    parser.add_argument("--data", default="live")
    parser.add_argument("--cash", type=int, default=1_000_000)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--real", action="store_true", help="Submit real orders via Kiwoom REST API")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode")
    args = parser.parse_args()

    if args.real and args.dry_run:
        parser.error("--real and --dry-run cannot be used together.")

    if not args.real and not args.dry_run and os.getenv("TRADING_MODE", "").lower() == "real":
        args.real = True

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
        order_ids = runtime.monitor_exits()
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
