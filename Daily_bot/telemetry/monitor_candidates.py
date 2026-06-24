from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from dotenv import load_dotenv

from Daily_bot.broker.kiwoom_client import KiwoomClient
from Daily_bot.broker.mock_client import MockKiwoomClient
from Daily_bot.storage.db import Recorder
from Daily_bot.strategy.signal import calc_expected_return, final_filter, get_candidates_top
from Daily_bot.strategy.universe import UniverseConfig, get_candidates, get_kospi_change_percent
from Daily_bot.telemetry.trace_helpers import ticker_key, trace_candidate_watchlist
from Daily_bot.utils import RateLimiter, is_after_now, is_between_now, load_yaml

load_dotenv()


def build_client(dry_run: bool):
    return MockKiwoomClient() if dry_run else KiwoomClient()


def build_universe_config(cfg: dict) -> UniverseConfig:
    return UniverseConfig(
        min_market_cap_krw=cfg["universe"]["min_market_cap_krw"],
        min_trading_value_krw=cfg["universe"]["min_trading_value_krw"],
        csv_path=cfg["universe"].get("csv_path"),
        cache_path=cfg["universe"].get("cache_path"),
        source=cfg["universe"].get("source", "KOSPI200"),
        refresh_daily=cfg["universe"].get("refresh_daily", True),
    )


def resolve_kospi_change_percent() -> float | None:
    try:
        return get_kospi_change_percent()
    except Exception as exc:
        print(f"Failed to resolve KOSPI change percent for monitor: {exc}")
        return None


def record_session_prev_close_prices(recorder: Recorder, cfg: dict) -> dict[str, int]:
    candidates = get_candidates(build_universe_config(cfg), cfg["trend_filter"]["enabled"])
    recorder.save_daily_reference_prices(candidates, source="candidate_monitor_startup")
    return recorder.get_daily_reference_prices()


def scan_filtered_candidates(
    client,
    recorder: Recorder,
    cfg: dict,
    kospi_change_percent: float | None = None,
    prev_close_prices: dict[str, int] | None = None,
) -> dict[str, object]:
    candidates = get_candidates(build_universe_config(cfg), cfg["trend_filter"]["enabled"])
    prev_close_prices = prev_close_prices or {}
    for candidate in candidates.values():
        prev_close_price = int(prev_close_prices.get(ticker_key(candidate.ticker), 0) or 0)
        if prev_close_price > 0:
            candidate.prev_close_price = prev_close_price
    limiter = RateLimiter(cfg["api"]["quote_rate_limit_per_second"])
    calculated = []
    scan_cycle_at = datetime.now()
    for ticker, candidate in candidates.items():
        try:
            limiter.wait()
            snapshot = client.get_20hoga(ticker)
            calculated_candidate = calc_expected_return(candidate, snapshot, cfg["strategy"]["sell_tick_offset"])
            recorder.save_snapshot(calculated_candidate, snapshot, scan_cycle_at=scan_cycle_at)
            recorder.save_signal(calculated_candidate, selected=False, scan_cycle_at=scan_cycle_at)
            recorder.save_market_trace(
                calculated_candidate,
                snapshot,
                phase="candidate_scan",
                selected=False,
                reason="monitor_scan",
                scan_cycle_at=scan_cycle_at,
                kospi_change_percent=kospi_change_percent,
            )
            calculated.append(calculated_candidate)
        except Exception as exc:
            print(f"Skipping {ticker} during candidate monitor scan: {exc}")

    top = get_candidates_top(calculated, cfg["strategy"]["top_ratio"])
    filtered = final_filter(
        top,
        cfg["strategy"]["min_expected_return_percent"],
        cfg["strategy"]["sell_tick_offset"],
        cfg["strategy"].get("max_spread_percent", 0.7),
        cfg["strategy"].get("min_prev_day_change_percent", 0.0),
        cfg["strategy"].get("max_prev_day_change_percent", 15.0),
        cfg["strategy"].get("spread_expected_return_multiplier", 0.0),
    )
    return {ticker_key(candidate.ticker): candidate for candidate in filtered}


def run(cfg_path: str, dry_run_override: bool | None = None) -> None:
    cfg = load_yaml(cfg_path)
    dry_run = cfg["risk"]["dry_run"] if dry_run_override is None else dry_run_override
    client = build_client(dry_run)
    recorder = Recorder(Path("bot.sqlite3"))
    client.auth()
    prev_close_prices = record_session_prev_close_prices(recorder, cfg)

    watchlist: dict[str, object] = {}
    print("Candidate monitor started. This process records quotes only and does not place orders.")

    while True:
        if is_after_now(cfg["market"]["force_sell_time"]):
            print("Candidate monitor reached force_sell_time. Stopping.")
            break

        if not is_between_now(cfg["market"]["start_buy_time"], cfg["market"]["stop_buy_time"]):
            time.sleep(5)
            continue

        try:
            kospi_change_percent = resolve_kospi_change_percent()
            filtered = scan_filtered_candidates(
                client,
                recorder,
                cfg,
                kospi_change_percent=kospi_change_percent,
                prev_close_prices=prev_close_prices,
            )
            watchlist.update(filtered)
            if watchlist:
                watchlist = trace_candidate_watchlist(
                    client=client,
                    recorder=recorder,
                    candidates=watchlist,
                    quote_rate_limit_per_second=cfg["api"]["quote_rate_limit_per_second"],
                    sell_tick_offset=cfg["strategy"]["sell_tick_offset"],
                    selected_keys=set(filtered.keys()),
                    kospi_change_percent=kospi_change_percent,
                )
            print(f"Candidate monitor traced {len(watchlist)} candidates; latest filtered={len(filtered)}")
        except Exception as exc:
            print(f"Candidate monitor cycle failed: {exc}")

        time.sleep(cfg["strategy"].get("scan_interval_seconds", 60))


def parse_args():
    parser = argparse.ArgumentParser(description="Quote-only monitor for Daily_bot filtered candidates.")
    parser.add_argument("--config", default=str(ROOT / "config/settings.yaml"))
    parser.add_argument("--dry-run", action="store_true", help="Use mock broker and do not send real orders")
    parser.add_argument("--real", action="store_true", help="Use real Kiwoom quote client")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    override = True if args.dry_run else False if args.real else None
    run(args.config, override)
