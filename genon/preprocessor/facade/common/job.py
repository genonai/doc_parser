"""요청 한 건의 파싱 상태를 담는 값 객체(job)와 라우트 호출 규약.

흐름 메소드(_start_job → 라우트 → post_parse)가 인자 하나(job)를 주고받게 한다.
라우트는 새 시그니처 `route_x(self, job)` 와 옛 시그니처 `route_x(self, file_path, ext, ctx,
**kwargs)` 를 모두 받는다. 옛 시그니처로 작성한 고객 라우트가 릴리스 갱신 뒤에도 그대로 돌아야
하기 때문이다.

docling 타입을 import 하지 않는다 — 배포본이 docling 버전에 묶이지 않게 한다.
"""

from __future__ import annotations

import inspect
import tempfile
from dataclasses import dataclass, field, replace
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

    temp_dir() 로 만든 임시 디렉터리는 요청이 끝날 때 close() 가 지운다. params 만 바꾼 사본
    (with_params)과 목록을 공유하므로 사본에서 만든 디렉터리도 함께 지워진다.
    """

    request: Any
    file_path: str
    ext: str
    doc_type: Optional[str]
    params: dict
    source: str = ""
    ctx: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)
    _temp_dirs: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        if not self.source:
            self.source = self.file_path

    def temp_dir(self, prefix: str) -> str:
        """요청이 끝날 때 지워지는 임시 디렉터리를 만든다."""
        tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self._temp_dirs.append(tmp)
        return tmp.name

    def close(self) -> None:
        """이 요청이 만든 임시 디렉터리를 모두 지운다."""
        while self._temp_dirs:
            self._temp_dirs.pop().cleanup()


def with_params(job: ParseJob, params: dict, ctx: Optional[dict] = None) -> ParseJob:
    """params(와 ctx)만 바꾼 사본. 옛 시그니처 라우트가 넘긴 kwargs 를 그대로 쓰기 위해 둔다."""
    return replace(job, params=params, ctx=job.ctx if ctx is None else ctx)


@dataclass
class ChunkJob:
    """청커 요청 한 건. 파서의 ParseJob 과 다른 객체다.

    kind       "docling"(문서형) | "parse"(행형)
    data       파서 결과. 문서형은 DoclingDocument 또는 직렬화한 dict, 행형은 list[dict]
    params     요청 파라미터(kwargs)
    guardrail  민감정보 컨텍스트(#315). core 가 청킹 단계로 그대로 넘긴다
    doc_type   요청이 선언한 문서 유형
    document   split_document 가 복원한 DoclingDocument. 벡터 조합이 다시 쓴다
    notes      훅 메소드 사이 값 전달용 dict
    """

    request: Any = None
    file_path: str = ""
    kind: str = ""
    data: Any = None
    params: dict = field(default_factory=dict)
    guardrail: dict = field(default_factory=dict)
    doc_type: Optional[str] = None
    document: Any = None
    config: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)


@dataclass
class Chunk:
    """분할 결과 1건. 입력 형식과 관계없이 같은 필드를 갖는다.

    필드 이름은 on_chunk 의 info 와 맞춘다 — 훅에서 보던 이름을 그대로 쓴다.

    text      본문 원문(문서 접두어·헤딩 경로를 붙이기 전)
    kind      "docling"(문서형) | "row"(엑셀 행·JSON 레코드) | "text"(그 밖) | "marker"(audio·[DA])
    page      페이지 번호. 문서형은 prov 가 없으면 0 이다
    headings  헤딩 경로. 문서형에만 있다
    metadata  레코드 메타데이터. 행형에만 있다
    source    원본 객체. bbox·표 조각 계산처럼 형식을 알아야 하는 자리에서만 쓴다
              (문서형 DocChunk / 텍스트형 langchain Document / 행형 element dict)
    """

    text: str = ""
    kind: str = ""
    page: int = 1
    headings: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    source: Any = None


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
