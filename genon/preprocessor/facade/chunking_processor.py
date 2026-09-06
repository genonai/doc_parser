# 청킹 전용 전처리기 (Chunk API; 모니모 GenOS Temporal 파이프라인 #284)
#
# ── 이 파일에서 고칠 자리 ────────────────────────────────────────────────────
# 청킹 본체는 facade/core/chunker.py 에 있고 열어 볼 일이 없다. 사이트마다 실제로
# 달라지는 것은 아래 둘뿐이다.
#
#   1) GenOSVectorMeta    적재 DB 컬럼. 청크 1건 = 1행.
#   2) GenosSmartChunker  청킹 동작 옵션과 헤더 구분자.
#
# 청크 크기·모드·헤더 부착 여부는 코드가 아니라 chunking_processor_config.yaml 이다.
#
# 코드서빙은 단 하나의 facade 파일만 /app/src/preprocessor.py 로 마운트하므로 이 파일은
# 다른 facade 를 import 하지 않는 자기완결 파일이다. core/ 와 chunking/ 등 공용 하위
# 모듈은 배포본에 함께 들어가므로 import 해도 된다.
from __future__ import annotations

from typing import Optional

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

    # 그림 annotation 텍스트를 청크 본문에 싣는다.
    PICTURE_ANNOTATION_TEXT = True
    # 표 설명 annotation 반영 범위: 검색 설명 접두만 붙인다.
    TABLE_DESCRIPTION_MODE = "prefix_only"

    # 한 경로 안의 레벨 구분자(부모 → 자식). heading 에 콤마가 들어있는 경우가 있어
    # (실측 409건 중 20건) 콤마로는 경로를 레벨 단위로 되돌릴 수 없다.
    # 예: "제4조(여비) ① 여비는 여객운임, 숙박비, 식비 …". " > " 는 실측 충돌이 0 이다.
    CHUNK_HEADER_SEP = " > "
    # 서로 다른 경로(형제 섹션) 사이 구분자. 경로 내부 구분자와 반드시 달라야
    # `A > B`(부모-자식)와 `A | B`(형제)가 구분된다.
    CHUNK_PATH_SEP = " | "
    # 다경로 청크에서 나열할 리프 최대 개수. 초과분은 "… 외 N개" 로 접는다
    # (실측: hwp 71경로 → 헤더 3,239자, 청크가 chunk_size 를 30% 초과).
    CHUNK_PATH_MAX_LEAVES = 5


class DocumentProcessor(ChunkerCore):
    """파싱 결과를 입력받아 청킹만 수행한다 (Chunk API, #284).

    IS_CHUNKER: main.py 가 이 프로세서가 /chunker API 전용임을 식별하는 데 사용.
    """

    IS_CHUNKER: bool = True

    VECTOR_META = GenOSVectorMeta
    CHUNKER = GenosSmartChunker
