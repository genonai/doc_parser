#!/usr/bin/env python3
"""이상 청크 판정(chunking.validation) 문서 샘플 3종 재생성.

판정 샘플(tests/fixtures/chunk_quality/cases.yaml)의 일부를 실제 문서로 만들어, 파싱·청킹을
거친 뒤에도 판정이 같은지 확인하는 원천이다. 대조군이 핵심이다 — 불량 섹션이 빠지는 것보다
정상 섹션이 남는 것이 이 기능의 위험이다.

  chunk_quality_sample.md    정상 섹션 사이에 불량 섹션(E03·E06·E08·E10·E11)을 하나씩 넣고
                             대조군(N01·N03·N05·N10)을 둔다
  chunk_quality_rows.json    파서 산출 형식(elements). 정상 FAQ 행 5건 + 빈 행 + 기호만 있는 행
  chunk_quality_all_bad.md   모든 섹션이 불량인 문서. 전부 제외되어 문서가 실패해야 한다

E03(빈 `<div>` 반복)은 마크다운 백엔드가 파싱 단계에서 버려 청크까지 오지 않는다. 판정은
단위 샘플이 확인하고, 이 문서에서는 청크에 남지 않는 것만 본다.

실 고객 데이터를 쓰지 않고 값은 지어냈다.
샘플이 다시 바뀌면 손으로 파일을 만지지 말고 이 스크립트를 고쳐 다시 실행한다.

실행:  genon/preprocessor/.venv/bin/python examples/parse_chunk/make_chunk_quality_sample.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[2] / "sample_files"

# 불량 본문. parse_chunk_verify.py 가 이 문자열이 청크에 남지 않았는지 단정한다.
E06 = "처리 중 오류가 발생했습니다. " * 30
E08 = "페이지를 표시할 수 없습니다\n" * 15
E10 = "\uFFFD " * 100 + "A"
E11 = "GLYPH<c=3,font=/AAA>" * 20

SAMPLE = f"""# 카드 이용 안내서

## 이용 개요

이 안내서는 카드 발급부터 결제, 해지까지의 절차를 설명한다. 각 절차의 기준일과 필요 서류를 함께 적는다.

## 빈 태그 영역

{"<div></div>" * 10}

## 결제일 안내

결제일은 매월 14일이며 등록한 자동이체 계좌에서 출금된다. 잔액이 부족하면 다음 영업일에 다시 출금한다.

## 오류 화면 1

{E06}

## 일본어 안내

本契約は、会員がサービスを利用する際の条件を定めるものです。

## 오류 화면 2

{E08}
## 약관 요약

제1조 이 약관은 서비스 이용 조건을 정한다.

{"*" * 30}

제2조 회원은 약관에 동의한 자를 말한다.

## 깨진 본문 1

{E10}

## 상품 비교

| 번호 | 상품명 | 연회비 | 적립률 | 비고 |
| --- | --- | --- | --- | --- |
| 1 | 가온카드 | 10000 | 0.5 | 기본 |
| 2 | 나래카드 | 15000 | 0.7 | 여행 |
| 3 | 다솜카드 | 20000 | 1.0 | 쇼핑 |
| 4 | 라온카드 | 5000 | 0.3 | 학생 |
| 5 | 마루카드 | 30000 | 1.2 | 프리미엄 |

## 깨진 본문 2

{E11}

## 화면 코드 예시

```html
<div class="card">
  <span>{{{{ name }}}}</span>
</div>
```

## 해지 안내

카드 해지는 고객센터나 앱에서 신청한다. 해지 전에 남은 할부 금액과 포인트를 확인한다.
"""

ALL_BAD = f"""## 오류 화면 1

{E06}

## 오류 화면 2

{E08}
## 깨진 본문

{E10}
"""

_FAQ = [
    ("영업시간은 언제인가요?", "평일 9시부터 18시까지입니다."),
    ("카드 분실 신고는 어떻게 하나요?", "앱의 분실 신고 메뉴나 고객센터에서 바로 신고합니다."),
    ("결제일을 바꿀 수 있나요?", "월 1회 앱에서 변경할 수 있습니다."),
    ("연회비는 언제 청구되나요?", "발급 다음 달 결제일에 청구됩니다."),
    ("포인트 유효기간은?", "적립일로부터 5년입니다."),
]


def _row(index: int, content: str, question: str = "") -> dict:
    return {"category": "tabular_row", "content": content, "page": 1, "id": index,
            "metadata": {"question": question}}


ROWS = {"elements": [
    *(_row(i, f"Q: {q} A: {a}", q) for i, (q, a) in enumerate(_FAQ)),
    _row(len(_FAQ), ""),                       # 빈 행 → blank
    _row(len(_FAQ) + 1, "- - - * * * ###"),    # 기호만 있는 행 → no_content
]}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "chunk_quality_sample.md").write_text(SAMPLE, encoding="utf-8")
    (OUT_DIR / "chunk_quality_all_bad.md").write_text(ALL_BAD, encoding="utf-8")
    (OUT_DIR / "chunk_quality_rows.json").write_text(
        json.dumps(ROWS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"생성: {OUT_DIR}/chunk_quality_{{sample.md,all_bad.md,rows.json}}")


if __name__ == "__main__":
    main()
