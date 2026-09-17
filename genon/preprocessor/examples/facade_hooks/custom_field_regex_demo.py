"""extractor: python 예시 — 규칙이 분명한 값은 LLM 에 묻지 않는다.

계약번호·금액처럼 표기가 정해진 값은 정규식이 더 정확하고 빠르며 비용이 없다.
사내 시스템에 조회해야 채워지는 값이라면 `async def` 로 바꿔 외부 호출을 하면 된다.

계약은 하나다 — **dict 를 돌려준다.** 그 뒤(출력 필드 정리, const/default, transform)는
`extractor: llm` 과 완전히 같은 경로를 탄다.

    def extract(text, document=None, output_fields=None, **kwargs) -> dict

인자가 부담스러우면 `def extract(text)` 만 선언해도 된다. kwargs 에는 요청 파라미터가
들어오므로 doc_type 이나 사이트 값으로 분기할 수 있다.
"""
import re

CONTRACT = re.compile(r"계약번호[:\s]*([A-Z0-9-]+)")
AMOUNT = re.compile(r"금액[:\s]*([0-9,]+)\s*원")


def extract(text, document=None, output_fields=None, **kwargs):
    contract = CONTRACT.search(text or "")
    amount = AMOUNT.search(text or "")
    return {
        "CONTRACT_NO": contract.group(1) if contract else None,
        # 쉼표는 그대로 둔다 — 숫자 변환은 yaml 의 transform: [to_int] 이 한다.
        "AMOUNT": amount.group(1) if amount else None,
    }
