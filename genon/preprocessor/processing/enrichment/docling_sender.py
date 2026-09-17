"""docling_sender.py — docling 이 만든 모델 요청을 전처리기가 대신 보낸다.

## 왜 있나

TOC(목차)와 문서 메타데이터 추출은 프롬프트 조립·분할·토큰 사전검사가 docling 포크 안에
있고, 그래서 HTTP 호출도 거기서 일어났다. 그 결과 이 두 섹션만 `params`·`headers`·`timeout`
을 설정할 수 없었고, 재시도도 없었다 — 나머지 섹션은 전처리기 쪽 공용 경로
(`llm_response`, `image_request`)가 전부 갖고 있는 것들이다.

이 모듈은 그 경계를 뒤집는다. **docling 은 무엇을 물어볼지만 정하고, 실제 전송은 여기서
한다.** docling 쪽에 남은 것은 `DataEnrichmentOptions.chat_sender` 로 이 함수를 받아
`requests.post` 대신 부르는 배선뿐이다(설정하지 않으면 종전 경로가 그대로 돈다).

## payload 를 여기서 바꾸지 않는 이유

docling 의 LLM 캐시 키가 `(url, payload)` 다. 여기서 payload 에 값을 더하면 캐시 키가 그
변화를 보지 못해 서로 다른 설정이 같은 캐시 항목을 나눠 쓰게 된다. 그래서 임의 파라미터
(`params`)만은 docling 이 payload 를 만들 때 합치고(`DataEnrichmentOptions.*_params`),
여기서는 캐시 키와 무관한 것(헤더·타임아웃·재시도·응답 해석)만 맡는다.

## 실패를 LLMApiError 로 돌려주는 이유

docling 의 TOC 분할 폴백이 `_is_token_overflow(err)` 로 이 예외의 상태 번호와 본문을 읽고,
파서의 에러 봉투도 같은 타입을 읽는다. 전처리기 쪽 예외를 그대로 올리면 두 판정이 조용히
꺼진다.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, Optional

import httpx

from docling.prompts.prompt_manager import LLMApiError
from docling.utils.llm_cache import remaining_timeout
from docling.utils.thinking import strip_reasoning

from genon.preprocessor.processing.common.model_params import resolve_headers
from genon.preprocessor.processing.enrichment.llm_response import post_chat_completion_sync

_log = logging.getLogger(__name__)

# docling 이 쓰던 기본값. 설정에 timeout 이 없으면 그대로 둔다(동작 보존).
DEFAULT_TIMEOUT = 3600.0


def make_chat_sender(
    *,
    api_key: str = "",
    headers: Optional[Mapping] = None,
    timeout: Optional[float] = None,
) -> Callable[..., Optional[str]]:
    """`DataEnrichmentOptions.chat_sender` 에 넣을 전송 함수를 만든다.

    돌려주는 함수의 계약은 docling 쪽 호출부와 같다 —
    `sender(url=..., payload=..., headers=...) -> Optional[str]`.

    Args:
        api_key: 인증키. `headers` 가 `Authorization` 을 직접 적었으면 그쪽이 이긴다.
        headers: 설정의 임의 요청 헤더.
        timeout: 설정의 호출 타임아웃(초). 없으면 docling 이 쓰던 3600 을 그대로 쓴다.
    """
    call_timeout = float(timeout) if timeout else DEFAULT_TIMEOUT
    req_headers = resolve_headers({"headers": headers}, api_key)

    def _send(*, url: str, payload: dict, headers: Optional[Mapping] = None) -> Optional[str]:
        # docling 이 만든 headers 는 Content-Type + Bearer 뿐이라 여기서 만든 것으로 갈음한다.
        # 설정의 임의 헤더가 실리는 자리가 이곳뿐이다.
        _ = headers
        try:
            with httpx.Client(timeout=httpx.Timeout(remaining_timeout(call_timeout))) as client:
                message = post_chat_completion_sync(
                    client, url, payload=payload, headers=req_headers, timeout=call_timeout
                )
        except httpx.HTTPStatusError as exc:
            raise LLMApiError(exc.response.text, status_code=exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            # 연결 실패·시간 초과. 상태 번호가 없으므로 봉투도 번호 없이 간다.
            raise LLMApiError(str(exc)) from exc
        except Exception as exc:
            # 게이트웨이가 200 으로 준 에러 봉투(LLMResponseError)는 상태 번호를 속성으로
            # 싣는다. isinstance 로 가리지 않는 이유는 이 저장소가 같은 모듈을 두 경로
            # (`processing.*` / `genon.preprocessor.processing.*`)로 import 하는 자리가 있어
            # 클래스 객체가 갈릴 수 있어서다 — 그러면 잡아야 할 예외를 조용히 놓친다.
            # 상태 번호를 싣지 않는 예외(캐시 deadline 초과 등)는 그대로 올린다.
            if not hasattr(exc, "status_code"):
                raise
            raise LLMApiError(str(exc), status_code=getattr(exc, "status_code")) from exc
        return strip_reasoning(message)

    return _send


def sender_from_config(conn: Any) -> Callable[..., Optional[str]]:
    """toc/metadata 설정 객체(`_TocConfig` 등)에서 바로 전송 함수를 만든다."""
    return make_chat_sender(
        api_key=getattr(conn, "api_key", "") or "",
        headers=getattr(conn, "headers", None),
        timeout=getattr(conn, "timeout", None),
    )
