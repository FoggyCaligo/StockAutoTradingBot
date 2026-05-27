from models import HogaLevel, HogaSnapshot
from strategy.orderbook_predictor import predict_price_from_hoga


def test_predict_price_returns_int():
    snapshot = HogaSnapshot(
        ticker="000000",
        current_price=10000,
        bids=[HogaLevel(9950, 100), HogaLevel(9900, 100)],
        asks=[HogaLevel(10050, 100), HogaLevel(10100, 100)],
    )
    assert isinstance(predict_price_from_hoga(snapshot), int)
