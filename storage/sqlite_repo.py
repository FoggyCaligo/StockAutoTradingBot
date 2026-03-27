import sqlite3
from pathlib import Path

from core.models import Prediction, OrderPlan


class SqliteRepo:
    def __init__(self, dbPath: str = "data/trading.db") -> None:
        Path("data").mkdir(exist_ok=True)
        self.connection = sqlite3.connect(dbPath)
        self._createTables()

    def _createTables(self) -> None:
        cursor = self.connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entryPrice INTEGER NOT NULL,
                predictedPrice INTEGER NOT NULL,
                expectedReturn REAL NOT NULL,
                predictedIndex INTEGER NOT NULL,
                createdAt DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orderPlans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                buyPrice INTEGER NOT NULL,
                sellPrice INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                expectedReturn REAL NOT NULL,
                createdAt DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.connection.commit()

    def savePrediction(self, prediction: Prediction) -> None:
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO predictions (
                symbol, entryPrice, predictedPrice, expectedReturn, predictedIndex
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            prediction.symbol,
            prediction.entryPrice,
            prediction.predictedPrice,
            prediction.expectedReturn,
            prediction.predictedIndex,
        ))
        self.connection.commit()

    def saveOrderPlan(self, plan: OrderPlan) -> None:
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO orderPlans (
                symbol, buyPrice, sellPrice, quantity, expectedReturn
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            plan.symbol,
            plan.buyPrice,
            plan.sellPrice,
            plan.quantity,
            plan.expectedReturn,
        ))
        self.connection.commit()