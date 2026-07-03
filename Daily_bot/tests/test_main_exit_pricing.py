from Daily_bot.main import _safe_target_sell_price
from Daily_bot.models import Candidate


def test_safe_target_sell_price_raises_target_above_buy_for_target_sell_price_subclass():
    candidate = Candidate(ticker="105630", price=8650, expect_price=8650)

    assert _safe_target_sell_price(candidate, tick_offset=1, buy_reference_price=8650) == 8660
