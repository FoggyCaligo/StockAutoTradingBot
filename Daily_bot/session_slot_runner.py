from __future__ import annotations

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
    """Refill profit exits, but close stop-loss slots for the rest of the trading day."""

    def __init__(self, state_path: Path = STATE_PATH):
        self.state_path = state_path
        self.session_date = datetime.now().strftime("%Y-%m-%d")
        self.closed_tickers: set[str] = set()
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

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_date": self.session_date,
            "closed_tickers": sorted(self.closed_tickers),
        }
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @property
    def closed_slot_count(self) -> int:
        return len(self.closed_tickers)

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

    def effective_position_limit(self, session_position_limit: int) -> int:
        if session_position_limit <= 0:
            return session_position_limit
        return max(0, session_position_limit - self.closed_slot_count)


def install_daily_slot_closure_policy() -> DailySlotClosurePolicy:
    policy = DailySlotClosurePolicy()
    original_attempt_stop_loss = bot_main._attempt_stop_loss_safely
    original_resolve_empty_slots = bot_main.resolve_empty_slots

    def attempt_stop_loss_with_slot_closure(client, recorder, positions, open_orders, cfg):
        executed, error, ticker = original_attempt_stop_loss(client, recorder, positions, open_orders, cfg)
        if executed:
            policy.record_stop_loss(ticker)
        return executed, error, ticker

    def resolve_empty_slots_with_daily_closure(
        max_position_count: int,
        active_count: int,
        candidate_count: int = 0,
    ) -> int:
        effective_limit = policy.effective_position_limit(max_position_count)
        return original_resolve_empty_slots(effective_limit, active_count, candidate_count)

    bot_main._attempt_stop_loss_safely = attempt_stop_loss_with_slot_closure
    bot_main.resolve_empty_slots = resolve_empty_slots_with_daily_closure
    return policy


def main() -> None:
    policy = install_daily_slot_closure_policy()
    print(
        "Daily slot policy active: profit exits refill before stop-buy time; "
        f"stop-loss slots stay closed for {policy.session_date}. "
        f"restored_closed_slots={policy.closed_slot_count}"
    )
    args = bot_main.parse_args()
    override = True if args.dry_run else False if args.real else None
    bot_main.run(args.config, override)


if __name__ == "__main__":
    main()
