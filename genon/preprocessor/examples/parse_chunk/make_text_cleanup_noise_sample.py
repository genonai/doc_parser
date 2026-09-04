#!/usr/bin/env python3
"""text_cleanup_noise_sample.md 재생성 — 청크 텍스트 패턴 후처리 검증용 원천.

`chunking.text_cleanup.rules` 가 지워야 할 노이즈와, **지우면 안 되는 대조군**을 한 파일에
담는다. 대조군이 이 샘플의 핵심이다 — 삭제가 되는지보다 삭제되면 안 되는 것이 남는지가
이 기능의 위험이다.

지워야 할 것
  1. 단독 `목차` 줄                      — `{line: "^\\s*목차\\s*$"}`
  2. `[이미지1]` 류 자리표시자            — `{find: "\\[이미지[^\\]]*\\]", replace: ""}`
  3. "본 문서는 참고용" 으로 시작하는 문단 — `{chunk: "^본 문서는 참고용"}`

남아야 할 것(대조군)
  4. 표 안의 `목차` 셀        — 표 행을 지우면 표가 통째로 깨진다
  5. 코드 펜스 안의 `[이미지1]`·`목차` — 코드 구간은 어떤 규칙도 타지 않는다
  6. `목차를 참고하세요` 처럼 단독 줄이 아닌 문장 — 정규식이 `^…$` 로 묶여 있다

실 고객 데이터를 쓰지 않고 값은 지어냈다.
샘플이 다시 바뀌면 손으로 md 를 만지지 말고 이 스크립트를 고쳐 다시 돌린다.

실행:  genon/preprocessor/.venv/bin/python examples/parse_chunk/make_text_cleanup_noise_sample.py
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "sample_files" / "text_cleanup_noise_sample.md"

TEXT = """# 사내 규정 안내

목차

1. 휴가 신청
2. 근태 기록

## 휴가 신청

[이미지1] 휴가 신청은 사내 포털에서 한다. 승인 전에는 일정이 확정되지 않는다.
자세한 절차는 목차를 참고하세요.

## 근태 기록

| 구분 | 설명 |
| --- | --- |
| 목차 | 문서 앞머리의 항목 목록 |
| 출퇴근 | 사원증 태깅으로 기록된다 |

[이미지2]

근태는 매월 말일에 마감한다.

## 예시 코드

```python
# 목차
sections = ["[이미지1]", "목차"]
```

## 고지

본 문서는 참고용이며 실제 적용은 인사규정을 따른다.
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(TEXT, encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
