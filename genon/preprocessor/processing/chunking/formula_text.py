"""수식 아이템을 청크 텍스트로 옮길 때의 표기.

## 왜 필요한가

마크다운 백엔드는 `$$ ... $$` 블록을 `formula` 라벨 아이템으로 만든다(docling 의
`backend/md_math.py`). 그런데 아이템의 `text` 에는 구분자가 없다. 그대로 청크에 실으면
블록 수식만 맨몸 LaTeX 로 남아, 같은 문서 안에서 인라인 수식은 `$...$` 로 실리는데
블록 수식은 아무 표시가 없는 불일치가 생긴다.

읽는 쪽에서 이 차이는 그냥 표기 문제가 아니다. 청크를 받는 LLM 이 `V_t = P \\times
a_{x+t}` 를 수식으로 알아보지 못하면 본문 문장으로 읽고, 첨자와 곱셈기호를 임의로
풀어 설명한다. 구분자가 그것을 막는다.

## duck typing 인 이유

배포본이 docling 버전에 묶이지 않도록 공용 모듈은 docling 타입을 import 하지 않는다.
라벨 값 문자열만 본다.
"""

from __future__ import annotations

from typing import Any, Optional

# docling_core 의 `DocItemLabel.FORMULA` 값. 타입을 import 하지 않으려고 값만 둔다.
FORMULA_LABEL = "formula"

BLOCK_DELIM = "$$"


def is_formula_item(item: Any) -> bool:
    """이 아이템이 블록 수식인가."""
    label = getattr(item, "label", None)
    value = getattr(label, "value", label)
    return isinstance(value, str) and value == FORMULA_LABEL


def wrap_block_formula(text: Optional[str]) -> Optional[str]:
    """블록 수식 본문에 `$$` 구분자를 씌운다.

    이미 씌워져 있으면 그대로 둔다. 백엔드가 구분자를 뗀 본문을 주지만, 다른 경로로 들어온
    아이템이 구분자를 달고 있을 수 있어 두 번 씌우지 않는다.
    """
    if text is None:
        return None
    body = text.strip()
    if not body:
        return text
    if body.startswith(BLOCK_DELIM) and body.endswith(BLOCK_DELIM) and len(body) > 4:
        return body
    return f"{BLOCK_DELIM}{body}{BLOCK_DELIM}"


def item_text(item: Any, text: Optional[str]) -> Optional[str]:
    """아이템에서 뽑은 텍스트를 청크에 실을 표기로 바꾼다."""
    if is_formula_item(item):
        return wrap_block_formula(text)
    return text
