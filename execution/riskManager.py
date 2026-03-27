from core.models import Position, Signal


class RiskManager:
    def __init__(self, maxDailyLoss: int, maxPositions: int = 3) -> None:
        self.maxDailyLoss = maxDailyLoss
        self.maxPositions = maxPositions
        self.dailyRealizedPnl = 0

    def canEnter(self, signal: Signal, positions: list[Position]) -> tuple[bool, str]:
        if self.dailyRealizedPnl <= -self.maxDailyLoss:
            return False, "일일 최대 손실 도달"

        if len(positions) >= self.maxPositions:
            return False, "최대 보유 종목 수 초과"

        if any(position.symbol == signal.symbol for position in positions):
            return False, "이미 보유 중인 종목"

        if not signal.isEntryCandidate:
            return False, "진입 후보가 아님"

        return True, "OK"