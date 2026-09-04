#!/usr/bin/env python3
"""monimo_product_hpp_fields_sample.json 재생성 — sections 의 값 파이프라인 검증용 원천.

기존 product_hpp 샘플 3건에는 `values`/`transform`/`template` 을 태울 재료가 없다.
그래서 같은 WCMS 상품 JSON 모양을 유지하면서 **값의 형태만** 아래처럼 흉내낸 가상 문서다.

1. `saleStatus` 가 사람이 읽는 이름이 아니라 **코드값**(`"1"`)이다 — `values` 로 접어야
   적재 컬럼이 표준값(`ON_SALE`)이 된다. 원천마다 `1`/`Y`/`판매중` 이 섞여 온다.
2. `feeAmount` 가 단위가 붙은 **금액 문자열**(`"18,000원"`)이다 — `transform` 으로
   숫자만 남기고 정수로 바꿔야 범위 검색이 된다.
3. 상품명이 브랜드(`brandName`)와 상품명(`cardTitle`) 두 필드로 나뉘어 있다 —
   `template` 으로 합쳐야 표시용 이름 하나가 된다.

그 외(섹션 구성·HTML 값·ignore 대상 키)는 기존 최소 샘플과 같게 두어, 이 파일 하나로
"값 파이프라인만" 달라진 대조가 되게 했다. 실 고객 데이터를 그대로 쓰지 않고 값은 지어냈다.

샘플이 다시 바뀌면 손으로 json 을 만지지 말고 이 스크립트를 고쳐 다시 돌린다.

실행:  genon/preprocessor/.venv/bin/python examples/parse_chunk/make_product_hpp_fields_sample.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = (
    Path(__file__).resolve().parents[2]
    / "sample_files" / "monimo" / "monimo_product_hpp_fields_sample.json"
)

PAYLOAD = {
    "wcmsId": "1900001",
    "productCode": "TST0001",
    # template 으로 합칠 두 필드. 원천은 브랜드와 상품명을 따로 들고 있다.
    "brandName": "다올신협",
    "cardTitle": "다올신협 체크카드",
    # values 로 접을 코드값. 원천이 사람이 읽는 이름을 주지 않는다.
    "saleStatus": "1",
    # transform 으로 숫자만 남길 금액 문자열.
    "feeAmount": "18,000원",
    "cardSlogan": "생활비는 아끼고<br>포인트는 쌓이는 체크카드",
    # 검색에 쓸모없는 값(ignore_keys 대상). 값 파이프라인과 무관하게 그대로 빠져야 한다.
    "pcImg1": "/wcms/home/scard/image/personal/b_TST0001.png",
    "fontColor": "#1a1a1a",
    "benefit": [
        "전월실적 없이 포인트 적립",
        "대중교통 10% 할인",
        "편의점 5% 할인",
    ],
    "htmlList": {
        "feeUrl": (
            "<h3>연회비</h3>"
            "<table><tr><th>구분</th><th>금액</th></tr>"
            "<tr><td>국내전용</td><td>18,000원</td></tr>"
            "<tr><td>해외겸용</td><td>20,000원</td></tr></table>"
        ),
        "noticeUrl": (
            "<h3>이용 유의사항</h3>"
            "<p>체크카드는 예금 잔액 범위에서만 결제됩니다.</p>"
            "<p>전월실적 조건은 매월 1일 기준으로 재산정됩니다.</p>"
        ),
    },
    # 이 카드가 아니라 다른 카드(추천 상품) — ignore_keys 로 통째로 빠진다.
    "mpo": [
        {"code": "OTH0002", "name": "다른 신협 카드", "ksp": [{"title": "다른 카드만의 혜택"}]},
    ],
}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(PAYLOAD, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
