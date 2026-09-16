"""LLM(chat completion) 응답 해석 한 벌.

게이트웨이가 HTTP 200 으로 에러 봉투를 돌려주는 경우가 있어 `raise_for_status()` 를 그대로
통과한다. 응답을 바로 인덱싱하면 `KeyError('choices')` 만 남아 서버가 준 사유를 되짚을 수
없으므로, 형식이 어긋나면 응답 본문을 실은 오류로 바꾼다.

같은 판정이 필요한 곳이 셋(custom_fields / metadata / body_summary)이라 여기 한 벌만 둔다.
"""

import json
from typing import Any

# 형식이 어긋난 LLM 응답을 오류 메시지에 실을 때의 본문 길이 상한.
ERROR_BODY_CHARS = 500


def chat_completion_message(data: Any) -> Any:
    """chat completion 응답에서 message 를 꺼낸다. 그 형식이 아니면 본문을 실어 실패한다.

    message 는 dict 인 것이 보통이지만 문자열로 오는 서버도 있어 형태를 좁히지 않는다
    (본문 추출은 호출부의 `strip_reasoning` 등이 양쪽을 받는다).
    """
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict) and first.get("message") is not None:
                return first["message"]
    raise ValueError(f"LLM 응답이 chat completion 형식이 아닙니다: {_body_snippet(data)}")


def _body_snippet(data: Any) -> str:
    """오류 메시지에 실을 응답 본문. 길면 잘라 로그가 부풀지 않게 한다."""
    try:
        body = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        body = str(data)
    if len(body) > ERROR_BODY_CHARS:
        return f"{body[:ERROR_BODY_CHARS]}...(이하 생략)"
    return body
