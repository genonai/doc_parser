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
from typing import Any, Callable, Optional

from docling_core.types import DoclingDocument
from docling_core.types.doc import PictureItem, TableItem

# 본문과 통계·순번 필드. core 가 본문에서 계산하므로 on_chunk 의 info["fields"] 로 바꿀 수 없다.
STAT_FIELDS = (
    "text", "n_char", "n_word", "n_line",
    "i_page", "e_page", "i_chunk_on_page", "n_chunk_of_page",
    "i_chunk_on_doc", "n_chunk_of_doc", "n_page",
)

# core_payload() 가 내보내는 공통 필드. build() 는 여기에 facade 고유 필드를 더한다.
CORE_FIELDS = STAT_FIELDS + (
    "reg_date", "chunk_bboxes", "media_files", "guardrail_categories",
    "has_table", "table_refs", "table_split_index", "table_split_total",
)


def _split_text(text: str, separator: str = "\n") -> "tuple[str, str]":
    """텍스트를 가운데에 가장 가까운 separator 경계에서 두 조각으로 나눈다.

    경계를 찾지 못하면 글자 수 절반에서 자른다 — 그래야 구분자가 없는 원천도 나뉜다.
    """
    middle = len(text) // 2
    cut = text.rfind(separator, 0, middle)
    if cut <= 0:
        cut = text.find(separator, middle)
    if cut <= 0:
        cut = middle
    return text[:cut], text[cut:].lstrip(separator)


def split_chunk(vector_metas: list, when: Callable[[Any], bool], *,
                separator: str = "\n", max_pieces: int = 64) -> list:
    """when(vector_meta) 이 참인 청크를 여러 건으로 나눈다(post_chunk 용).

    조건이 거짓이 될 때까지 반으로 나눈다. 나뉜 조각은 원본 필드를 그대로 물려받는다.
    통계와 순번은 바뀌므로 호출부가 refresh_stats 를 부른다.
    """
    out: list = []
    for vector_meta in vector_metas:
        pending = [vector_meta]
        pieces = 0
        while pending:
            current = pending.pop(0)
            text = getattr(current, "text", "") or ""
            if not when(current) or len(text) < 2 or pieces >= max_pieces:
                out.append(current)
                continue
            head, tail = _split_text(text, separator)
            if not head or not tail:
                out.append(current)
                continue
            pieces += 1
            first = current.model_copy(deep=True)
            first.text = head
            second = current.model_copy(deep=True)
            second.text = tail
            pending[:0] = [first, second]
    return out


def merge_small_chunks(vector_metas: list, min_chars: int = 80, *,
                       separator: str = "\n") -> list:
    """min_chars 미만인 청크를 앞 청크에 이어 붙인다(post_chunk 용).

    앞 청크가 없으면 뒤 청크와 합친다. 메타데이터는 남는 쪽(앞 청크)의 것을 쓴다.
    통계와 순번은 바뀌므로 호출부가 refresh_stats 를 부른다.
    """
    out: list = []
    for vector_meta in vector_metas:
        text = getattr(vector_meta, "text", "") or ""
        if out and len(text) < min_chars:
            merged = out[-1]
            merged.text = f"{merged.text}{separator}{text}" if merged.text else text
            continue
        out.append(vector_meta.model_copy(deep=True))
    # 첫 청크가 짧고 뒤에 청크가 있으면 그 둘을 합친다.
    if len(out) > 1 and len((getattr(out[0], "text", "") or "")) < min_chars:
        head = out.pop(0)
        out[0].text = f"{head.text}{separator}{out[0].text}" if head.text else out[0].text
    return out


def drop_fields(vector_meta, *names: str):
    """청크에서 필드를 지운다. 선언된 필드는 None 으로, 추가 필드는 통째로 뺀다."""
    declared = getattr(type(vector_meta), "model_fields", {})
    extra = getattr(vector_meta, "__pydantic_extra__", None)
    for name in names:
        if name in declared:
            setattr(vector_meta, name, None)
        elif isinstance(extra, dict):
            extra.pop(name, None)
    return vector_meta


def chunk_fields(value) -> dict:
    """on_chunk 가 info["fields"] 에 남긴 청크별 값을 검사해 dict 로 돌려준다.

    본문은 반환값으로 바꾸고 통계와 순번은 core 가 계산한다. fields 로 덮게 두면 본문과
    통계가 어긋나므로 STAT_FIELDS 는 거부한다.
    """
    if not value:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f'on_chunk 의 info["fields"] 는 dict 여야 합니다: {type(value).__name__}')
    reserved = sorted(set(STAT_FIELDS) & set(value))
    if reserved:
        raise ValueError(
            f'on_chunk 의 info["fields"] 로 바꿀 수 없는 필드입니다: {reserved}. '
            "본문은 반환값으로 바꾸고, 통계와 순번은 core 가 계산합니다.")
    return dict(value)


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


def refresh_stats(vectors, reindex: bool = True):
    """청크 목록의 파생 필드를 본문 기준으로 다시 계산한다(제자리 수정, 같은 목록 반환).

    `post_chunk` 에서 본문을 고치거나 청크를 버리면 통계와 순번이 옛 값으로 남는다
    (실측: 마커만 지운 훅에서 11건 중 9건의 n_char 가 실제 길이와 달랐다). 파싱·청킹
    본체가 처음 계산할 때와 같은 식을 쓰므로 결과가 어긋나지 않는다.

      통계         n_char=len, n_word=split, n_line=splitlines
      순번         i_chunk_on_doc / n_chunk_of_doc / i_chunk_on_page / n_chunk_of_page / n_page

    reindex=False 면 통계만 고친다. 청크를 버리지 않고 본문만 손봤을 때 쓴다.

    순번은 0 부터 센다 — 청킹 본체의 세 경로가 모두 그렇게 쓴다. 단일 마커 청크 한 건만
    만드는 경로는 1 부터 넣으므로, 그 산출에 reindex 를 걸면 1 이 0 으로 바뀐다.
    """
    items = list(vectors)
    for item in items:
        text = getattr(item, "text", None) or ""
        item.n_char = len(text)
        item.n_word = len(text.split())
        item.n_line = len(text.splitlines())
    if not reindex:
        return vectors

    def page_of(item):
        return getattr(item, "i_page", None) or 1

    per_page: dict = {}
    for item in items:
        page = page_of(item)
        per_page[page] = per_page.get(page, 0) + 1
    n_page = max(per_page) if per_page else 1
    seen: dict = {}
    for index, item in enumerate(items):
        page = page_of(item)
        item.i_chunk_on_doc = index
        item.n_chunk_of_doc = len(items)
        item.i_chunk_on_page = seen.get(page, 0)
        item.n_chunk_of_page = per_page[page]
        item.n_page = n_page
        seen[page] = seen.get(page, 0) + 1
    return vectors
