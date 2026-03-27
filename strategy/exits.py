from datetime import datetime

from core.models import Position, Signal


def shouldExitBySignal(position: Position, signal: Signal) -> bool:
    return signal.finalScore <= 0


def shouldExitByPrice(position: Position, currentPrice: int, stopLossRatio: float, takeProfitRatio: float) -> bool:
    pnlRatio = (currentPrice - position.averagePrice) / max(position.averagePrice, 1)
    return pnlRatio <= -stopLossRatio or pnlRatio >= takeProfitRatio


def shouldExitByTime(position: Position, now: datetime, maxHoldSeconds: int) -> bool:
    return (now - position.openedAt).total_seconds() >= maxHoldSeconds