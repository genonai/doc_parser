"""고객 확장 훅(edit_input / edit_document / edit_chunk / edit_output) 호출 규약.

core 가 훅을 부르는 자리는 전부 이 모듈을 거친다. 규약은 두 가지다.

1. **요청 파라미터 전달** — 훅이 `**kwargs` 를 선언했을 때만 요청 파라미터를 넘긴다.
   선언하지 않은 기존 훅은 인자가 늘지 않으므로 그대로 동작한다(릴리스 갱신 때
   고객이 보관해 둔 patch 가 깨지지 않아야 한다).
   훅이 이미 자리로 받는 이름(ext/doc_type/data/result 등)은 중복 인자가 되므로 뺀다.

2. **async 허용** — 훅이 코루틴을 돌려주면 await 한다. 사내 API 조회처럼 외부 호출이
   필요한 보강을 훅에서 할 수 있어야 하고, 그것을 동기로 하면 이벤트 루프가 막혀
   같은 서버의 다른 문서 요청까지 멈춘다.

프로세서는 모듈 레벨 싱글턴 하나가 모든 요청을 받는다. 훅에서 요청 상태를 `self` 에
두면 동시 요청끼리 섞이므로, 요청마다 달라지는 값은 이 통로(kwargs)로만 받는다.
"""

from __future__ import annotations

import inspect
from functools import lru_cache
from typing import Any, Callable

# 훅에 넘길 수 없는 요청 파라미터. core 가 내부 배관용으로 kwargs 에 실어 나르는 값이라
# 훅의 관심사가 아니고, 이름이 길어 훅 시그니처를 오염시킨다.
_INTERNAL_KEYS = frozenset({"_sensitive_infos", "_guardrail_masking", "_enrichment_context"})


@lru_cache(maxsize=256)
def _signature_of(func: Callable) -> tuple[bool, frozenset]:
    """(**kwargs 를 받는가, 이름으로 이미 받는 파라미터들)."""
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):  # 시그니처를 읽을 수 없는 호출가능 객체
        return False, frozenset()
    accepts_var_keyword = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
    named = frozenset(
        p.name for p in params if p.kind is not inspect.Parameter.VAR_KEYWORD
    )
    return accepts_var_keyword, named


def hook_kwargs(fn: Callable, request_kwargs: dict | None) -> dict:
    """훅에 실제로 넘길 요청 파라미터."""
    if not request_kwargs:
        return {}
    func = getattr(fn, "__func__", fn)  # 바인딩된 메서드는 매번 새 객체라 원본으로 캐시한다
    accepts_var_keyword, named = _signature_of(func)
    if not accepts_var_keyword:
        return {}
    return {
        key: value
        for key, value in request_kwargs.items()
        if key not in named and key not in _INTERNAL_KEYS
    }


async def call_hook(fn: Callable, *args, request_kwargs: dict | None = None) -> Any:
    """훅을 부르고, 코루틴이면 await 해서 결과를 돌려준다."""
    result = fn(*args, **hook_kwargs(fn, request_kwargs))
    if inspect.isawaitable(result):
        result = await result
    return result


class _Drop:
    """edit_chunk 가 "이 청크를 버린다" 고 말하는 표식. 인스턴스는 DROP 하나뿐이다."""

    __slots__ = ()

    def __repr__(self) -> str:  # 오류 메시지에 그대로 나온다
        return "tb.DROP"


# edit_chunk 에서 청크를 버릴 때 돌려준다. None 을 "버림" 으로 삼지 않는 이유는 return 을
# 빠뜨린 훅이 조용히 청크를 지우기 때문이다 — 버리는 것은 명시해야 한다.
DROP = _Drop()


async def call_chunk_hook(fn: Callable, text: str, info: dict,
                          request_kwargs: dict | None = None) -> tuple:
    """edit_chunk 결과를 (본문, 버릴지) 로 정규화한다.

      문자열       그 문자열이 청크 본문이 된다
      None         손대지 않는다(받은 본문 그대로)
      DROP / 공백  이 청크를 버린다
    """
    out = await call_hook(fn, text, info, request_kwargs=request_kwargs)
    if out is DROP:
        return None, True
    if out is None:
        return text, False
    if not isinstance(out, str):
        raise TypeError(
            f"edit_chunk 는 문자열이나 None, tb.DROP 을 돌려줘야 합니다: {type(out).__name__}")
    if not out.strip():
        return None, True
    return out, False
