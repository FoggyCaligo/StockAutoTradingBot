import sqlite3
from pathlib import Path

from core.models import Signal


class SqliteRepo:
    def __init__(self, dbPath: str = "data/trading.db") -> None:
        Path("data").mkdir(exist_ok=True)
        self.connection = sqlite3.connect(dbPath)
        self._createTables()

    def _createTables(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                predictedPrice INTEGER NOT NULL,
                rawEdge REAL NOT NULL,
                spreadRatio REAL NOT NULL,
                impactPenalty REAL NOT NULL,
                volatilityPenalty REAL NOT NULL,
                finalScore REAL NOT NULL,
                isEntryCandidate INTEGER NOT NULL,
                reason TEXT NOT NULL,
                createdAt DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.connection.commit()

    def saveSignal(self, signal: Signal) -> None:
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO signals (
                symbol, predictedPrice, rawEdge, spreadRatio,
                impactPenalty, volatilityPenalty, finalScore,
                isEntryCandidate, reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal.symbol,
            signal.predictedPrice,
            signal.rawEdge,
            signal.spreadRatio,
            signal.impactPenalty,
            signal.volatilityPenalty,
            signal.finalScore,
            1 if signal.isEntryCandidate else 0,
            signal.reason,
        ))
        self.connection.commit()