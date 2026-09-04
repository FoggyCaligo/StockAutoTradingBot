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

    - The session position limit is whatever the normal capital-based slot plan resolves to.
    - Before that full limit has ever been reached, never-used slots may be filled at the normal 0.71% threshold.
    - Slots returned by take-profit may be refilled at the stricter 0.90% threshold only while the batch has not yet reached full capacity.
    - Once the current effective position limit has been fully occupied at least once, the batch is locked: no further buys until every active position/order is gone.
    - Stop-loss/dynamic-stop slots stay closed for the rest of the session, reducing the effective limit for that day only.
    - When a locked batch becomes completely flat, a new batch may start using the remaining effective slots.
    - All session-only state resets naturally on the next trading date.
    """

    def __init__(self, state_path: Path = STATE_PATH):
        self.state_path = state_path
        self.session_date = datetime.now().strftime("%Y-%m-%d")
        self.closed_tickers: set[str] = set()
        self.latest_active_count = 0
        self.batch_peak_active_count = 0
        self.batch_full_locked = False
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
        self.batch_peak_active_count = max(0, int(payload.get("batch_peak_active_count", 0) or 0))
        self.batch_full_locked = bool(payload.get("batch_full_locked", False))

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_date": self.session_date,
            "closed_tickers": sorted(self.closed_tickers),
            "batch_peak_active_count": self.batch_peak_active_count,
            "batch_full_locked": self.batch_full_locked,
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

    def observe_active_count(self, active_count: int, session_position_limit: int | None = None) -> None:
        active_count = max(0, int(active_count or 0))
        self.latest_active_count = active_count

        changed = False
        if self.batch_full_locked and active_count == 0:
            self.batch_full_locked = False
            self.batch_peak_active_count = 0
            changed = True
        elif not self.batch_full_locked and active_count > self.batch_peak_active_count:
            self.batch_peak_active_count = active_count
            changed = True

        if session_position_limit is not None:
            effective_limit = self.effective_position_limit(session_position_limit)
            if effective_limit > 0 and active_count >= effective_limit and not self.batch_full_locked:
                self.batch_full_locked = True
                self.batch_peak_active_count = effective_limit
                changed = True
                print(
                    "Full batch reached; locking new buys until batch is completely flat: "
                    f"active={active_count} effective_limit={effective_limit}"
                )

        if changed:
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
        effective_peak = min(self.batch_peak_active_count, effective_limit)
        return max(0, effective_limit - effective_peak)

    def returned_profit_slot_count(self, session_position_limit: int) -> int:
        effective_limit = self.effective_position_limit(session_position_limit)
        if effective_limit <= 0:
            return 0
        effective_peak = min(self.batch_peak_active_count, effective_limit)
        return max(0, effective_peak - self.latest_active_count)

    def is_profit_refill_cycle(self, session_position_limit: int) -> bool:
        if self.batch_full_locked:
            return False
        return self.unused_slot_count(session_position_limit) == 0 and self.returned_profit_slot_count(session_position_limit) > 0

    def allowed_empty_slots(self, session_position_limit: int) -> int:
        if self.batch_full_locked:
            return 0
        unused = self.unused_slot_count(session_position_limit)
        if unused > 0:
            return unused
        return self.returned_profit_slot_count(session_position_limit)


def install_daily_slot_closure_policy() -> DailySlotClosurePolicy:
    policy = DailySlotClosurePolicy()
    original_attempt_stop_loss = bot_main._attempt_stop_loss_safely
    original_resolve_empty_slots = bot_main.resolve_empty_slots
    original_filter_candidates = bot_main.filter_candidates_for_entry
    original_get_active_tickers = bot_main._get_active_tickers

    current_session_limit = {"value": 0}

    def attempt_stop_loss_with_slot_closure(client, recorder, positions, open_orders, cfg):
        executed, error, ticker = original_attempt_stop_loss(client, recorder, positions, open_orders, cfg)
        if executed:
            policy.record_stop_loss(ticker)
        return executed, error, ticker

    def get_active_tickers_with_batch_tracking(positions, open_orders):
        active = original_get_active_tickers(positions, open_orders)
        session_limit = int(current_session_limit["value"] or 0)
        policy.observe_active_count(len(active), session_limit if session_limit > 0 else None)
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
        policy.observe_active_count(active_count, current_session_limit["value"])
        effective_limit = policy.effective_position_limit(current_session_limit["value"])
        baseline = original_resolve_empty_slots(effective_limit, active_count, candidate_count)
        allowed = policy.allowed_empty_slots(current_session_limit["value"])
        return min(baseline, allowed)

    bot_main._attempt_stop_loss_safely = attempt_stop_loss_with_slot_closure
    bot_main._get_active_tickers = get_active_tickers_with_batch_tracking
    bot_main.filter_candidates_for_entry = filter_candidates_with_refill_threshold
    bot_main.resolve_empty_slots = resolve_empty_slots_with_daily_closure
    return policy


def main() -> None:
    policy = install_daily_slot_closure_policy()
    print(
        "Daily slot policy active: capital-based slot limit; unused slots use 0.71%; "
        "take-profit returned slots use 0.90% until the batch first reaches full capacity; "
        "after full capacity is reached, new buys stay locked until the batch is completely flat; "
        f"stop-loss slots stay closed for {policy.session_date}. "
        f"restored_closed_slots={policy.closed_slot_count} "
        f"restored_batch_peak={policy.batch_peak_active_count} "
        f"restored_batch_locked={policy.batch_full_locked}"
    )
    args = bot_main.parse_args()
    override = True if args.dry_run else False if args.real else None
    bot_main.run(args.config, override)


if __name__ == "__main__":
    main()
