from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent
STATE_PATH = ROOT / "logs" / "session_closed_slots.json"
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from Daily_bot import main as bot_main


class DailySlotClosurePolicy:
    """Capital-based live slot policy for the current trading session.

    - The session position limit is resolved from the normal capital-based slot plan.
    - Never-used slots use the normal entry threshold.
    - Slots returned by take-profit may be refilled freely before stop-buy time,
      but use the stricter refill expected-return threshold.
    - Stop-loss/dynamic-stop slots stay closed for the rest of the current session.
    - There is no full-batch lock: a take-profit slot may be reused repeatedly.
    - All session-only state resets naturally on the next trading date.
    """

    def __init__(self, state_path: Path = STATE_PATH):
        self.state_path = state_path
        self.session_date = datetime.now().strftime("%Y-%m-%d")
        self.closed_tickers: set[str] = set()
        self.peak_active_count = 0
        self.latest_active_count = 0
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if payload.get("session_date") != self.session_date:
            return
        self.closed_tickers = {
            bot_main._ticker_key(str(ticker))
            for ticker in payload.get("closed_tickers", [])
            if bot_main._ticker_key(str(ticker))
        }
        self.peak_active_count = max(0, int(payload.get("peak_active_count", 0) or 0))

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_date": self.session_date,
            "closed_tickers": sorted(self.closed_tickers),
            "peak_active_count": self.peak_active_count,
        }
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @property
    def closed_slot_count(self) -> int:
        return len(self.closed_tickers)

    def effective_position_limit(self, session_position_limit: int) -> int:
        if session_position_limit <= 0:
            return session_position_limit
        return max(0, session_position_limit - self.closed_slot_count)

    def observe_active_count(self, active_count: int) -> None:
        self.latest_active_count = max(0, int(active_count or 0))
        if self.latest_active_count > self.peak_active_count:
            self.peak_active_count = self.latest_active_count
            self._save()

    def record_stop_loss(self, ticker: str | None) -> None:
        ticker_key = bot_main._ticker_key(str(ticker or ""))
        if not ticker_key or ticker_key in self.closed_tickers:
            return
        self.closed_tickers.add(ticker_key)
        self._save()
        print(
            f"Closing one slot for the rest of {self.session_date} after stop-loss: "
            f"ticker={ticker_key} closed_slots_today={self.closed_slot_count}"
        )

    def unused_slot_count(self, session_position_limit: int) -> int:
        effective_limit = self.effective_position_limit(session_position_limit)
        if effective_limit <= 0:
            return 0
        effective_peak = min(self.peak_active_count, effective_limit)
        return max(0, effective_limit - effective_peak)

    def profit_refill_slot_count(self, session_position_limit: int) -> int:
        effective_limit = self.effective_position_limit(session_position_limit)
        if effective_limit <= 0:
            return 0
        effective_peak = min(self.peak_active_count, effective_limit)
        return max(0, effective_peak - self.latest_active_count)

    def allowed_empty_slots(self, session_position_limit: int) -> int:
        unused = self.unused_slot_count(session_position_limit)
        if unused > 0:
            return unused
        return self.profit_refill_slot_count(session_position_limit)

    def is_profit_refill_cycle(self, session_position_limit: int) -> bool:
        return (
            self.unused_slot_count(session_position_limit) == 0
            and self.profit_refill_slot_count(session_position_limit) > 0
        )


def install_daily_slot_closure_policy() -> DailySlotClosurePolicy:
    policy = DailySlotClosurePolicy()
    original_attempt_stop_loss = bot_main._attempt_stop_loss_safely
    original_resolve_empty_slots = bot_main.resolve_empty_slots
    original_get_active_tickers = bot_main._get_active_tickers
    original_filter_candidates = bot_main.filter_candidates_for_entry

    current_session_limit = {"value": 0}

    def attempt_stop_loss_with_slot_closure(client, recorder, positions, open_orders, cfg):
        executed, error, ticker = original_attempt_stop_loss(client, recorder, positions, open_orders, cfg)
        if executed:
            policy.record_stop_loss(ticker)
        return executed, error, ticker

    def get_active_tickers_with_tracking(positions, open_orders):
        active = original_get_active_tickers(positions, open_orders)
        policy.observe_active_count(len(active))
        return active

    def filter_candidates_with_refill_threshold(
        calculated,
        cfg,
        previous_scan_prices=None,
        active_tickers=None,
        blocked_tickers=None,
        allow_refill_empty_slots=True,
    ):
        session_limit = int(current_session_limit["value"] or 0)
        if session_limit > 0 and policy.is_profit_refill_cycle(session_limit):
            strict_cfg = copy.deepcopy(cfg)
            refill_threshold = float(cfg["strategy"].get("refill_min_expected_return_percent", 0.90) or 0.90)
            strict_cfg["strategy"]["min_expected_return_percent"] = refill_threshold
            strict_cfg["strategy"]["min_expected_return_fallback_percents"] = []
            filtered, used = original_filter_candidates(
                calculated,
                strict_cfg,
                previous_scan_prices=previous_scan_prices,
                active_tickers=active_tickers,
                blocked_tickers=blocked_tickers,
                allow_refill_empty_slots=allow_refill_empty_slots,
            )
            if filtered:
                print(
                    "Applying stricter take-profit refill threshold: "
                    f"min_expected_return={refill_threshold:.2f}% candidates={len(filtered)}"
                )
            return filtered, used
        return original_filter_candidates(
            calculated,
            cfg,
            previous_scan_prices=previous_scan_prices,
            active_tickers=active_tickers,
            blocked_tickers=blocked_tickers,
            allow_refill_empty_slots=allow_refill_empty_slots,
        )

    def resolve_empty_slots_with_daily_closure(
        max_position_count: int,
        active_count: int,
        candidate_count: int = 0,
    ) -> int:
        current_session_limit["value"] = max(0, int(max_position_count or 0))
        policy.observe_active_count(active_count)
        effective_limit = policy.effective_position_limit(current_session_limit["value"])
        baseline = original_resolve_empty_slots(effective_limit, active_count, candidate_count)
        allowed = policy.allowed_empty_slots(current_session_limit["value"])
        return min(baseline, allowed)

    bot_main._attempt_stop_loss_safely = attempt_stop_loss_with_slot_closure
    bot_main._get_active_tickers = get_active_tickers_with_tracking
    bot_main.filter_candidates_for_entry = filter_candidates_with_refill_threshold
    bot_main.resolve_empty_slots = resolve_empty_slots_with_daily_closure
    return policy


def main() -> None:
    policy = install_daily_slot_closure_policy()
    print(
        "Daily slot policy active: capital-based slot limit; unused slots use 0.71%; "
        "take-profit returned slots freely refill at 0.90% before stop-buy time; "
        f"stop-loss slots stay closed for {policy.session_date}. "
        f"restored_closed_slots={policy.closed_slot_count} "
        f"restored_peak_active={policy.peak_active_count}"
    )
    args = bot_main.parse_args()
    override = True if args.dry_run else False if args.real else None
    bot_main.run(args.config, override)


if __name__ == "__main__":
    main()
