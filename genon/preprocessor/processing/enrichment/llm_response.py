"""LLM(chat completion) 응답 해석과 일시적 실패 재시도 한 벌.

게이트웨이가 HTTP 200 으로 에러 봉투를 돌려주는 경우가 있어 `raise_for_status()` 를 그대로
통과한다. 응답을 바로 인덱싱하면 `KeyError('choices')` 만 남아 서버가 준 사유를 되짚을 수
없으므로, 형식이 어긋나면 응답 본문을 실은 오류로 바꾼다. 봉투가 상태 번호를 밝히면 그 번호를
`status_code` 로 실어 둔다 — `classify_error` 가 그 속성을 읽어 transient/permanent 를 가른다.

재시도는 한 번뿐이고, 생성이 시작되기 전에 실패한 것으로 볼 수 있는 경우만 다시 부른다.
표 설명·레코드 필드처럼 실패해도 문서를 막지 않는 경로는 호출 측(Temporal) 재시도가 닿지
않으므로, 여기가 그 경로들의 유일한 재시도 수단이다.

같은 판정이 필요한 곳이 셋(custom_fields / metadata / body_summary)이라 여기 한 벌만 둔다.
body_summary 는 동기 호출이라 기다리는 동안 이벤트 루프를 막으므로 재시도는 쓰지 않는다.
"""

import asyncio
import json
import logging
import random
from typing import Any, Optional

import httpx

from docling.utils.llm_cache import CacheDeadlineExceeded, remaining_timeout

_log = logging.getLogger(__name__)

# 형식이 어긋난 LLM 응답을 오류 메시지에 실을 때의 본문 길이 상한.
ERROR_BODY_CHARS = 500

# 다시 불러 볼 만한 상태 번호. 500 은 뺀다 — vLLM 의 500 은 같은 입력에서 반복되는 경우가 많다.
RETRY_STATUS = frozenset({429, 502, 503, 504})

# 재시도 전 대기(초). 동시 호출이 한꺼번에 몰리지 않도록 범위 안에서 무작위로 고른다.
_RETRY_DELAY_RANGE = (1.0, 2.0)
# 서버가 Retry-After 로 더 기다리라고 해도 이 이상은 기다리지 않는다.
_RETRY_AFTER_CAP = 10.0


class LLMResponseError(ValueError):
    """chat completion 형식이 아닌 응답. 봉투가 밝힌 상태 번호를 `status_code` 로 싣는다."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
    raise LLMResponseError(
        f"LLM 응답이 chat completion 형식이 아닙니다: {_body_snippet(data)}",
        status_code=_envelope_status(data),
    )


async def post_chat_completion(
    client: Any, url: str, *, payload: dict, headers: dict, timeout: float
) -> Any:
    """chat completion 을 호출해 message 를 돌려준다. 일시적 실패는 한 번만 다시 부른다.

    시도마다 남은 요청 시간으로 제한시간을 다시 잡는다 — 첫 시도가 시간을 쓴 만큼 재시도는
    짧아져야 요청 deadline 을 넘기지 않는다.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = await client.post(
                url, json=payload, headers=headers,
                timeout=httpx.Timeout(remaining_timeout(timeout)),
            )
            resp.raise_for_status()
            return chat_completion_message(resp.json())
        except Exception as exc:
            if attempt > 1 or not is_retryable(exc):
                raise
            delay = _retry_delay(exc)
            if not _deadline_allows(delay):
                raise
            _log.warning(f"[llm] 일시적 실패로 {delay:.1f}초 후 한 번 더 호출합니다: {exc}")
            await asyncio.sleep(delay)


def is_retryable(exc: BaseException) -> bool:
    """다시 불러 볼 만한 실패인가.

    시간 초과는 모두 뺀다. 호출 제한시간이 연결 대기에도 똑같이 걸려 있어(최대 600초)
    한 번만 재시도해도 호출 하나가 두 배로 늘어난다.
    """
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRY_STATUS
    if isinstance(exc, LLMResponseError):
        return exc.status_code in RETRY_STATUS
    return False


def _envelope_status(data: Any) -> Optional[int]:
    """에러 봉투가 스스로 밝힌 HTTP 상태 번호. 없으면 None(분류는 종전대로 permanent).

    확인된 형식은 vLLM 의 최상위 정수 `code` 하나다. 다른 키나 문자열 번호는 실제 표본이
    나온 뒤에 더한다 — 표본 없이 넓히면 의도하지 않은 응답까지 분류가 바뀐다.
    """
    if not isinstance(data, dict):
        return None
    code = data.get("code")
    if isinstance(code, int) and not isinstance(code, bool) and 400 <= code <= 599:
        return code
    return None


def _retry_delay(exc: BaseException) -> float:
    delay = random.uniform(*_RETRY_DELAY_RANGE)
    response = getattr(exc, "response", None)
    header = response.headers.get("Retry-After") if response is not None else None
    if isinstance(header, str) and header.strip().isdigit():
        delay = max(delay, min(float(header.strip()), _RETRY_AFTER_CAP))
    return delay


def _deadline_allows(delay: float) -> bool:
    """기다린 뒤에도 요청 시간이 남는가. deadline 이 없으면 항상 참이다."""
    try:
        return remaining_timeout(delay) >= delay
    except CacheDeadlineExceeded:
        return False


def _body_snippet(data: Any) -> str:
    """오류 메시지에 실을 응답 본문. 길면 잘라 로그가 부풀지 않게 한다."""
    try:
        body = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        body = str(data)
    if len(body) > ERROR_BODY_CHARS:
        return f"{body[:ERROR_BODY_CHARS]}...(이하 생략)"
    return body
