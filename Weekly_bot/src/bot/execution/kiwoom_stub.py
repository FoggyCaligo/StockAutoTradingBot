from __future__ import annotations

from bot.execution.base import OrderExecutor
from bot.models import OrderIntent, Position


class KiwoomExecutorStub(OrderExecutor):
    """실제 키움 OpenAPI+ 연동 전용 stub.

    이 클래스는 의도적으로 주문을 실행하지 않습니다.
    실거래 연결 시 다음 메서드들을 실제 OpenAPI 호출로 교체하세요.
    """

    def get_available_cash(self) -> int:
        raise NotImplementedError("TODO: 키움 예수금 조회 API로 교체")

    def get_positions(self) -> list[Position]:
        raise NotImplementedError("TODO: 키움 잔고 조회 API로 교체")

    def submit_order(self, order: OrderIntent) -> str:
        raise NotImplementedError("TODO: 키움 SendOrder 호출로 교체")
