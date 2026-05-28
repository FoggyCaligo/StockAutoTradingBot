from __future__ import annotations

from bot.config import StrategyConfig
from bot.models import Candidate, ExitDecision, MarketSnapshot, Position


class WeeklyPullbackStrategy:
    def __init__(self, config: StrategyConfig):
        self.config = config

    def select_candidates(self, snapshots: list[MarketSnapshot]) -> list[Candidate]:
        candidates: list[Candidate] = []
        for snapshot in snapshots:
            passed, reasons = self._passes_filters(snapshot)
            if not passed:
                continue
            candidates.append(
                Candidate(
                    snapshot=snapshot,
                    score=self._score(snapshot),
                    reasons=reasons,
                )
            )
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[: self.config.max_positions]

    def _passes_filters(self, s: MarketSnapshot) -> tuple[bool, list[str]]:
        reasons: list[str] = []

        if not s.is_kospi200:
            return False, ["not_kospi200"]
        reasons.append("kospi200")

        if s.market_cap_krw < self.config.min_market_cap_krw:
            return False, ["market_cap_too_small"]
        reasons.append("market_cap_ok")

        if not (self.config.min_change_pct <= s.change_pct <= self.config.max_change_pct):
            return False, ["change_pct_out_of_range"]
        reasons.append("pullback_range_ok")

        if s.turnover_krw < self.config.min_turnover_krw:
            return False, ["turnover_too_low"]
        reasons.append("turnover_ok")

        if self.config.min_volume > 0 and s.volume < self.config.min_volume:
            return False, ["volume_too_low"]
        if self.config.min_volume > 0:
            reasons.append("volume_ok")

        lower_envelope = self._lower_envelope(s)
        if s.current_price >= lower_envelope:
            return False, ["not_below_envelope"]
        reasons.append("below_envelope")

        if not self._trend_ok(s):
            return False, ["trend_filter_failed"]
        reasons.append("trend_ok")

        if self.config.max_spread_ticks > 0 and s.spread_ticks > self.config.max_spread_ticks:
            return False, ["spread_too_wide"]
        if self.config.max_spread_ticks > 0:
            reasons.append("spread_ok")

        return True, reasons

    def _lower_envelope(self, s: MarketSnapshot) -> float:
        envelope_basis = self._envelope_basis_ma(s)
        return envelope_basis * (1.0 - self.config.envelope_lower_pct / 100.0)

    def _envelope_basis_ma(self, s: MarketSnapshot) -> float:
        if self.config.envelope_ma_days == 20:
            return s.ma20
        if self.config.envelope_ma_days == 30:
            return s.ma30
        if self.config.envelope_ma_days == 50:
            return s.ma50
        if self.config.envelope_ma_days == 120:
            return s.ma120
        return s.ma20

    @staticmethod
    def _trend_ok(s: MarketSnapshot) -> bool:
        ma30_up = s.ma30 > s.ma30_prev
        ma50_up = s.ma50 > s.ma50_prev
        ma120_up_and_price_above = s.ma120 > s.ma120_prev and s.current_price > s.ma120
        return ma30_up or ma50_up or ma120_up_and_price_above

    def _score(self, s: MarketSnapshot) -> float:
        # 단순 v0.1 점수화. 필터 통과 후 10개 초과 시 정렬용으로만 사용한다.
        pullback_score = abs(s.change_pct)
        turnover_score = min(s.turnover_krw / 10_000_000_000, 10.0)
        spread_penalty = s.spread_ticks * 0.5 if self.config.max_spread_ticks > 0 else 0.0
        trend_bonus = 0.0
        if s.ma30 > s.ma30_prev:
            trend_bonus += 2.0
        if s.ma50 > s.ma50_prev:
            trend_bonus += 1.5
        if s.ma120 > s.ma120_prev and s.current_price > s.ma120:
            trend_bonus += 1.0
        return pullback_score + turnover_score + trend_bonus - spread_penalty

    def check_exit(self, position: Position, current_price: int) -> ExitDecision:
        pnl = position.pnl_pct(current_price)
        if pnl >= self.config.take_profit_pct:
            return ExitDecision(position, current_price, True, "take_profit", pnl)
        if pnl <= self.config.stop_loss_pct:
            return ExitDecision(position, current_price, True, "stop_loss", pnl)
        return ExitDecision(position, current_price, False, "hold", pnl)
