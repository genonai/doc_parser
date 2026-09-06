# 청킹 전용 전처리기 (Chunk API; 모니모 GenOS Temporal 파이프라인 #284)
#
# ── 이 파일에서 고칠 자리 ────────────────────────────────────────────────────
# 청킹 본체는 facade/core/chunker.py 에 있고 열어 볼 일이 없다.
#
#   GenOSVectorMeta    적재 DB 컬럼. 청크 1건 = 1행.
#   GenosSmartChunker  청킹 동작 옵션과 헤더 구분자.
#   pre_chunk          파서 산출을 청킹 직전에 손볼 때.
#   post_chunk         완성된 청크를 손볼 때.
#
# 청크 크기·모드·헤더 부착 여부는 코드가 아니라 chunking_processor_config.yaml 이다.
from __future__ import annotations

from typing import Optional

from fastapi import Request

from pydantic import BaseModel

from genon.preprocessor.facade.chunking import smart_chunker as sc
# main.py 의 예외 핸들러가 이 이름으로 잡는다. core 가 던지는 것과 같은 클래스다.
from genon.preprocessor.facade.core.errors import GenosServiceException  # noqa: F401
from genon.preprocessor.facade.core.chunker import ChunkerCore


class GenOSVectorMeta(BaseModel):
    """청크 1건 = 적재 DB 1행. 선언에 없는 키도 그대로 실린다(extra=allow)."""

    class Config:
        extra = 'allow'
    text: str = None
    n_char: int = None
    n_word: int = None
    n_line: int = None
    e_page: int = None
    i_page: int = None
    i_chunk_on_page: int = None
    n_chunk_of_page: int = None
    i_chunk_on_doc: int = None
    n_chunk_of_doc: int = None
    n_page: int = None
    reg_date: str = None
    chunk_bboxes: str = None
    media_files: str = None
    title: str = None
    created_date: int = None
    appendix: str = None ## !! appendix feature (2025-09-30, geonhee kim) !!
    file_path: Optional[str] = None
    guardrail_categories: Optional[list] = None  # #315 민감정보 분류 라벨. 미적용 시 None
    # 표 메타(#360). 표 청크만 골라 검색하거나 나뉜 조각을 원래 순서로 잇는 데 쓴다.
    has_table: bool = False
    table_refs: Optional[str] = None
    table_split_index: Optional[int] = None
    table_split_total: Optional[int] = None


class GenosSmartChunker(sc.SmartChunkerBase):
    """청킹 본체는 facade/chunking/smart_chunker.py 다. 여기엔 고른 옵션만 둔다."""

    PICTURE_ANNOTATION_TEXT = True          # 그림 annotation 을 청크 본문에 싣는다
    TABLE_DESCRIPTION_MODE = "prefix_only"  # 표 설명은 검색용 접두만

    # 경로 안 구분자(부모 → 자식). heading 에 콤마가 든 경우가 있어(실측 409건 중 20건)
    # 콤마로는 레벨을 되돌릴 수 없다. " > " 는 실측 충돌이 0 이다.
    CHUNK_HEADER_SEP = " > "
    # 형제 경로 사이 구분자. 경로 안 구분자와 달라야 `A > B`(부모-자식)와 `A | B`(형제)가
    # 구분된다.
    CHUNK_PATH_SEP = " | "
    # 다경로 청크에서 나열할 리프 최대 개수. 초과분은 "… 외 N개" 로 접는다
    # (실측: hwp 71경로 → 헤더 3,239자, chunk_size 를 30% 초과).
    CHUNK_PATH_MAX_LEAVES = 5


class DocumentProcessor(ChunkerCore):
    """파싱 결과를 입력받아 청킹만 수행한다 (Chunk API, #284).

    IS_CHUNKER: main.py 가 이 프로세서가 /chunker API 전용임을 식별하는 데 사용.
    """

    IS_CHUNKER: bool = True

    VECTOR_META = GenOSVectorMeta
    CHUNKER = GenosSmartChunker

    async def __call__(self, request: Request, file_path: str = "", **kwargs):
        """① 입력 판별 → ② pre_chunk → ③ 분할·벡터 조합 → ④ post_chunk"""
        src = self.load_input(file_path, **kwargs)
        src.data = self.pre_chunk(src.kind, src.data, **kwargs)
        vectors = await self.chunk(request, file_path, src, **kwargs)
        return self.post_chunk(vectors, **kwargs)

    def pre_chunk(self, kind, data, **kwargs):
        """[전처리] 분할 직전. 받은 형 그대로 돌려준다.

        kind=="docling" 이면 data 는 DoclingDocument, "parse" 면 list[dict] 다.
        """
        return data

    def post_chunk(self, vectors, **kwargs):
        """[후처리] 응답 직전. list[GenOSVectorMeta] 를 손본다(필드 추가·청크 제거)."""
        return vectors
