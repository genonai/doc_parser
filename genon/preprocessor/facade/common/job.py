"""요청 한 건의 파싱 상태를 담는 값 객체(job)와 라우트 호출 규약.

흐름 메소드(_start_job → 라우트 → post_parse)가 인자 하나(job)를 주고받게 한다.
라우트는 새 시그니처 `route_x(self, job)` 와 옛 시그니처 `route_x(self, file_path, ext, ctx,
**kwargs)` 를 모두 받는다. 옛 시그니처로 작성한 고객 라우트가 릴리스 갱신 뒤에도 그대로 돌아야
하기 때문이다.

docling 타입을 import 하지 않는다 — 배포본이 docling 버전에 묶이지 않게 한다.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Optional


@dataclass
class ParseJob:
    """파서 요청 한 건.

    file_path  요청이 넘긴 원본 경로. 처리 중에 바뀌지 않는다
    source     실제로 파싱할 입력 경로. 확장자 별칭 사본이나 pre_parse 파생 파일이면 원본과 다르다
    ext        표준 확장자(별칭 적용 후, 소문자)
    doc_type   정규화된 문서 유형
    params     요청 파라미터(kwargs)
    ctx        라우트 사이 공유 상태(enrichment_context, artifacts_source)
    notes      훅 메소드 사이 값 전달용 dict
    """

    request: Any
    file_path: str
    ext: str
    doc_type: Optional[str]
    params: dict
    source: str = ""
    ctx: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.source:
            self.source = self.file_path


@lru_cache(maxsize=256)
def _first_param(func: Callable) -> Optional[str]:
    try:
        params = list(inspect.signature(func).parameters.values())
    except (TypeError, ValueError):  # 시그니처를 읽을 수 없는 호출가능 객체
        return None
    if params and params[0].name == "self":  # 언바운드 함수
        params = params[1:]
    return params[0].name if params else None


def takes_job(route: Callable) -> bool:
    """라우트가 새 시그니처 route_x(self, job) 인가."""
    func = getattr(route, "__func__", route)  # 바인딩된 메서드는 매번 새 객체라 원본으로 캐시한다
    return _first_param(func) == "job"


async def call_route(route: Callable, job: ParseJob):
    """라우트를 시그니처에 맞춰 부른다."""
    if takes_job(route):
        return await route(job)
    return await route(job.source, job.ext, job.ctx, **job.params)
