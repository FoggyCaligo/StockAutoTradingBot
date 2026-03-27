from core.models import Signal


def selectTopSignals(signals: list[Signal], topK: int) -> list[Signal]:
    candidates = [signal for signal in signals if signal.isEntryCandidate]
    candidates.sort(key=lambda signal: signal.finalScore, reverse=True)
    return candidates[:topK]