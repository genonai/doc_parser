"""GenosSmartChunker 의 공용 본체.

intelligent / convert / chunking 세 facade 가 각자 복제해 두었던 청킹 로직의 단일
사본이다. 정본은 intelligent 사본이며, 세 사본의 실제 차이는 클래스 플래그
(PICTURE_ANNOTATION_TEXT / TABLE_DESCRIPTION_MODE / AUTO_TABLE_AS_CHUNK_FOR_SHEETS)
로만 남겼다. 파생 클래스가 값을 정하면 기존 동작이 그대로 재현된다.

플래그가 서로 다르다는 사실 자체가 이 파일에서 한눈에 보인다는 점이 통합의 핵심이다.
예전에는 chunking 사본이 표 설명 기능에서 뒤처져 있다는 것을 세 파일을 나란히 놓고
비교해야만 알 수 있었다.
"""

from __future__ import annotations

import bisect
import logging
import math
from pathlib import Path
from typing import Any, ClassVar, Iterator, Optional, Union

import semchunk
from pydantic import ConfigDict, model_validator
from transformers import AutoTokenizer, PreTrainedTokenizerBase
from typing_extensions import Self

from docling_core.transforms.chunker import BaseChunk, BaseChunker, DocChunk, DocMeta
from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams
from docling_core.types import DoclingDocument
from docling_core.types import DoclingDocument as DLDocument
from docling_core.types.doc import (
    DocItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
)
from docling_core.types.doc.document import CodeItem, ContentLayer, LevelNumber, ListItem
from docling_core.types.doc.labels import DocItemLabel

from genon.preprocessor.facade.chunking import header_path as hp
from genon.preprocessor.facade.chunking.table_splitter import (
    leading_header_row_count,
    split_entries_preserving_tables,
    split_table_rows,
)
from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.enrichment.table_description import (
    TableDescriptionExtractor,
    refined_html_to_format,
)

_log = logging.getLogger(__name__)


class SmartChunkerBase(BaseChunker):
    """토큰 제한을 고려하여 섹션별 청크를 분할하고 병합하는 청커 (v2).

    facade 3종이 각자 복제해 두었던 GenosSmartChunker 의 단일 사본이다. 본문은
    intelligent_processor 사본을 정본으로 삼았다. 세 사본의 실제 차이는 아래 세 개의
    클래스 플래그로만 남아 있으며, 파생 클래스가 값을 정해 기존 동작을 그대로 재현한다.
    구분자 상수도 facade 가 자기 모듈 상수를 그대로 실어 준다 — 청크 크기 산정과
    compose_vectors 의 실제 부착이 같은 문자열을 봐야 하기 때문이다.
    """

    # 그림(PictureItem) annotation 텍스트를 청크 본문에 싣는지.
    # False 면 이미지 자리에 빈 문자열이 들어간다(convert 의 기존 동작).
    PICTURE_ANNOTATION_TEXT: ClassVar[bool] = True
    # 표 description annotation 반영 범위.
    #   "full"        refine HTML 재구성 + 검색 설명 접두 + 이미지 요약 접미까지
    #   "prefix_only" 검색 설명 접두만 (chunking 의 기존 동작)
    TABLE_DESCRIPTION_MODE: ClassVar[str] = "full"
    # xlsx 유래 문서(그룹명 'sheet: X')를 table_as_chunk 로 자동 전환할지.
    # True 면 kwargs 없이도 표 단위 청크로 나눈다(chunking 의 기존 동작).
    AUTO_TABLE_AS_CHUNK_FOR_SHEETS: ClassVar[bool] = False

    # 헤더 경로 구분자. facade 모듈 상수를 파생 클래스가 실어 준다.
    CHUNK_HEADER_SEP: ClassVar[str] = " > "
    CHUNK_PATH_SEP: ClassVar[str] = " | "
    CHUNK_PATH_MAX_LEAVES: ClassVar[int] = 5

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tokenizer: Union[PreTrainedTokenizerBase, str, Path] = (
            Path(cp.DEFAULT_TOKENIZER_LOCAL_PATH)
            if Path(cp.DEFAULT_TOKENIZER_LOCAL_PATH).exists()
            else cp.DEFAULT_TOKENIZER_ID
        )
    max_tokens: int = 1024
    merge_peers: bool = True
    # 토큰 수 계산 방식. "char"(default)=문자 수 기준 | "huggingface"=HF 토크나이저 기준
    tokenizer_type: str = "char"
    # 청킹 모드. "split_only"(기본)=chunk_size 초과 청크만 분할(구조 보존) | "resize_all"=모든 청크를 chunk_size 에 맞게 병합/분할
    chunk_mode: str = "split_only"
    # 청크 텍스트 선두 "HEADER: <섹션 경로>" 라인 부착 여부(기본 on). compose_vectors 의 실제 부착 지점과
    # 이 청커의 크기 산정(_size)이 같은 값을 봐야 청크 경계가 산출 텍스트와 일치한다.
    include_chunk_header: bool = True

    # _inner_chunker: BaseChunker = None
    _tokenizer: PreTrainedTokenizerBase = None
    merge_list_items: bool = True

    @model_validator(mode="after")
    def _initialize_components(self) -> Self:
        # 토크나이저 초기화
        mode = (self.tokenizer_type or "char").strip().lower()
        if mode not in {"char", "huggingface"}:
            _log.warning(f"[GenosSmartChunker] Unknown tokenizer_type '{mode}', fallback to 'char'.")
            mode = "char"
        self.tokenizer_type = mode
        if mode == "char":
            # 문자 수 기반: HF 토크나이저 로드 불필요 (외부 모델 의존 제거)
            self._tokenizer = None
        else:
            self._tokenizer = (
                self.tokenizer
                if isinstance(self.tokenizer, PreTrainedTokenizerBase)
                else AutoTokenizer.from_pretrained(self.tokenizer)
            )
        return self

    def preprocess(self, dl_doc: DLDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        """문서의 모든 아이템을 헤더 정보와 함께 청크로 생성

        Args:
            dl_doc: 청킹할 문서

        Yields:
            문서의 모든 아이템을 포함하는 하나의 청크
        """
        # 모든 아이템과 헤더 정보 수집
        all_items = []
        all_header_info = []  # 각 아이템의 헤더 정보
        current_heading_by_level: dict[LevelNumber, str] = {}
        all_header_short_info = []  # 각 아이템의 짧은 헤더 정보
        current_heading_short_by_level: dict[LevelNumber, str] = {}
        list_items: list[TextItem] = []
        dropped_filename_titles: list[TextItem] = []  # 파일명 TITLE. 본문이 비면 되살린다

        # iterate_items()로 수집된 아이템들의 self_ref 추적
        processed_refs = set()
        filename_titles = hp.filename_title_candidates(dl_doc)

        # 모든 아이템 순회
        for item, level in dl_doc.iterate_items(included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}, traverse_pictures=True):
            if hasattr(item, 'self_ref'):
                processed_refs.add(item.self_ref)

            if not isinstance(item, DocItem):
                continue

            # 리스트 아이템 병합 처리
            if self.merge_list_items:
                if isinstance(item, ListItem) or (
                    isinstance(item, TextItem) and item.label == DocItemLabel.LIST_ITEM
                ):
                    list_items.append(item)
                    continue
                elif list_items:
                    # 누적된 리스트 아이템들을 추가
                    for list_item in list_items:
                        all_items.append(list_item)
                        # 리스트 아이템의 헤더 정보 저장
                        all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                        all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})
                    list_items = []

            # 일부 backend 는 파일명을 TITLE 아이템으로 만든다. 파일명은 문서 내용이 아니므로
            # 본문과 섹션 breadcrumb 양쪽에서 모두 제외한다. 실제 문서 TITLE 은 유지한다.
            # 제외한 결과 남는 아이템이 하나도 없으면 아래에서 되살린다.
            if (isinstance(item, TextItem) and item.label == DocItemLabel.TITLE and
                    any(hp.normalize_filename_title(value) in filename_titles
                        for value in (item.text, item.orig) if value)):
                dropped_filename_titles.append(item)
                continue

            # 섹션 헤더 처리. TITLE 은 제외한다 — 파일명·문서 타이틀이 `HEADER:` 라인에
            # 중복 노출되는 것을 막기 위함이며, TITLE 텍스트는 아래 본문 분기로 보존된다.
            if isinstance(item, SectionHeaderItem) or (
                isinstance(item, TextItem) and
                item.label in [DocItemLabel.SECTION_HEADER]
            ):
                # 새로운 헤더 레벨 설정
                header_level = (
                    item.level if isinstance(item, SectionHeaderItem) else 1
                )
                current_heading_by_level[header_level] = item.text
                current_heading_short_by_level[header_level] = item.orig  # 첫 단어로 짧은 헤더 정보 설정

                # 더 깊은 레벨의 헤더들 제거
                keys_to_del = [k for k in current_heading_by_level if k > header_level]
                for k in keys_to_del:
                    current_heading_by_level.pop(k, None)
                keys_to_del_short = [k for k in current_heading_short_by_level if k > header_level]
                for k in keys_to_del_short:
                    current_heading_short_by_level.pop(k, None)

                # 헤더 아이템도 추가 (헤더 자체도 아이템임)
                all_items.append(item)
                all_header_info.append(dict(current_heading_by_level))
                all_header_short_info.append(dict(current_heading_short_by_level))
                continue

            if (isinstance(item, TextItem) or
                isinstance(item, ListItem) or
                isinstance(item, CodeItem) or
                isinstance(item, TableItem) or
                isinstance(item, PictureItem)):
                # if item.label in [DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER]:
                #     item.text = ""
                all_items.append(item)
                # 현재 아이템의 헤더 정보 저장
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})

        # 마지막 리스트 아이템들 처리
        if list_items:
            for list_item in list_items:
                all_items.append(list_item)
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})

        # iterate_items()에서 누락된 테이블들을 별도로 추가
        missing_tables = []
        for table in dl_doc.tables:
            table_ref = getattr(table, 'self_ref', None)
            if table_ref not in processed_refs:
                missing_tables.append(table)

        # 누락된 테이블들을 문서 앞부분에 추가 (페이지 1의 테이블들일 가능성이 높음)
        if missing_tables:
            for missing_table in missing_tables:
                # 첫 번째 위치에 삽입 (헤더 테이블일 가능성이 높음)
                all_items.insert(0, missing_table)
                all_header_info.insert(0, {})  # 빈 헤더 정보
                all_header_short_info.insert(0, {})  # 빈 짧은 헤더 정보

        # 파일명 TITLE 만 있는 문서는 위에서 전부 걸러져 all_items 가 빈다. 그대로 두면
        # 청크가 0개가 되어 하위에서 "chunk length is 0" 으로 실패하므로 되살린다.
        if not all_items and dropped_filename_titles:
            _log.warning(
                "[GenosSmartChunker] 파일명 TITLE 외 본문 아이템이 없어 파일명 TITLE "
                f"{len(dropped_filename_titles)}개를 본문으로 되살립니다."
            )
            for title_item in dropped_filename_titles:
                all_items.append(title_item)
                all_header_info.append({})
                all_header_short_info.append({})

        # 아이템이 없으면 빈 문서
        if not all_items:
            return

        # 모든 아이템을 하나의 청크로 반환 (HybridChunker에서 분할)
        # headings는 None으로 설정하고, 헤더 정보는 별도로 관리
        chunk = DocChunk(
            text="",  # 텍스트는 HybridChunker에서 생성
            meta=DocMeta(
                doc_items=all_items,
                headings=None,  # DocMeta의 원래 형식 유지
                captions=None,
                origin=dl_doc.origin,
            ),
        )
        # 헤더 정보를 별도 속성으로 저장
        chunk._header_info_list = all_header_info
        chunk._header_short_info_list = all_header_short_info  # 짧은 헤더 정보도 저장
        yield chunk

    def _count_tokens(self, text: str) -> int:
        """텍스트의 토큰 수 계산 (안전한 분할 처리)"""
        if not text:
            return 0

        if self._tokenizer is None:   # 문자 수 기반
            return len(text)

        # 텍스트를 더 작은 단위로 분할하여 계산
        max_chunk_length = 300  # 더 안전한 길이로 설정
        total_tokens = 0

        # 텍스트를 줄 단위로 먼저 분할
        lines = text.split('\n')
        current_chunk = ""

        for line in lines:
            # 현재 청크에 줄을 추가했을 때 길이 확인
            temp_chunk = current_chunk + '\n' + line if current_chunk else line

            if len(temp_chunk) <= max_chunk_length:
                current_chunk = temp_chunk
            else:
                # 현재 청크가 있으면 토큰 계산
                if current_chunk:
                    try:
                        total_tokens += len(self._tokenizer.tokenize(current_chunk))
                    except Exception:
                        total_tokens += int(len(current_chunk.split()) * 1.3)  # 대략적인 계산

                # 새로운 청크 시작
                current_chunk = line

        # 마지막 청크 처리
        if current_chunk:
            try:
                total_tokens += len(self._tokenizer.tokenize(current_chunk))
            except Exception:
                total_tokens += int(len(current_chunk.split()) * 1.3)  # 대략적인 계산

        return total_tokens

    def _generate_text_from_items_with_headers(self, items: list[DocItem],
                                              header_info_list: list[dict],
                                              dl_doc: DoclingDocument,
                                              **kwargs) -> str:
        """DocItem 리스트로부터 본문 텍스트 생성.

        섹션 헤더 경로(breadcrumb)는 여기서 본문에 삽입하지 않는다. 청크 선두의
        `HEADER: <섹션 경로>` 라인(compose_vectors)이 유일한 부착 지점이며, 과거에는 이 루프와
        `_generate_section_text_with_heading` 이 같은 문자열을 추가로 두 번 더 넣어 청크 텍스트의
        30~56% 가 제목 반복이었다(임베딩 희석·BM25 term frequency 왜곡). `header_info_list` 는
        호출부 시그니처 호환을 위해 유지한다.
        """
        text_parts = []

        for item in items:
            # 아이템 텍스트 추가
            if isinstance(item, TableItem):
                table_text = self._extract_table_text(item, dl_doc, **kwargs)
                if table_text:
                    text_parts.append(table_text)
            elif hasattr(item, 'text') and item.text:
                # 타이틀과 섹션 헤더 처리 개선
                # is_section_header = (
                #     isinstance(item, SectionHeaderItem) or
                #     (isinstance(item, TextItem) and
                #      item.label in [DocItemLabel.SECTION_HEADER])  # TITLE은 제외
                # )

                # 중복 제거는 섹션헤더/타이틀에만 적용한다. 본문까지 막으면 같은 섹션 안에서
                # 정당하게 반복되는 문장(상품요약서의 동일 특이사항 문단, 반복 표 캡션, 반복
                # OCR 줄)이 통째로 사라진다 — 실측: Q/A 4회 반복 md 에서 아이템 65개 중 36개 소실.
                # 그룹 단위 판정이라 chunk_size 가 작아 섹션이 쪼개지면 증상이 사라져 발견이 늦었다
                # (실측 본문 라인: chunk_size 1024 → 64, 3000/10000 → 29).
                if self._is_section_header(item) and item.text in text_parts:
                    continue
                text_parts.append(item.text)
            elif isinstance(item, PictureItem):
                if not self.PICTURE_ANNOTATION_TEXT:
                    text_parts.append("")  # 이미지는 빈 텍스트
                    continue
                picture_text = self._extract_picture_annotation_text(item)
                if picture_text and picture_text not in text_parts:
                    text_parts.append(picture_text)

        result_text = self.delim.join(text_parts)
        return result_text

    @staticmethod
    def _extract_picture_annotation_text(item: PictureItem) -> str:
        """PictureItem annotation의 텍스트를 단일 문자열로 추출."""
        texts: list[str] = []
        for annotation in getattr(item, "annotations", []) or []:
            text = str(getattr(annotation, "text", "") or "").strip()
            if text:
                texts.append(text)
        if not texts:
            return ""
        # 동일 annotation 중복 주입 방지
        return "\n".join(dict.fromkeys(texts))

    @staticmethod
    def _resolve_table_format(kwargs: dict) -> str:
        """표 직렬화 형식 결정: table_format(html|markdown) 우선, 없으면 레거시 export_to_html(1/0)."""
        fmt = kwargs.get("table_format")
        if fmt is None:
            return "html" if kwargs.get("export_to_html", 1) == 1 else "markdown"
        fmt = str(fmt).strip().lower()
        return "markdown" if fmt == "markdown" else "html"

    @staticmethod
    def _resolve_compact_tables(kwargs: dict) -> bool:
        """markdown 표를 compact(컬럼 정렬 패딩 제거)로 낼지 결정. 기본 True."""
        return cp.resolve_compact_tables(kwargs)

    def _extract_table_text_full(self, table_item: TableItem, dl_doc: DoclingDocument, **kwargs) -> str:
        """테이블 청크 텍스트를 만든다.

        표 description annotation 이 있으면 반영한다:
        - refine ON: 재구성 HTML 로 표 본체 교체
        - 텍스트 표 RAG 설명 존재: '[표 검색 설명]' 블록을 선두에 병기
        - 이미지 요약 존재: '\\n---\\n[표 설명]\\n<요약>' 을 항상 병기
        annotation 이 없으면(기본) 기존 export 결과를 그대로 반환(회귀 없음).
        """
        refined_html = TableDescriptionExtractor.extract_refined_html(table_item)
        # 텍스트 표 RAG 설명이 있으면 접두로 싣고 이미지 요약 접미 경로는 쓰지 않는다.
        retrieval_prefix = TableDescriptionExtractor.retrieval_prefix(table_item)
        table_summary = "" if retrieval_prefix else TableDescriptionExtractor.extract_summary(table_item)

        # refine 은 항상 HTML 로 재구성 → output table_format 에 맞춰 변환(markdown 등).
        refined = refined_html_to_format(
            refined_html, self._resolve_table_format(kwargs), self._resolve_compact_tables(kwargs))
        source = TableDescriptionExtractor.clean_copy(table_item) if retrieval_prefix else table_item
        base_text = refined or self._compute_table_base_text(source, dl_doc, **kwargs)
        if retrieval_prefix:
            return retrieval_prefix + base_text if base_text else retrieval_prefix.rstrip("\n")
        if table_summary:
            if base_text:
                return base_text + "\n---\n[표 설명]\n" + table_summary
            return "[표 설명]\n" + table_summary
        return base_text

    def _compute_table_base_text(self, table_item: TableItem, dl_doc: DoclingDocument, **kwargs) -> str:
        """테이블에서 텍스트를 추출하는 일반화된 메서드"""
        try:
            if self._resolve_table_format(kwargs) == "markdown":
                if self._resolve_compact_tables(kwargs):
                    # TableItem.export_to_markdown() 은 compact 옵션이 없어 직접 serializer 구성
                    # (컬럼 정렬 패딩 제거 → 대형 표 markdown 크기 대폭 축소)
                    table_text = MarkdownDocSerializer(
                        doc=dl_doc,
                        params=MarkdownParams(compact_tables=True),
                    ).serialize(item=table_item).text
                else:
                    table_text = table_item.export_to_markdown(dl_doc)
            else:
                table_text = table_item.export_to_html(dl_doc)
            if table_text and table_text.strip():
                return table_text
        except Exception:
            pass

        # export_to_markdown 실패 시 테이블 셀 데이터에서 직접 텍스트 추출
        try:
            if hasattr(table_item, 'data') and table_item.data:
                cell_texts = []

                # table_cells에서 텍스트 추출
                if hasattr(table_item.data, 'table_cells'):
                    for cell in table_item.data.table_cells:
                        if hasattr(cell, 'text') and cell.text and cell.text.strip():
                            cell_texts.append(cell.text.strip())

                # grid에서 텍스트 추출 (table_cells가 없는 경우)
                elif hasattr(table_item.data, 'grid') and table_item.data.grid:
                    for row in table_item.data.grid:
                        if isinstance(row, list):
                            for cell in row:
                                if hasattr(cell, 'text') and cell.text and cell.text.strip():
                                    cell_texts.append(cell.text.strip())

                # 추출된 셀 텍스트들을 결합
                if cell_texts:
                    return ' '.join(cell_texts)
        except Exception:
            pass

        # 모든 방법 실패 시 item.text 사용 (있는 경우)
        if hasattr(table_item, 'text') and table_item.text:
            return table_item.text

        return ""

    @staticmethod
    def _sheet_prefix(table_item: TableItem, dl_doc: DoclingDocument) -> str:
        """xlsx docling 표의 부모 그룹(name='sheet: X')에서 시트명을 뽑아 '시트명: X\\n' 접두 생성.
        시트 그룹이 없으면 '' 반환(PDF 등 비-xlsx 문서엔 실질 미적용)."""
        try:
            parent = table_item.parent.resolve(dl_doc) if getattr(table_item, "parent", None) else None
            name = getattr(parent, "name", None)
        except Exception:
            name = None
        if not name:
            return ""
        if not name.startswith("sheet: "):
            return ""
        name = name[len("sheet: "):]
        name = name.strip()
        return f"시트명: {name}\n" if name else ""

    def _table_item_to_texts_full(self, table_item: TableItem, dl_doc: DoclingDocument,
                             h_short: dict, *, max_tokens: Optional[int] = None,
                             **kwargs) -> list[str]:
        """표를 청크 텍스트 목록으로 변환. chunk_size(max_tokens) 초과 시 row 단위로 분할하고
        각 분할 청크에 헤더 행(선두 column_header 행 + 다음 컬럼명 행)을 반복 포함한다.

        미초과(또는 max_tokens<=0)면 현행과 동일하게 단일 청크(docling export_to_html) 1개를 반환.
        모든 청크(단일/분할)에 시트명 접두(`시트명: X\\n`)를 붙인다.
        """
        sheet_prefix = self._sheet_prefix(table_item, dl_doc)
        single = sheet_prefix + self._generate_section_text_with_heading([table_item], [h_short], dl_doc, **kwargs)

        # 재구성 HTML(refine)이 있으면 grid/구조가 달라 row 분할이 무의미 → 단일 청크로 둔다.
        if TableDescriptionExtractor.extract_refined_html(table_item):
            return [single]
        # 분할 조각에는 짧은 retrieval_context 만 반복한다(key_facts 는 행 매핑이 불가능).
        description_prefix = TableDescriptionExtractor.retrieval_prefix(table_item, split_piece=True)
        # 요약(summary)만 있는 경우: chunk_size 초과 표는 정상적으로 row 분할하고,
        # 요약은 마지막 분할 청크에만 1회 덧붙인다(중복 방지). single 경로는 이미 요약 포함.
        table_summary = (
            "" if TableDescriptionExtractor.retrieval_text(table_item)
            else TableDescriptionExtractor.extract_summary(table_item)
        )

        limit = self.max_tokens if max_tokens is None else max_tokens
        if limit is None or limit <= 0:
            return [single]
        if self._count_tokens(single) <= limit:
            return [single]

        try:
            grid = table_item.data.grid
            num_cols = table_item.data.num_cols
        except Exception:
            return [single]
        if not grid or not num_cols:
            return [single]

        flag_n = leading_header_row_count(grid)
        origin = getattr(dl_doc, "origin", None)
        mimetype = str(getattr(origin, "mimetype", "") or "").lower()
        filename = str(getattr(origin, "filename", "") or "").lower()
        is_html = mimetype in {"text/html", "application/xhtml+xml"} or filename.endswith(
            (".html", ".htm", ".xhtml")
        )
        header_n = max(flag_n, 1) if is_html else flag_n + 1
        suffix = "\n---\n[표 설명]\n" + table_summary if table_summary else ""
        result = split_table_rows(
            grid=grid,
            num_cols=num_cols,
            single_text=single,
            limit=limit,
            count_text=self._count_tokens,
            table_format=self._resolve_table_format(kwargs),
            header_row_count=header_n,
            prefix=sheet_prefix + description_prefix,
            suffix=suffix,
        )
        for index in result.oversized_piece_indexes:
            _log.warning(
                "[GenosSmartChunker] 표의 단일 행 또는 설명이 분할 예산(%d)을 초과해 "
                "행 구조를 보존한 채 유지합니다: table=%s size=%d",
                limit, getattr(table_item, "self_ref", ""),
                self._count_tokens(result.pieces[index]),
            )
        return result.pieces

    def _extract_table_text_prefix_only(self, table_item: TableItem, dl_doc: DoclingDocument, **kwargs) -> str:
        """테이블에서 텍스트를 추출하는 일반화된 메서드.

        텍스트 표 RAG 설명이 있으면 청크 선두에 붙인다. 설명은 표 본체와 함께 검색되어야
        의미가 있으므로 접두 위치가 고정이다.
        """
        prefix = TableDescriptionExtractor.retrieval_prefix(table_item)
        # docling serializer 는 표 annotation 을 본문에 함께 싣는다. 접두로 직접 붙이는 만큼
        # 직렬화 대상에서는 설명을 떼어 같은 문장이 두 번 들어가지 않게 한다.
        source = TableDescriptionExtractor.clean_copy(table_item) if prefix else table_item
        try:
            if self._resolve_table_format(kwargs) == "markdown":
                if self._resolve_compact_tables(kwargs):
                    # TableItem.export_to_markdown() 은 compact 옵션이 없어 직접 serializer 구성
                    # (컬럼 정렬 패딩 제거 → 대형 표 markdown 크기 대폭 축소)
                    table_text = MarkdownDocSerializer(
                        doc=dl_doc,
                        params=MarkdownParams(compact_tables=True),
                    ).serialize(item=source).text
                else:
                    table_text = source.export_to_markdown(dl_doc)
            else:
                table_text = source.export_to_html(dl_doc)
            if table_text and table_text.strip():
                return prefix + table_text
        except Exception:
            pass

        # export 실패 시 테이블 셀 데이터에서 직접 텍스트 추출
        try:
            if hasattr(table_item, 'data') and table_item.data:
                cell_texts = []

                # table_cells에서 텍스트 추출
                if hasattr(table_item.data, 'table_cells'):
                    for cell in table_item.data.table_cells:
                        if hasattr(cell, 'text') and cell.text and cell.text.strip():
                            cell_texts.append(cell.text.strip())

                # grid에서 텍스트 추출 (table_cells가 없는 경우)
                elif hasattr(table_item.data, 'grid') and table_item.data.grid:
                    for row in table_item.data.grid:
                        if isinstance(row, list):
                            for cell in row:
                                if hasattr(cell, 'text') and cell.text and cell.text.strip():
                                    cell_texts.append(cell.text.strip())

                # 추출된 셀 텍스트들을 결합
                if cell_texts:
                    return prefix + ' '.join(cell_texts)
        except Exception:
            pass

        # 모든 방법 실패 시 item.text 사용 (있는 경우)
        if hasattr(table_item, 'text') and table_item.text:
            return prefix + table_item.text

        return ""

    def _table_item_to_texts_prefix_only(self, table_item: TableItem, dl_doc: DoclingDocument,
                             h_short: dict, *, max_tokens: Optional[int] = None,
                             **kwargs) -> list[str]:
        """표를 청크 텍스트 목록으로 변환. chunk_size(max_tokens) 초과 시 row 단위로 분할하고
        각 분할 청크에 헤더 행(HTML은 선두 헤더, 그 외는 기존 제목+컬럼명 규칙)을 반복 포함한다.

        미초과(또는 max_tokens<=0)면 현행과 동일하게 단일 청크(docling export_to_html) 1개를 반환.
        모든 청크(단일/분할)에 시트명 접두(`시트명: X\\n`)를 붙인다.
        """
        sheet_prefix = self._sheet_prefix(table_item, dl_doc)
        single = sheet_prefix + self._generate_section_text_with_heading([table_item], [h_short], dl_doc, **kwargs)

        limit = self.max_tokens if max_tokens is None else max_tokens
        if limit is None or limit <= 0:
            return [single]
        if self._count_tokens(single) <= limit:
            return [single]

        try:
            grid = table_item.data.grid
            num_cols = table_item.data.num_cols
        except Exception:
            return [single]
        if not grid or not num_cols:
            return [single]

        # HTML은 선두 헤더 플래그 행(없으면 첫 행), 그 외 문서는 기존 규칙대로
        # 플래그 행 다음의 컬럼명 추정 행까지 반복한다.
        flag_n = leading_header_row_count(grid)
        origin = getattr(dl_doc, "origin", None)
        origin_mimetype = str(getattr(origin, "mimetype", "") or "").lower()
        origin_filename = str(getattr(origin, "filename", "") or "").lower()
        is_html = origin_mimetype in {"text/html", "application/xhtml+xml"} or origin_filename.endswith(
            (".html", ".htm", ".xhtml")
        )
        header_n = max(flag_n, 1) if is_html else flag_n + 1
        # 분할 조각에는 짧은 retrieval_context 만 반복한다(key_facts 는 행 매핑이 불가능).
        description_prefix = TableDescriptionExtractor.retrieval_prefix(
            table_item, split_piece=True
        )
        result = split_table_rows(
            grid=grid,
            num_cols=num_cols,
            single_text=single,
            limit=limit,
            count_text=self._count_tokens,
            table_format=self._resolve_table_format(kwargs),
            header_row_count=header_n,
            prefix=sheet_prefix + description_prefix,
        )
        for index in result.oversized_piece_indexes:
            if index < len(result.pieces):
                _log.warning(
                    "[GenosSmartChunker] 표의 단일 행이 분할 예산(%d)을 초과해 HTML 행 구조를 "
                    "보존한 채 초과 청크로 유지합니다: table=%s size=%d",
                    limit, getattr(table_item, "self_ref", ""),
                    self._count_tokens(result.pieces[index]),
                )
        return result.pieces

    @staticmethod
    def _doc_has_sheet_groups(dl_doc) -> bool:
        """DoclingDocument 가 xlsx 유래인지(그룹명 'sheet: X' 존재) 자동 감지."""
        try:
            for g in getattr(dl_doc, "groups", None) or []:
                name = getattr(g, "name", None)
                if isinstance(name, str) and name.startswith("sheet: "):
                    return True
        except Exception:
            pass
        return False

    def _extract_table_text(self, table_item: TableItem, dl_doc: DoclingDocument, **kwargs) -> str:
        """표 청크 텍스트. 설명 annotation 반영 범위는 TABLE_DESCRIPTION_MODE 가 정한다."""
        if self.TABLE_DESCRIPTION_MODE == "full":
            return self._extract_table_text_full(table_item, dl_doc, **kwargs)
        return self._extract_table_text_prefix_only(table_item, dl_doc, **kwargs)

    def _table_item_to_texts(self, table_item: TableItem, dl_doc: DoclingDocument,
                             h_short: dict, *, max_tokens=None, **kwargs) -> list[str]:
        """표를 청크 텍스트 목록으로 변환. 분할 조각의 설명 처리는 모드에 따라 다르다."""
        if self.TABLE_DESCRIPTION_MODE == "full":
            return self._table_item_to_texts_full(
                table_item, dl_doc, h_short, max_tokens=max_tokens, **kwargs)
        return self._table_item_to_texts_prefix_only(
            table_item, dl_doc, h_short, max_tokens=max_tokens, **kwargs)

    def _header_line(self, headings, include_header: bool) -> str:
        """청크 선두 `HEADER:` 라인. 구분자·리프 상한은 facade 가 정한 클래스 속성을 쓴다."""
        return hp.build_header_line(
            headings, include_header,
            self.CHUNK_HEADER_SEP, self.CHUNK_PATH_SEP, self.CHUNK_PATH_MAX_LEAVES)

    def _header_line_for(self, h_short: list[dict]) -> str:
        """그룹의 header_short 정보로 청크 선두 헤더 라인을 만든다(크기 산정용).

        compose_vectors 의 실제 부착과 같은 조립 함수를 쓰므로 둘이 어긋날 수 없다.
        """
        return self._header_line(self._extract_header_paths(h_short), self.include_chunk_header)

    def _extract_header_paths(self, header_info_list: list[dict]) -> Optional[list[str]]:
        """아이템별 헤더 스택(h_short)에서 실제 부모→자식 경로들을 뽑는다.

        반환 원소 하나가 하나의 완전한 경로(`부모 > 자식`)이며, 한 청크가 여러 섹션에
        걸치면 경로가 여러 개 나온다. 호출부(_header_line)가 CHUNK_PATH_SEP 으로 잇는다.

        예전에는 모든 헤더를 평탄하게 dedup 한 목록만 만들어, 형제 섹션이 부모-자식처럼
        표기됐다(실측: `상품 안내 > 우대금리 조건 > 가입 제한` — 뒤 둘은 형제 관계).
        h_short 는 아이템마다 {level: text} 맵을 갖고 있어 진짜 경로를 복원할 수 있다.
        """
        if not header_info_list:
            return None

        paths: list[tuple] = []
        for header_info in header_info_list:
            if not header_info:
                continue
            path = tuple(header_info[lvl] for lvl in sorted(header_info) if header_info[lvl])
            if path and path not in paths:
                paths.append(path)

        # 다른 경로의 진부분 접두인 경로는 버린다: ('A',) + ('A','B') → ('A','B') 하나.
        # (실측 card01 split_only 94건이 이 형태 — 접으면 표기가 짧아지고 의미는 같다)
        return hp.collapse_paths(
            [self.CHUNK_HEADER_SEP.join(p) for p in paths], self.CHUNK_HEADER_SEP) or None

    def _split_table_text(self, table_text: str, max_tokens: int) -> list[str]:
        """테이블 텍스트를 토큰 제한에 맞게 분할 (단순 토큰 수 기준)"""
        if not table_text:
            return [table_text]

        # 전체 테이블이 토큰 제한 내인지 확인
        if self._count_tokens(table_text) <= max_tokens:
            return [table_text]

        # 단순히 토큰 수 기준으로 텍스트 분할
        # semchunk 사용하여 토큰 제한에 맞게 분할 (char 모드는 문자 수 카운터 len 사용)
        counter = len if self._tokenizer is None else self._tokenizer
        chunker = semchunk.chunkerify(counter, chunk_size=max_tokens)
        chunks = chunker(table_text)
        return chunks if chunks else [table_text]

    def _split_text_to_budget(self, text: str, budget: int) -> list[str]:
        """텍스트를 budget 이하 조각들로 내부 분할한다.

        split_items_evenly_by_tokens 는 아이템 경계에서만 자르므로, 아이템 하나가 예산보다
        크면 그대로 통과해 청크가 chunk_size 를 넘었다(실측: 1500자 아이템 → 1515자 청크).
        표 분할(_split_table_text)과 같은 semchunk 방식으로 아이템 내부를 자른다.

        조각들은 원본 아이템을 공유하므로 chunk_bboxes 가 아이템 전체를 가리킨다
        (표 분할과 동일한 기존 트레이드오프).
        """
        if not text or budget <= 0:
            return [text]
        if self._count_tokens(text) <= budget:
            return [text]
        # char 모드는 문자 수 카운터(len), huggingface 모드는 토크나이저를 카운터로 쓴다.
        counter = len if self._tokenizer is None else self._tokenizer
        pieces = semchunk.chunkerify(counter, chunk_size=budget)(text)
        return [p for p in pieces if p] or [text]

    def _is_section_header(self, item: DocItem) -> bool:
        """아이템이 section header인지 확인"""
        return (isinstance(item, SectionHeaderItem) or
                (isinstance(item, TextItem) and
                 item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]))

    def _get_section_header_level(self, item: DocItem) -> Optional[int]:
        """Section header의 level을 반환"""
        if isinstance(item, SectionHeaderItem):
            return item.level
        elif isinstance(item, TextItem):
            if item.label == DocItemLabel.TITLE:
                return 0
            elif item.label == DocItemLabel.SECTION_HEADER:
                return 1
        return None

    def _generate_section_text_with_heading(self, section_items: list[DocItem],
                                            section_header_infos: list[dict],
                                            dl_doc: DoclingDocument,
                                            **kwargs) -> str:
        """섹션의 본문 텍스트를 생성한다.

        과거에는 이름 그대로 섹션 heading 을 본문 앞에 접두로 붙였으나, 청크 선두의
        `HEADER: <섹션 경로>` 라인(compose_vectors)과 정보가 완전히 동일한 중복이라 제거했다.
        섹션 경로 부착 지점은 compose_vectors 한 곳뿐이며, include_chunk_header 로 on/off 한다.
        (호출부가 5곳이라 함수명은 유지한다.)
        """
        return self._generate_text_from_items_with_headers(
            section_items, section_header_infos, dl_doc, **kwargs
        )

    def _split_document_by_tokens(self, doc_chunk: DocChunk, dl_doc: DoclingDocument, **kwargs) -> list[DocChunk]:
        """문서를 토큰 제한에 맞게 분할 (v2: 섹션 헤더 기준으로 분할 후 max_tokens로 병합)"""
        items = doc_chunk.meta.doc_items
        header_info_list = getattr(doc_chunk, '_header_info_list', [])
        header_short_info_list = getattr(doc_chunk, '_header_short_info_list', [])

        if not items:
            return []

        # ================================================================
        # 헬퍼 함수들
        # ================================================================

        def get_header_level(header_infos, *, first=False, default=-1):
            """header_infos에서 최종 레벨 계산"""
            if not header_infos:
                return default
            info = header_infos[0] if first else header_infos[-1]
            return max(info.keys(), default=default)

        def get_current_chunk(doc_chunk: DocChunk, merged_texts: list[str], merged_header_short_infos: list[dict], merged_items: list[DocItem]):
            """현재까지 병합된 내용으로 DocChunk 생성"""
            # doc_items 가 비면 DocMeta(min_length=1) 검증에서 크래시하므로 스킵한다.
            # (chunk_size 분할 시 헤더만 남고 items 가 빈 무의미 그룹이 생길 수 있음)
            if not merged_texts or not merged_items:
                return None
            chunk_text = "\n".join(merged_texts)
            used_headers = self._extract_header_paths(merged_header_short_infos)

            return DocChunk(
                    text=chunk_text,
                    meta=DocMeta(
                        doc_items=merged_items,
                        headings=used_headers,
                        captions=None,
                        origin=doc_chunk.meta.origin,
                    )
                )

        def get_text_from_item(item: DocItem) -> str:
            """DocItem에서 텍스트 추출"""
            if isinstance(item, TableItem):
                return self._extract_table_text(item, dl_doc, **kwargs)
            elif hasattr(item, 'text') and item.text:
                return item.text
            elif isinstance(item, PictureItem):
                text = ""
                for annotation in item.annotations:
                    if hasattr(annotation, 'text'):
                        text += annotation.text
                return text
            return ""

        def split_items_evenly_by_tokens(item_token_counts, max_tokens):
            n = len(item_token_counts)
            total = sum(item_token_counts)
            if n == 0:
                return []
            if total <= max_tokens:
                return [(0, n)]   # 항상 (a,b)

            k = math.ceil(total / max_tokens)
            target = total / k

            P = [0]
            for c in item_token_counts:
                P.append(P[-1] + c)

            cuts = [0]
            used = {0}
            for t in range(1, k):
                goal = t * target
                j = bisect.bisect_left(P, goal)

                cand = []
                if 0 < j < len(P): cand.append(j)
                if 0 <= j-1 < len(P): cand.append(j-1)

                best = None
                best_dist = float("inf")
                for x in cand:
                    if x in used:
                        continue
                    if x <= cuts[-1]:
                        continue
                    if x >= len(P)-1:  # n
                        continue
                    dist = abs(P[x] - goal)
                    if dist < best_dist:
                        best_dist = dist
                        best = x

                if best is None:
                    best = min(max(cuts[-1] + 1, 1), len(P)-2)

                cuts.append(best)
                used.add(best)

            cuts.append(n)

            # 폭 0 범위(a==b)는 빈 items 그룹을 만들어 하위에서 무의미 청크가 되므로 제외.
            return [(a, b) for a, b in zip(cuts[:-1], cuts[1:]) if a < b]

        def adjust_captions(items_group):

            b_modified = False
            for idx, group in enumerate(items_group):
                if group is None:
                    continue
                item = group[0][0]
                ref_idx_list = []
                if hasattr(item, 'captions') and item.captions:
                    for cap in item.captions:
                        cap_ref = cap.cref
                        cap_idx = -1
                        for j, it in enumerate(items_group):
                            if it is None:
                                continue
                            if getattr(it[0][0], 'self_ref', None) == cap_ref:
                                cap_idx = j
                                break
                        if cap_idx != -1:
                            ref_idx_list.append(cap_idx)
                if ref_idx_list:
                    ref_idx_list = sorted(ref_idx_list)

                if not ref_idx_list:
                    continue

                # caption 아이템들을 부모 아이템 바로 뒤로 이동
                for cap_idx in ref_idx_list:
                    for g in items_group[cap_idx]:
                        items_group[idx].append(g)
                    items_group[cap_idx] = None  # 나중에 None 제거
                    b_modified = True

            if b_modified:
                items_group = [it for it in items_group if it is not None]

            return items_group

        def adjust_pictures_in_tables(items_group):
            # picture in table 처리

            b_modified = False
            for idx, group in enumerate(items_group):
                if group is None:
                    continue
                item = group[0][0]
                pic_idx_list = []
                if isinstance(item, TableItem):
                    if not item.prov:  # HTML 등 좌표(prov) 없는 표는 bbox 매칭 불가 → 건너뜀
                        continue
                    table_bbox = item.prov[0].bbox
                    table_page_no = item.prov[0].page_no

                    for j in range(len(items_group)):
                        if items_group[j] is None:
                            continue
                        pic_item = items_group[j][0][0]
                        if isinstance(pic_item, PictureItem):
                            # table 안의 picture인지 확인. iou 사용
                            if not pic_item.prov:  # 좌표 없는 그림은 매칭 불가 → 건너뜀
                                continue
                            pic_bbox = pic_item.prov[0].bbox
                            pic_page_no = pic_item.prov[0].page_no
                            if pic_page_no != table_page_no:
                                continue
                            ios = pic_bbox.intersection_over_self(table_bbox)
                            if ios > 0.5:  # picture가 50% 이상 table 안에 포함되면 table 안의 picture로 간주
                                pic_idx_list.append(j)
                    if pic_idx_list:
                        pic_idx_list = sorted(pic_idx_list)

                if not pic_idx_list:
                    continue

                for pic_idx in pic_idx_list:
                    for g in items_group[pic_idx]:
                        items_group[idx].append(g)
                    items_group[pic_idx] = None  # 나중에 None 제거
                    b_modified = True

            if b_modified:
                items_group = [it for it in items_group if it is not None]

            return items_group

        def split_item_groups_preserving_tables(items_group, budget):
            """공통 분할기를 사용해 HTML/Markdown TableItem의 행 경계를 보존한다."""

            def unpack(entries):
                return (
                    [entry[0] for entry in entries],
                    [entry[1] for entry in entries],
                    [entry[2] for entry in entries],
                )

            def render(entries):
                entry_items, _, entry_short = unpack(entries)
                return self._generate_section_text_with_heading(
                    entry_items, entry_short, dl_doc, **kwargs
                )

            parts = split_entries_preserving_tables(
                item_groups=items_group,
                budget=budget,
                is_table_entry=lambda entry: isinstance(entry[0], TableItem),
                render_entries=render,
                count_text=self._count_tokens,
                split_plain_text=self._split_text_to_budget,
                split_table_entry=lambda entry, part_budget: self._table_item_to_texts(
                    entry[0], dl_doc, entry[2], max_tokens=part_budget, **kwargs
                ),
            )
            if parts is None:
                return None
            result = []
            for text, entries in parts:
                entry_items, entry_infos, entry_short = unpack(entries)
                result.append((text, entry_items, entry_infos, entry_short))
            return result

        # ================================================================
        # 표 단위 청크 분리 (xlsx docling 전용, kwargs: table_as_chunk)
        #   각 TableItem 을 독립 청크로, 사이의 연속 비표 아이템은 별도 청크로 묶는다.
        #   chunk_size(max_tokens) 와 무관하게 표가 병합되지 않도록 토큰 단계 이전에 확정 반환한다.
        # ================================================================
        if kwargs.get("table_as_chunk") or (
                self.AUTO_TABLE_AS_CHUNK_FOR_SHEETS and self._doc_has_sheet_groups(dl_doc)):
            table_chunks: list[DocChunk] = []
            buf_items: list[DocItem] = []
            buf_short: list[dict] = []

            def _flush_buf():
                if buf_items:
                    text = self._generate_section_text_with_heading(buf_items, buf_short, dl_doc, **kwargs)
                    # 빈 문서 방어용 "." placeholder 등 무의미한 텍스트 run 은 청크로 만들지 않는다.
                    if text and text.strip() and text.strip() != ".":
                        ch = get_current_chunk(doc_chunk, [text], list(buf_short), list(buf_items))
                        if ch:
                            table_chunks.append(ch)
                    buf_items.clear()
                    buf_short.clear()

            for i, item in enumerate(items):
                h_short = header_short_info_list[i] if i < len(header_short_info_list) else {}
                if isinstance(item, TableItem):
                    _flush_buf()
                    # 행이 많아 chunk_size 를 초과하는 표는 row 단위로 분할(각 청크에 헤더 반복 포함).
                    for text in self._table_item_to_texts(item, dl_doc, h_short, **kwargs):
                        ch = get_current_chunk(doc_chunk, [text], [h_short], [item])
                        if ch:
                            table_chunks.append(ch)
                else:
                    buf_items.append(item)
                    buf_short.append(h_short)
            _flush_buf()

            if table_chunks:
                return table_chunks

        # ================================================================
        # 1단계: 섹션 헤더 기준으로 분할
        # ================================================================

        sections = []  # [(items, header_infos, header_short_infos), ...]
        cur_items, cur_h_infos, cur_h_short = [], [], []

        for i, item in enumerate(items):
            h_info = header_info_list[i] if i < len(header_info_list) else {}
            h_short = header_short_info_list[i] if i < len(header_short_info_list) else {}

            # 섹션 헤더를 만나면
            if self._is_section_header(item):
                # 이전 섹션이 있으면 저장
                if cur_items:
                    sections.append((cur_items, cur_h_infos, cur_h_short))

                # 새로운 섹션 시작
                cur_items = [item]
                cur_h_infos = [h_info]
                cur_h_short = [h_short]
            else:
                # 섹션 헤더가 아니면 현재 섹션에 추가
                cur_items.append(item)
                cur_h_infos.append(h_info)
                cur_h_short.append(h_short)

        # 마지막 섹션 저장
        if cur_items:
            sections.append((cur_items, cur_h_infos, cur_h_short))

        # ================================================================
        # 2단계: 각 섹션의 텍스트에 heading 붙이기
        # ================================================================

        sections_with_text = []
        for items, header_infos, header_short_infos in sections:
            text = self._generate_section_text_with_heading(
                items, header_short_infos, dl_doc, **kwargs
            )
            sections_with_text.append((
                text,
                items,
                header_infos,
                header_short_infos
            ))

        # ================================================================
        # 2.5단계: 너무 긴 청크는 분할 (인덱스 꼬임 방지를 위해 새 리스트 사용)
        #   resize_all 전용. split_only 는 구조 그룹핑(4단계) 후 5.5단계에서 분할한다
        #   (여기서 분할하면 같은 섹션 조각들이 4단계에서 다시 병합되어 무의미).
        # ================================================================
        if self.max_tokens > 0 and self.chunk_mode == "resize_all":
            final_sections = []  # 결과를 담을 새 리스트
            for text, items, h_infos, h_short in sections_with_text:
                # 크기 판정·분할 예산 모두 헤더 라인 몫을 반영해야 최종 청크가 한도를 지킨다
                # (예전엔 본문만 세어 헤더가 그 위에 얹혀 초과했다).
                header_tokens = self._count_tokens(self._header_line_for(h_short))
                if self._count_tokens(text) + header_tokens <= self.max_tokens:
                    final_sections.append((text, items, h_infos, h_short))
                    continue
                budget = self.max_tokens - header_tokens
                if budget <= 0:
                    # 헤더 하나가 chunk_size 이상인 병리 케이스 — 본문 기준으로 폴백(경고).
                    _log.warning(
                        "[GenosSmartChunker] 헤더 라인(%d)이 chunk_size(%d) 이상 — 헤더 몫 예약 생략, "
                        "청크가 한도를 초과할 수 있음", header_tokens, self.max_tokens)
                    budget = self.max_tokens

                # caption 및 table 내 그림은 같은 섹션에 있도록 조정
                items_group=[[(item, info, short)] for item, info, short in zip(items, h_infos, h_short)]
                items_group = adjust_captions(items_group)
                items_group = adjust_pictures_in_tables(items_group)

                table_safe_parts = split_item_groups_preserving_tables(items_group, budget)
                if table_safe_parts is not None:
                    final_sections.extend(table_safe_parts)
                    continue

                # 너무 긴 섹션은 분할
                # 각 아이템 별 token 수 계산
                # 최종 텍스트는 delim.join(...) 이라 아이템 사이 구분자도 길이에 들어간다.
                delim_tokens = self._count_tokens(self.delim)
                item_token_counts = []
                for group in items_group:
                    cur_count = 0
                    for g in group:
                        cur_count += self._count_tokens(get_text_from_item(g[0])) + delim_tokens
                    item_token_counts.append(cur_count)

                # 아이템 그룹들을 토큰 기준으로 균등 분할 (헤더 몫을 뺀 예산 기준)
                split_info = split_items_evenly_by_tokens(item_token_counts, budget)

                # 분할된 결과들을 새 리스트에 추가
                for (a, b) in split_info:

                    # 각 그룹에서 items, h_infos, h_short로 분리
                    group_items = []
                    group_h_infos = []
                    group_h_short = []
                    for idx in range(a, b):
                        for g in items_group[idx]:
                            group_items.append(g[0])
                            group_h_infos.append(g[1])
                            group_h_short.append(g[2])

                    new_text = self._generate_section_text_with_heading(
                        group_items, group_h_short, dl_doc, **kwargs
                    )
                    # 아이템 경계로도 예산을 못 맞추면(단일 긴 아이템) 텍스트 내부를 자른다.
                    for piece in self._split_text_to_budget(new_text, budget):
                        final_sections.append((piece, group_items, group_h_infos, group_h_short))

            sections_with_text = final_sections  # 전체 리스트 교체

        # ================================================================
        # 3단계: 단독 타이틀(1줄만) → 다음 섹션으로 병합
        # ================================================================

        for i in range(len(sections_with_text) - 2, -1, -1):
            text, items, h_infos, h_short = sections_with_text[i]

            # 아이템이 하나인 섹션 헤더만 검사
            if len(items) != 1 or not self._is_section_header(items[0]):
                continue

            # 문단이 이미 구성된 것은 제외 (문자 수가 30자 이상이면 문단을 구성했다고 간주)
            item_text = "".join(getattr(it, "text", "") for it in items)
            if len(item_text) > 30:
                continue

            # 현재 섹션헤더 레벨이 다음 섹션헤더 레벨보다 더 높은 경우에만 병합 (높은 레벨이 더 작은 숫자)
            n_text, n_items, n_h_infos, n_h_short = sections_with_text[i + 1]
            current_level = get_header_level(h_infos, first=False)
            next_level = get_header_level(n_h_infos, first=True)
            if 0 <= next_level < current_level:
                continue

            # 병합 결과가 chunk_size 를 넘으면 합치지 않는다. 이 단계에는 크기 검사가 없어,
            # 5.5/2.5 단계가 예산에 맞춰 잘라놓은 조각을 여기서 다시 붙여 한도를 넘겼다
            # (실측: 6자 제목 + 1009자 조각 = 1016자 → 헤더 15자 포함 1031 > 1024).
            merged_text = text + '\n' + n_text
            if self.max_tokens > 0 and self._count_tokens(
                    self._header_line_for(h_short + n_h_short) + merged_text) > self.max_tokens:
                continue

            # 다음 섹션과 병합
            sections_with_text[i] = (merged_text, items + n_items, h_infos + n_h_infos, h_short + n_h_short)
            sections_with_text.pop(i + 1)

        # ================================================================
        # 4단계: 토큰 기준 병합 (1차 — 섹션 구조 경계 기준 그룹 생성)
        # ================================================================

        groups: list[dict] = []
        merged_texts, merged_items = [], []
        merged_header_infos, merged_header_short_infos = [], []

        def flush_group():
            if merged_texts:
                groups.append({
                    "texts": list(merged_texts),
                    "items": list(merged_items),
                    "h_infos": list(merged_header_infos),
                    "h_short": list(merged_header_short_infos),
                })

        for text, items, header_infos, header_short_infos in sections_with_text:

            b_new_chunk = False

            #----------------------------------
            # 병합 가능 여부 판단

            # 병합 가능 토큰 수 계산. 헤더 라인도 최종 청크 길이에 들어가므로 함께 센다
            # (경로가 합쳐지면 헤더가 길어져 병합 판정이 달라져야 한다).
            test_tokens = self._count_tokens(
                self._header_line_for(merged_header_short_infos + list(header_short_infos))
                + "\n".join(merged_texts + [text]))

            # 현재 섹션헤더 레벨과 병합된 섹션헤더 레벨
            section_level = get_header_level(header_infos, first=True)
            merged_level = get_header_level(merged_header_infos, first=False)

            # split_only: base 섹션 granularity 유지 — 구조 그룹핑 병합 없이 섹션마다 분리(장 단위 병합 방지).
            #   (1·3단계로 만든 섹션을 그대로 두고, 초과분만 5.5단계에서 분할)
            if self.chunk_mode == "split_only" and len(merged_texts) > 0:
                b_new_chunk = True
            # 토큰 수 초과 시 새로운 청크 생성 (resize_all 전용)
            elif self.chunk_mode == "resize_all" and test_tokens > self.max_tokens and len(merged_texts) > 0:
                b_new_chunk = True
            # 현재 섹션헤더 레벨이 더 높으면 새로운 청크 생성 (resize_all 구조 경계)
            elif 0 <= section_level < merged_level:
                b_new_chunk = True
            #----------------------------------

            # 새로운 청크 생성
            if b_new_chunk:
                flush_group()

                # 새로운 병합 시작
                merged_texts = [text]
                merged_items = list(items)
                merged_header_infos = list(header_infos)
                merged_header_short_infos = list(header_short_infos)
            else:
                # 현재 섹션 병합
                merged_texts.append(text)
                merged_items.extend(items)
                merged_header_infos.extend(header_infos)
                merged_header_short_infos.extend(header_short_infos)

        # 마지막 병합된 items 처리
        flush_group()

        # ================================================================
        # 5단계: chunk_size 한도 내 인접 그룹 greedy 병합
        #   1차 결과(구조 경계 기준 그룹)를 순서대로, 합산 크기가 chunk_size 이하인 동안
        #   인접 그룹끼리 결합한다. (크기는 HEADER 라인 포함 최종 텍스트 기준)
        # ================================================================
        def _size(g):
            text = "\n".join(g["texts"])
            # compose_vectors 가 실제로 붙이는 것과 같은 문자열이어야 경계 판정이 산출 텍스트와 일치한다.
            header_line = self._header_line_for(g["h_short"])
            # char 모드면 문자 수, huggingface 모드면 토큰 수로 산정 (max_tokens 단위와 일치)
            return self._count_tokens(header_line + text)

        if self.max_tokens > 0 and groups and self.chunk_mode == "resize_all":
            def _merge(a, b):
                return {
                    "texts": a["texts"] + b["texts"],
                    "items": a["items"] + b["items"],
                    "h_infos": a["h_infos"] + b["h_infos"],
                    "h_short": a["h_short"] + b["h_short"],
                }

            merged_groups = [groups[0]]
            for g in groups[1:]:
                cand = _merge(merged_groups[-1], g)
                if _size(cand) <= self.max_tokens:
                    merged_groups[-1] = cand
                else:
                    merged_groups.append(g)
            groups = merged_groups

        # ================================================================
        # 5.5단계: split_only 전용 — chunk_size 초과 그룹만 토큰 기준 균등 분할
        #   (구조 기반 그룹은 유지, 작은 그룹은 병합하지 않고 그대로 둔다)
        # ================================================================
        if self.max_tokens > 0 and groups and self.chunk_mode == "split_only":
            new_groups = []
            for g in groups:
                if _size(g) <= self.max_tokens:
                    new_groups.append(g)
                    continue

                # caption 및 table 내 그림은 같은 조각에 있도록 조정 (2.5단계와 동일 로직)
                items_group = [[(it, inf, sh)] for it, inf, sh in zip(g["items"], g["h_infos"], g["h_short"])]
                items_group = adjust_captions(items_group)
                items_group = adjust_pictures_in_tables(items_group)

                # 최종 텍스트는 delim.join(...) 이라 아이템 사이 구분자도 길이에 들어간다.
                # 아이템당 구분자 1개를 더해 예산을 보수적으로 잡는다(조각당 1개 과대 계상 → 안전).
                # 안 더하면 구분자 총량만큼 예산이 헐거워져 실제 산출이 chunk_size 를 넘는다
                # (실측: 18줄 청크에서 구분자 17자가 빠져 11자 초과).
                delim_tokens = self._count_tokens(self.delim)
                item_token_counts = []
                for grp in items_group:
                    item_token_counts.append(
                        sum(self._count_tokens(get_text_from_item(x[0])) + delim_tokens for x in grp))

                # item_token_counts 는 본문 토큰만 세므로, 청크 선두에 붙을 헤더 라인 몫을 예산에서
                # 빼야 한다. 안 빼면 각 조각이 본문만으로 max_tokens 를 채우고 헤더가 그 위에 얹혀
                # chunk_size 를 초과한다. 하위 그룹의 h_short 는 부모의 부분집합이라 부모 기준
                # 헤더 길이는 상한이며, 따라서 이 예약은 안전하다.
                header_tokens = self._count_tokens(self._header_line_for(g["h_short"]))
                budget = self.max_tokens - header_tokens
                if budget <= 0:
                    # 헤더 하나가 chunk_size 이상인 병리 케이스(조문 전체가 SECTION_HEADER 로 승격된 경우).
                    # 예약하면 예산이 0 이하가 되어 분할이 끝나지 않으므로 기존 동작(본문 기준)으로
                    # 폴백하고 경고만 남긴다 — 근본 원인은 heading 길이라 별도로 다룬다.
                    _log.warning(
                        "[GenosSmartChunker] 헤더 라인(%d)이 chunk_size(%d) 이상 — 헤더 몫 예약 생략, "
                        "청크가 한도를 초과할 수 있음", header_tokens, self.max_tokens)
                    budget = self.max_tokens

                table_safe_parts = split_item_groups_preserving_tables(items_group, budget)
                if table_safe_parts is not None:
                    for piece, piece_items, piece_infos, piece_short in table_safe_parts:
                        new_groups.append({
                            "texts": [piece],
                            "items": piece_items,
                            "h_infos": piece_infos,
                            "h_short": piece_short,
                        })
                    continue

                for (a, b) in split_items_evenly_by_tokens(item_token_counts, budget):
                    gi, gh, gs = [], [], []
                    for idx in range(a, b):
                        for x in items_group[idx]:
                            gi.append(x[0]); gh.append(x[1]); gs.append(x[2])
                    new_text = self._generate_section_text_with_heading(gi, gs, dl_doc, **kwargs)
                    # 아이템 경계 분할로도 예산을 못 맞추면(단일 긴 아이템) 텍스트 내부를 자른다.
                    for piece in self._split_text_to_budget(new_text, budget):
                        new_groups.append({"texts": [piece], "items": gi,
                                           "h_infos": gh, "h_short": gs})
            groups = new_groups

        # ================================================================
        # 6단계: 최종 DocChunk 생성
        # ================================================================
        result_chunks = []
        for g in groups:
            cur_chunk = get_current_chunk(doc_chunk, g["texts"], g["h_short"], g["items"])
            if cur_chunk:
                result_chunks.append(cur_chunk)

        return result_chunks

    def chunk(self, dl_doc: DoclingDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        """문서를 청킹하여 반환

        Args:
            dl_doc: 청킹할 문서

        Yields:
            토큰 제한에 맞게 분할된 청크들
        """
        doc_chunks = list(self.preprocess(dl_doc=dl_doc, **kwargs))

        if not doc_chunks:
            return iter([])

        doc_chunk = doc_chunks[0]  # preprocess는 하나의 청크만 반환

        final_chunks = self._split_document_by_tokens(doc_chunk, dl_doc, **kwargs)
        final_chunks = self._merge_heading_only_chunks(final_chunks)

        return iter(final_chunks)

    def _merge_heading_only_chunks(self, chunks: list[DocChunk]) -> list[DocChunk]:
        """본문이 없고 제목만 있는 청크를 인접 청크로 병합한다.

        docling 이 섹션 헤더를 독립 DocItem 으로 내보내면 그 헤더 하나만 담긴 청크가 생긴다
        (실측: 여비세칙 76청크 중 20개). 이런 청크는 제목 용어로 검색 상위를 차지한 뒤 답변 근거를
        0 개 제공하는 인덱스 오염이므로, doc_items(bbox·페이지 커버리지)와 headings 를 다음 청크로
        승계시키고 청크 자체는 없앤다. 다음 청크가 없으면 이전 청크로 후방 병합한다.

        DocMeta.doc_items 는 min_length=1 이라 doc_items 가 비지 않도록 원본을 그대로 옮긴다.
        """
        if len(chunks) < 2:
            return chunks

        def _is_heading_only(chunk: DocChunk) -> bool:
            """아이템이 전부 섹션 헤더/타이틀인 청크.

            문자열 기반(본문에서 heading 을 replace 해 남는지)으로 판정하면, 본문이 헤더
            문자열로만 구성된 정상 청크를 헤더-only 로 오판해 본문이 사라졌다
            (실측: 헤더 `가` + 본문 `가가가가가` → `가가가가가` 소실). 유형으로 판정한다.
            """
            return bool(chunk.meta.doc_items) and all(
                self._is_section_header(item) for item in chunk.meta.doc_items)

        def _text_covered(donor: DocChunk, headings) -> bool:
            """donor 텍스트의 모든 줄이 병합 후 헤더 경로에 남는지.

            _absorb 는 donor 본문을 버리고 headings 만 승계하므로, donor 텍스트가 경로에
            남지 않으면 그 내용은 산출물에서 사라진다. 유형 판정만으로는 부족하다 —
            _is_section_header 는 TITLE 도 포함하는데 문서 선두 TITLE 청크는 headings 가
            비어 있을 수 있다(실측: hwp chunk 0 에 HEADER 라인 없음). 여기서 최종 차단한다.
            """
            joined = self.CHUNK_PATH_SEP.join(headings or [])
            return all(line.strip() in joined
                       for line in (donor.text or "").splitlines() if line.strip())

        def _absorb(donor: DocChunk, target: DocChunk, *, donor_first: bool) -> DocChunk:
            items = ([*donor.meta.doc_items, *target.meta.doc_items] if donor_first
                     else [*target.meta.doc_items, *donor.meta.doc_items])
            headings = (hp.union_paths(donor.meta.headings, target.meta.headings) if donor_first
                        else hp.union_paths(target.meta.headings, donor.meta.headings))
            return DocChunk(
                text=target.text,
                meta=DocMeta(
                    doc_items=items,
                    headings=headings,
                    # 이 청커는 captions 를 채우지 않는다(get_current_chunk 도 None).
                    # target.meta.captions 를 읽으면 docling 의 deprecation 경고만 유발한다.
                    captions=None,
                    origin=target.meta.origin,
                ),
            )

        def _fits(chunk: DocChunk) -> bool:
            """병합 결과가 chunk_size 이내인지. 본문은 안 늘지만 headings 합집합으로 헤더 라인이
            길어지므로, _size 검증이 끝난 뒤인 여기서 다시 확인해야 한도를 넘기지 않는다."""
            if self.max_tokens <= 0:
                return True
            line = self._header_line(chunk.meta.headings, self.include_chunk_header)
            return self._count_tokens(line + (chunk.text or "")) <= self.max_tokens

        result: list[DocChunk] = []
        pending: list[DocChunk] = []  # 아직 흡수처를 못 찾은 제목-only 청크들
        for chunk in chunks:
            if _is_heading_only(chunk):
                pending.append(chunk)
                continue
            # 가까운 donor 부터 흡수해야 items·headings 가 문서 순서로 쌓인다.
            # 한도에 걸리면 그 지점에서 멈춘다 — 가까운 donor 를 못 넣은 채 먼 donor 를 건너뛰어
            # 붙이면 순서가 뒤집히기 때문이다. 못 넣은 것은 제목-only 청크로 그대로 남긴다.
            skipped: list[DocChunk] = []
            stopped = False
            for donor in reversed(pending):
                if stopped:
                    skipped.append(donor)
                    continue
                cand = _absorb(donor, chunk, donor_first=True)
                # 크기(_fits)와 무손실(_text_covered) 둘 다 만족할 때만 흡수한다.
                # 못 하면 제목-only 청크로 남긴다 — 인덱스 오염을 조금 남기는 편이 본문 유실보다 낫다.
                if _fits(cand) and _text_covered(donor, cand.meta.headings):
                    chunk = cand
                else:
                    stopped = True
                    skipped.append(donor)
            result.extend(reversed(skipped))  # 문서 순서 복원 후 target 앞에 배치
            result.append(chunk)
            pending.clear()

        # 문서 끝에 남은 제목-only 청크는 마지막 본문 청크로 후방 병합(한도를 넘으면 그대로 남긴다).
        while pending:
            donor = pending.pop(0)
            if result:
                cand = _absorb(donor, result[-1], donor_first=False)
                if _fits(cand) and _text_covered(donor, cand.meta.headings):
                    result[-1] = cand
                    continue
            result.append(donor)
        # 본문 청크가 하나도 없으면(제목뿐인 문서) 원본을 그대로 반환한다.
        return result if result else chunks