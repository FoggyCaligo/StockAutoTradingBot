"""Quote-only market trace recorder. Never submits, cancels or reconciles orders."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from Daily_bot import main as bot_main
from Daily_bot.storage.db import Recorder
from Daily_bot.strategy.orderbook_predictor import calc_target_sell_price
from Daily_bot.strategy.signal import calc_expected_return
from Daily_bot.strategy.universe import get_candidates
from Daily_bot.utils import RateLimiter, load_yaml


def collect_cycle(client, recorder, candidates, cfg, kospi_change_percent=None) -> tuple[int, int]:
    """Capture each eligible ticker once; preserve the existing market_traces schema."""
    limiter = RateLimiter(int(cfg["api"]["quote_rate_limit_per_second"]))
    scan_cycle_at = datetime.now()
    successful = 0
    failed = 0
    for ticker, candidate in candidates.items():
        try:
            limiter.wait()
            snapshot = client.get_20hoga(ticker)
            traced = calc_expected_return(
                candidate, snapshot,
                cfg["strategy"]["sell_tick_offset"], cfg["strategy"],
            )
            recorder.save_market_trace(
                traced, snapshot, phase="scan_candidate", selected=False,
                reason="quote_only_collection", scan_cycle_at=scan_cycle_at,
                kospi_change_percent=kospi_change_percent,
            )
            if not recorder._db_enabled or not recorder._csv_enabled:
                raise RuntimeError("market trace DB or CSV sink has been disabled")
            successful += 1
        except Exception as exc:
            failed += 1
            print(f"Market trace collection failed ticker={ticker}: {exc}", flush=True)
            if not recorder._db_enabled or not recorder._csv_enabled:
                raise RuntimeError("Market trace recording stopped: DB/CSV write failure") from exc
    return successful, failed


def run(cfg_path: str, *, dry_run: bool = False) -> None:
    cfg = load_yaml(cfg_path)
    client = bot_main.build_client(dry_run)
    if getattr(client, "is_real_trade", False):
        raise RuntimeError("Quote-only collector requires is_real_trade=false")
    recorder = Recorder(bot_main.BOT_DB_PATH, log_dir=bot_main.BOT_LOG_DIR)
    if not recorder._db_enabled or not recorder._csv_enabled:
        raise RuntimeError("Market trace recorder initialization failed")
    print("Quote-only market trace mode: trade/order/cancel/force-sell APIs disabled", flush=True)
    try:
        while not bot_main._authenticate_client_safely(client):
            time.sleep(5)
        print("Quote-only authentication completed", flush=True)
        start = cfg.get("market_trace_collection", {}).get("start_time", cfg["market"]["start_buy_time"])
        end = cfg.get("market_trace_collection", {}).get("end_time", cfg["market"].get("end_time", "15:25"))
        interval = max(1, int(cfg.get("market_trace_collection", {}).get("interval_seconds", cfg["strategy"]["scan_interval_seconds"])))
        # A single universe refresh for the session avoids repeated remote universe calls.
        candidates = None
        while not bot_main.is_after_now(end):
            if not bot_main.is_after_now(start):
                time.sleep(5)
                continue
            try:
                if candidates is None:
                    candidates = get_candidates(
                        bot_main.build_universe_config(cfg),
                        cfg["trend_filter"]["enabled"],
                    )
                    print(f"Quote-only universe initialized: {len(candidates)} tickers", flush=True)
                if not candidates:
                    raise RuntimeError("No eligible tickers in the market universe")
                count, failures = collect_cycle(client, recorder, candidates, cfg)
                print(f"Market trace cycle completed: saved={count} errors={failures}", flush=True)
            except (KeyboardInterrupt, SystemExit):
                raise
            except RuntimeError:
                raise
            except Exception as exc:
                print(f"Market trace cycle error: {exc}", flush=True)
                candidates = None
            time.sleep(interval)
        print("Quote-only collection window ended; exiting normally", flush=True)
    finally:
        if recorder.conn is not None:
            recorder.conn.close()


if __name__ == "__main__":
    args = bot_main.parse_args()
    run(args.config, dry_run=args.dry_run)
