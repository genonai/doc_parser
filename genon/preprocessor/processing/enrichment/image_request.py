"""image_request.py — VLM(비전) 이미지 설명 요청 공용 함수.

page_description / image_description / table_description 세 곳이 각자 `api_image_request`
를 부르면서 조금씩 다르게 처리했다 — model 을 params 에 넣는 3줄이 세 곳에 복제됐고,
재시도가 없어 일시적 502 하나로 그 이미지·표·페이지의 설명이 조용히 사라졌고, thinking 을
설정할 방법이 없었고(사람이 직접 params.chat_template_kwargs 를 적어야만 가능), 응답에
`strip_reasoning` 이 적용되지 않았다. 이 모듈이 그 요청 경로 한 벌을 담당한다.

`api_image_request` 자체(docling)는 그대로 쓴다 — 헤더 조립·model 주입·재시도·thinking
토글·reasoning 제거만 이 모듈이 감싼다.

## thinking 기본값이 텍스트 섹션과 다른 이유

텍스트 3곳(metadata/custom_fields/body_summary)의 thinking 기본값은 "off"다 — 이미 같은
게이트웨이에 `chat_template_kwargs` 를 보내고 있었으므로 기본을 켜도 위험이 없었다.
VLM 은 dots-mocr 등 다른 서빙을 쓰는 현장이 있고, 그 채팅 템플릿이 모르는 kwarg 를 받으면
요청 자체가 실패할 수 있다. 그래서 여기 기본값은 "auto"(= `chat_template_kwargs` 미전송)로
둔다 — 설정을 바꾸지 않은 현장 세 곳의 요청이 이번 변경으로 한꺼번에 달라지지 않도록 하는
장치다. thinking 을 쓰려는 현장은 `thinking: off|on` 을 명시하면 된다.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from docling.utils.api_image_request import api_image_request
from docling.utils.thinking import resolve_thinking_kwargs, strip_reasoning

from genon.preprocessor.processing.common.model_params import resolve_headers
from genon.preprocessor.processing.enrichment.llm_response import retry_once_sync


def request_image_description(
    *,
    image: Any,
    prompt: str,
    url: str,
    api_key: str = "",
    model: str = "",
    headers: Optional[Mapping] = None,
    params: Optional[Mapping] = None,
    timeout: float = 360.0,
    thinking: str = "auto",
    thinking_dialect: str = "standard",
) -> str:
    """VLM 에 이미지+프롬프트를 보내 설명 문자열을 반환한다.

    page/image/table description 세 곳이 공유하는 유일한 요청 경로다. 호출부는 자신의
    문맥(before/after/caption 등)으로 만든 prompt 와 옵션만 넘기면 된다.

    Args:
        headers: 옵션의 `headers`(임의 요청 헤더). `resolve_headers` 가 Content-Type/
            Authorization 을 덧붙인다.
        params: 옵션의 `params`(temperature 등 임의 생성 파라미터 passthrough).
        thinking/thinking_dialect: `model_params.resolve_thinking` 이 돌려준 값을 그대로
            받는다. 비어 있으면("auto") `chat_template_kwargs` 자체를 보내지 않는다.

    실패는 감추지 않고 그대로 올린다 — 항목 단위 fail-open(경고 후 건너뛰기)은 호출부의 몫이다.
    """
    req_headers = resolve_headers(None, api_key, base=headers)

    req_params = dict(params or {})
    if model and "model" not in req_params:
        req_params["model"] = model
    thinking_kwargs = resolve_thinking_kwargs(thinking, thinking_dialect)
    if thinking_kwargs and "chat_template_kwargs" not in req_params:
        req_params["chat_template_kwargs"] = thinking_kwargs

    def _send() -> str:
        return api_image_request(
            image=image,
            prompt=prompt,
            url=url,
            timeout=timeout,
            headers=req_headers,
            **req_params,
        )

    result = retry_once_sync(_send)
    return strip_reasoning(result)
