"""model_params.py — 모델 요청 파라미터를 한 가지 규칙으로 읽고 조립한다.

## 왜 있나

모델을 부르는 섹션마다 설정할 수 있는 옵션이 달랐다. 원인은 payload 를 만드는 방식이
두 갈래였기 때문이다 — 어떤 자리는 키를 코드에 나열해 그 목록 밖의 값은 아예 보낼 수
없었고(toc/metadata/custom_fields/doc_summary), 어떤 자리는 `params` dict 를 통째로
펼쳐 무엇이든 보낼 수 있었다(이미지·표·페이지 설명). 그래서 `seed` 는 toc 만, `headers` 는
설명 계열만 되는 식으로 표가 톱니처럼 갈렸다.

이 모듈은 그 두 갈래를 하나로 모은다. 설정을 읽는 쪽은 `collect_generation_params` 로
같은 키 집합을 같은 규칙으로 읽고, 요청을 만드는 쪽은 `build_chat_payload` 로 같은 모양의
payload 를 만든다.

## 우선순위

이름 있는 키(`temperature` …)와 임의 키 통로(`params`)에 같은 이름이 있으면 **`params` 가
이긴다.** `params` 는 운영자가 "검사하지 말고 이대로 보내라" 고 적는 탈출구이기 때문이다.

docling 타입에 의존하지 않는다(배포본이 docling 버전에 묶이지 않게 한다).
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from .config_parse import parse_optional_float, parse_optional_int

_log = logging.getLogger(__name__)

# 모델 요청에 실리는 생성 파라미터. 값을 만드는 방법이 같으므로 한 곳에 적는다.
# 여기 없는 키는 `params` 로 보낸다 — 새 키가 생길 때마다 코드를 고치지 않기 위한 통로다.
FLOAT_KEYS = ("temperature", "top_p", "repetition_penalty")
INT_KEYS = ("max_tokens", "seed")
GENERATION_KEYS = FLOAT_KEYS + INT_KEYS


def collect_generation_params(cfg: Optional[Mapping], *, label: str = "") -> dict:
    """설정 블록에서 생성 파라미터를 모아 payload 에 실을 dict 하나로 돌려준다.

    **설정에 적힌 키만 담는다.** 코드 기본값을 여기서 채우지 않는 것이 중요하다 — 담아
    버리면 호출부가 갖고 있는 typed 기본값을 덮어써서, 설정하지 않은 값이 설정한 것처럼
    동작한다.

    Args:
        cfg: enrichment 블록 하나(예: `metadata:` 아래). `params` 하위 dict 도 함께 읽는다.
        label: 경고 로그에 찍을 자리 이름.
    """
    cfg = cfg if isinstance(cfg, Mapping) else {}
    where = f"[{label}] " if label else ""
    out: dict[str, Any] = {}

    for key in FLOAT_KEYS:
        if key in cfg:
            value = parse_optional_float(cfg.get(key), f"{label}.{key}" if label else key)
            if value is not None:
                out[key] = value
    for key in INT_KEYS:
        if key in cfg:
            value = parse_optional_int(cfg.get(key), f"{label}.{key}" if label else key)
            if value is not None:
                out[key] = value

    extra = cfg.get("params")
    if isinstance(extra, Mapping):
        # 임의 키 통로가 최종이다. 값 검사를 하지 않는 것이 이 통로의 목적이다.
        out.update(dict(extra))
    elif extra is not None:
        _log.warning(f"{where}params 는 매핑이어야 합니다(받은 것: {type(extra).__name__}). 무시합니다.")
    return out


def resolve_headers(
    cfg: Optional[Mapping], api_key: str = "", *, base: Optional[Mapping] = None
) -> dict:
    """요청 헤더를 만든다 — 설정의 `headers` + Content-Type + Authorization.

    `Authorization` 을 설정이 직접 적었으면 덮어쓰지 않는다. 게이트웨이가 Bearer 가 아닌
    인증을 쓰는 현장이 있고, 그때 api_key 를 비워 두고 헤더로 넣는 것이 유일한 방법이다.
    """
    headers: dict[str, str] = dict(base or {})
    raw = (cfg or {}).get("headers") if isinstance(cfg, Mapping) else None
    if isinstance(raw, Mapping):
        headers.update({str(k): str(v) for k, v in raw.items()})
    elif raw is not None:
        _log.warning(f"headers 는 매핑이어야 합니다(받은 것: {type(raw).__name__}). 무시합니다.")
    headers.setdefault("Content-Type", "application/json")
    if api_key and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def resolve_thinking(thinking: Optional[str] = None, dialect: Optional[str] = "standard") -> tuple:
    """thinking / thinking_dialect 원값을 정규화한다. `(thinking, dialect)` 로 돌려준다.

    같은 판정이 세 곳(enrichment_config._parse_thinking, custom_fields_enricher,
    doc_summary)에 필요했는데 dialect 검증이 빠진 채 복제된 자리가 있었다(custom_fields).
    여기 한 벌만 두고 나머지는 이 함수를 부른다.

    Args:
        thinking: "on"/"off"/"auto" 등 원값. `None` 이면 "off"(추론 차단이 기본값).
        dialect: "standard"(기본) | "hcx". 이 둘이 아니면 "standard" 로 떨어뜨린다 —
            오타를 그대로 내보내면 게이트웨이가 모르는 키로 요청이 나간다.
    """
    resolved_thinking = "off" if thinking is None else str(thinking).strip().lower()
    resolved_dialect = str(dialect or "standard").strip().lower()
    if resolved_dialect not in {"standard", "hcx"}:
        resolved_dialect = "standard"
    return resolved_thinking, resolved_dialect


def build_chat_payload(
    *,
    model: str,
    messages: list,
    params: Optional[Mapping] = None,
    thinking_kwargs: Optional[Mapping] = None,
    **typed: Any,
) -> dict:
    """chat/completions 요청 본문을 만든다. 모든 텍스트 모델 호출이 여기를 지난다.

    Args:
        typed: 호출부가 dataclass 로 들고 있는 기본값(`temperature=0.0` 등). `None` 은 뺀다.
        params: `collect_generation_params` 결과. 설정에 적힌 값이라 typed 를 이긴다.
        thinking_kwargs: 추론 모드 토큰. 비어 있으면 키 자체를 넣지 않는다 —
            빈 dict 를 보내면 모델이 기본값을 쓰지 않고 빈 설정으로 읽는 게이트웨이가 있다.
    """
    payload: dict[str, Any] = {"model": model or "model", "messages": messages}
    for key, value in typed.items():
        if value is not None:
            payload[key] = value
    if params:
        payload.update(dict(params))
    if thinking_kwargs:
        payload["chat_template_kwargs"] = dict(thinking_kwargs)
    return payload
