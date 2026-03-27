from config.settings import settings


class KisSpecs:
    tokenPath = settings.kisTokenPath
    wsApprovalPath = settings.kisWsApprovalPath
    inquirePricePath = settings.kisInquirePricePath
    inquireOrderbookPath = settings.kisInquireOrderbookPath
    orderCashPath = settings.kisOrderCashPath
    hashkeyPath = settings.kisHashkeyPath

    trPrice = settings.kisTrPrice
    trOrderbook = settings.kisTrOrderbook
    trBuy = settings.kisTrBuy
    trSell = settings.kisTrSell
    trBalance = settings.kisTrBalance

    wsTrOrderbook = settings.kisWsTrOrderbook
    wsTrTrade = settings.kisWsTrTrade

    custtype = settings.kisCusttype

    @staticmethod
    def buildPriceParams(symbol: str) -> dict:
        return {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": symbol,
        }

    @staticmethod
    def buildOrderbookParams(symbol: str) -> dict:
        return {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": symbol,
        }

    @staticmethod
    def buildBuyBody(symbol: str, quantity: int, price: int | None) -> dict:
        return {
            "CANO": settings.kisAccountNo,
            "ACNT_PRDT_CD": settings.kisAccountProductCode,
            "PDNO": symbol,
            "ORD_DVSN": "01" if price is None else "00",  # TODO: 문서 기준 재검증
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0" if price is None else str(price),
        }

    @staticmethod
    def buildSellBody(symbol: str, quantity: int, price: int | None) -> dict:
        return {
            "CANO": settings.kisAccountNo,
            "ACNT_PRDT_CD": settings.kisAccountProductCode,
            "PDNO": symbol,
            "ORD_DVSN": "01" if price is None else "00",  # TODO: 문서 기준 재검증
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0" if price is None else str(price),
        }

    @staticmethod
    def buildWsSubscribeMessage(trId: str, trKey: str, approvalKey: str) -> dict:
        # TODO: 현재 계정/환경에 맞는 공식 웹소켓 구독 포맷으로 맞춰 넣기
        return {
            "header": {
                "approval_key": approvalKey,
                "custtype": settings.kisCusttype,
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": trId,
                    "tr_key": trKey,
                }
            },
        }