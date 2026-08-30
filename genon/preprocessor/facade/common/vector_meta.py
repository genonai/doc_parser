"""벡터 메타데이터 빌더의 공통 코어.

facade 4종이 복제해 두었던 GenOSVectorMetaBuilder 에서 어느 facade나 같은 부분
(텍스트 통계, 페이지 정보, 청크 인덱스, bbox, 미디어 파일, 글로벌 메타데이터,
민감정보 라벨)만 뽑았다.

벡터 스키마(GenOSVectorMeta)와 그 facade 고유 필드(title, created_date, authors,
appendix, file_path 등)는 사이트마다 다르므로 각 facade 에 그대로 둔다. 파생 클래스가
__init__ 에서 고유 필드를 더하고 build() 에서 core_payload() 와 합친다.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from docling_core.types import DoclingDocument
from docling_core.types.doc import PictureItem, TableItem

# core_payload() 가 내보내는 공통 필드. build() 는 여기에 facade 고유 필드를 더한다.
CORE_FIELDS = (
    "text", "n_char", "n_word", "n_line",
    "i_page", "e_page", "i_chunk_on_page", "n_chunk_of_page",
    "i_chunk_on_doc", "n_chunk_of_doc", "n_page",
    "reg_date", "chunk_bboxes", "media_files", "guardrail_categories",
    "has_table", "table_refs", "table_split_index", "table_split_total",
)


class VectorMetaBuilderBase:
    """공통 세터를 제공하는 빌더 기반 클래스. build() 는 파생 클래스가 구현한다."""

    def __init__(self):
        self.text: Optional[str] = None
        self.n_char: Optional[int] = None
        self.n_word: Optional[int] = None
        self.n_line: Optional[int] = None
        self.i_page: Optional[int] = None
        self.e_page: Optional[int] = None
        self.i_chunk_on_page: Optional[int] = None
        self.n_chunk_of_page: Optional[int] = None
        self.i_chunk_on_doc: Optional[int] = None
        self.n_chunk_of_doc: Optional[int] = None
        self.n_page: Optional[int] = None
        self.reg_date: Optional[str] = None
        self.chunk_bboxes: Optional[str] = None
        self.media_files: Optional[str] = None
        self.guardrail_categories: Optional[list] = None  # #315 민감정보 분류 라벨
        # 표 관련 메타(#360). 표가 없는 청크는 has_table=False, 나머지는 None.
        self.has_table: bool = False
        self.table_refs: Optional[str] = None
        self.table_split_index: Optional[int] = None
        self.table_split_total: Optional[int] = None
        self.extra_metadata: dict[str, Any] = {}

    def set_guardrail_categories(self, guardrail_categories: Optional[list]):
        """#315 청크 민감정보 분류 라벨 설정 (부동산/인사/민감 등 리스트, 미적용/없음 시 None)"""
        self.guardrail_categories = guardrail_categories or None
        return self

    def set_text(self, text: str):
        """텍스트와 관련된 데이터를 설정"""
        self.text = text
        self.n_char = len(text)
        self.n_word = len(text.split())
        self.n_line = len(text.splitlines())
        return self

    def set_page_info(self, i_page: int, i_chunk_on_page: int, n_chunk_of_page: int):
        """페이지 정보 설정"""
        self.i_page = i_page
        self.i_chunk_on_page = i_chunk_on_page
        self.n_chunk_of_page = n_chunk_of_page
        return self

    def set_chunk_index(self, i_chunk_on_doc: int):
        """문서 전체의 청크 인덱스 설정"""
        self.i_chunk_on_doc = i_chunk_on_doc
        return self

    def set_global_metadata(self, **global_metadata):
        """글로벌 메타데이터 병합.

        빌더가 실제로 들고 있는 인스턴스 속성일 때만 덮어쓰고, 나머지는
        extra_metadata 로 흘린다. hasattr 로 판정하면 메서드 이름과 같은 키가
        들어왔을 때 메서드를 덮어쓴다 — 그래서 __dict__ 로 본다.
        """
        for key, value in global_metadata.items():
            if key in self.__dict__ and key != "extra_metadata":
                setattr(self, key, value)
            else:
                self.extra_metadata[key] = value
        return self

    def set_chunk_bboxes(self, doc_items: list, document: DoclingDocument):
        chunk_bboxes = []
        for item in doc_items:
            for prov in item.prov:
                size = document.pages.get(prov.page_no).size
                bbox = prov.bbox
                bbox_data = {'l': bbox.l / size.width,
                             't': bbox.t / size.height,
                             'r': bbox.r / size.width,
                             'b': bbox.b / size.height,
                             'coord_origin': bbox.coord_origin.value}
                chunk_bboxes.append({'page': prov.page_no, 'bbox': bbox_data,
                                     'type': item.label, 'ref': item.self_ref})
        self.e_page = max([bbox['page'] for bbox in chunk_bboxes]) if chunk_bboxes else 0
        self.chunk_bboxes = json.dumps(chunk_bboxes)
        return self

    def set_media_files(self, doc_items: list, include_tables: bool = False):
        temp_list = []
        for item in doc_items:
            if isinstance(item, PictureItem) and item.image:
                path = str(item.image.uri)
                temp_list.append({'name': path.rsplit("/", 1)[-1], 'type': 'image',
                                  'ref': item.self_ref})
            elif include_tables and isinstance(item, TableItem) and item.image:
                # 표 이미지는 picture 와 구분되도록 type='table_image' 로 기록한다.
                # ref(self_ref)는 chunk_bboxes 의 table 엔트리 ref 와 동일 → 조인 가능.
                path = str(item.image.uri)
                temp_list.append({'name': path.rsplit("/", 1)[-1], 'type': 'table_image',
                                  'ref': item.self_ref})
        self.media_files = json.dumps(temp_list)
        return self

    def set_table_info(self, doc_items: list, split_totals: Optional[dict] = None,
                       seen_counts: Optional[dict] = None):
        """청크가 담은 표를 메타데이터로 드러낸다.

        예전에는 chunk_bboxes 안 type 을 파헤쳐야만 표 청크인지 알 수 있었다. 하이브리드
        검색에서 표만 걸러 보거나, 나뉜 조각을 원래 순서로 다시 잇는 데 쓴다.

        ``split_totals`` 는 청커가 남긴 {self_ref: 조각 수} 이고, ``seen_counts`` 는
        호출부가 문서 단위로 들고 다니는 {self_ref: 지금까지 본 조각 수} 다. 같은 표가
        연속해서 나오는 순서가 곧 조각 순서다.
        """
        refs = [item.self_ref for item in doc_items if isinstance(item, TableItem)]
        self.has_table = bool(refs)
        self.table_refs = json.dumps(refs) if refs else None
        if not refs or not split_totals or seen_counts is None:
            return self
        # 한 청크에 표가 여럿이면 조각 순서라는 개념이 성립하지 않으므로 비워 둔다.
        total = split_totals.get(refs[0]) if len(refs) == 1 else None
        if total and total > 1:
            index = seen_counts.get(refs[0], 0)
            seen_counts[refs[0]] = index + 1
            self.table_split_index = index
            self.table_split_total = total
        return self

    def core_payload(self) -> dict:
        """모든 facade 가 공유하는 필드만 담은 dict. facade 고유 필드는 build() 가 더한다."""
        return {name: getattr(self, name) for name in CORE_FIELDS}
