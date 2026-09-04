#!/usr/bin/env python3
"""monimo_product_slf_fields_sample.md 재생성 — document 값 파이프라인 검증용 원천.

기존 상품요약서 샘플의 front matter 7줄(file/document_type/source_file/source_pages/
author/created_at/conversion_note)에는 접을 코드값도 결합할 두 필드도 없다. 이 파일은 같은
md 상품요약서 모양을 유지하면서 **front matter 값의 형태만** 아래처럼 흉내낸 가상 문서다.

1. `sale_state` 가 사람이 읽는 이름이 아니라 **코드값**(`"1"`)이다 — `values` 로 접어야
   적재 컬럼이 표준값(`SALE`)이 된다.
2. `created_at` 이 `2026.01.12` 처럼 **구분자가 다른 날짜 문자열**이다 — `transform` 의
   `date_int_flex` 가 YYYYMMDD 정수로 바꾼다.
3. 상품명이 브랜드(`brand`)와 상품명(`product_name`) 두 키로 나뉘어 있다 — `template` 으로
   합쳐야 표시용 이름 하나가 된다.

본문은 기존 샘플과 같은 구조(핵심 키워드 / 문서 개요 / ```text 펜스 원문 전사)를 짧게
줄여 두었다. 실 고객 데이터를 그대로 쓰지 않고 값은 지어냈다.

샘플이 다시 바뀌면 손으로 md 를 만지지 말고 이 스크립트를 고쳐 다시 돌린다.

실행:  genon/preprocessor/.venv/bin/python examples/parse_chunk/make_product_slf_fields_sample.py
"""
from __future__ import annotations

from pathlib import Path

OUT = (
    Path(__file__).resolve().parents[2]
    / "sample_files" / "monimo" / "monimo_product_slf_fields_sample.md"
)

TEXT = """---
file: "든든한 여행상해보험(2601)(무배당) 상품요약서"
document_type: "보험 상품요약서"
source_file: "1900000000001.pdf"
source_pages: 4
author: "홍길동"
brand: "삼성생명"
product_name: "든든한 여행상해보험(2601)(무배당)"
product_code: "90001"
sale_state: "1"
created_at: "2026.01.12"
conversion_note: "AI 검색요약용 Markdown 변환본."
---

# 든든한 여행상해보험(2601)(무배당) 상품요약서

## 핵심 키워드

여행상해, 해외실손, 항공기지연, 휴대품손해

## 문서 개요

해외여행 중 발생한 상해와 질병 치료비를 보장하는 상품이다. 보험기간은 여행 출발일부터
귀국일까지이며, 최대 90일까지 가입할 수 있다.

## 원문 전사

```text
1. 보험금 지급사유
   가. 여행 중 상해로 사망한 경우 사망보험금을 지급합니다.
   나. 여행 중 상해로 치료를 받은 경우 실제 부담한 의료비를 보장합니다.
   ※ 여행 출발 전 발생한 상해는 보장하지 않습니다.

2. 보험금을 지급하지 않는 사유
   가. 계약자 또는 피보험자의 고의로 발생한 손해
   나. 전쟁, 혁명, 내란으로 발생한 손해
```
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(TEXT, encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
