from __future__ import annotations

import json
import logging
import math, bisect
import yaml
from pathlib import Path

from collections import defaultdict
from datetime import datetime
from typing import Optional, Any, List

from fastapi import Request
from pydantic import BaseModel

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    PdfPipelineOptions,
    TableFormerMode,
)
from docling.document_converter import DocumentConverter, HTMLFormatOption
from docling.datamodel.document import ConversionResult
from docling_core.transforms.chunker import (
    BaseChunk,
    BaseChunker,
    DocChunk,
    DocMeta,
)
from docling_core.types import DoclingDocument

from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    DocItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
)
from docling_core.types.doc.document import (
    CodeItem,
    ContentLayer,
    LevelNumber,
    ListItem,
)

_log = logging.getLogger(__name__)

import re
import subprocess

from typing import Iterator, Union

from pydantic import ConfigDict, model_validator
from typing_extensions import Self

try:
    import semchunk
    from transformers import AutoTokenizer, PreTrainedTokenizerBase
except ImportError:
    raise RuntimeError("Module requires 'chunking' extra; to install, run: " "`pip install 'docling-core[chunking]'`")

####################################################
#################### 전처리 코드 #####################
####################################################

####################################################
#################### 전처리 코드 #####################
####################################################

#
#
# Copyright IBM Corp. 2024 - 2024
# SPDX-License-Identifier: MIT
#

"""Chunker implementation leveraging the document structure (GenosSmartChunker, ported
from facade/intelligent_processor.py) with added domain-specific heading promotion for
Korean legal/administrative-rule article numbering (chapter/section/article markers that
appear as plain text rather than a docling-detected SectionHeaderItem)."""

_DEFAULT_TOKENIZER_LOCAL_PATH = "/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2"
_DEFAULT_TOKENIZER_ID = "sentence-transformers/all-MiniLM-L6-v2"


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _parse_optional_int(value: Any, key: str = "") -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        if key:
            _log.warning(f"[DocumentProcessor] Invalid int value for '{key}': {value!r}. Fallback to default.")
        return None


def _load_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError(f"Invalid config format: expected mapping, got {type(cfg).__name__}")
    return cfg


def _resolve_tokenizer(chunking_cfg: dict):
    """Resolve the chunking tokenizer from config: prefer a local path if present,
    otherwise fall back to the HF tokenizer id (for network-restricted deployments)."""
    local = chunking_cfg.get("tokenizer_path") or _DEFAULT_TOKENIZER_LOCAL_PATH
    hf_id = chunking_cfg.get("tokenizer_id") or _DEFAULT_TOKENIZER_ID
    return Path(local) if Path(local).exists() else hf_id


def _resolve_default_config_path() -> str:
    base_dir = Path(__file__).resolve().parent
    local_config = (base_dir / "../resource_dev/administrative_rule_processor_config.yaml").resolve()
    default_config = (base_dir / "../resource/administrative_rule_processor_config.yaml").resolve()
    if local_config.exists():
        return str(local_config)
    return str(default_config)


class GenosSmartChunker(BaseChunker):
    """토큰 제한을 고려하여 섹션별 청크를 분할하고 병합하는 청커 (v2)"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tokenizer: Union[PreTrainedTokenizerBase, str, Path] = (
        Path(_DEFAULT_TOKENIZER_LOCAL_PATH) if Path(_DEFAULT_TOKENIZER_LOCAL_PATH).exists() else _DEFAULT_TOKENIZER_ID
    )
    max_tokens: int = 1024
    merge_peers: bool = True
    # 토큰 수 계산 방식. "char"(default)=문자 수 기준 | "huggingface"=HF 토크나이저 기준
    tokenizer_type: str = "char"

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

    def preprocess(self, dl_doc: DoclingDocument, **kwargs: Any) -> Iterator[BaseChunk]:
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

        # Domain-specific heading promotion state (Korean legal/administrative-rule
        # article numbering: chapter/section/article markers embedded as plain text).
        _domain_title_cnt = 1
        _domain_url_temp = ""
        _RE_JANG = "^제\\s*\\d{1,3}\\s*장.*"
        _RE_JEOL = "^제\\s*\\d{1,3}\\s*절.*"
        _RE_JO = "^제\\s*\\d{1,3}\\s*조.*"
        _RE_CLEAN = r"[\n\t]+"

        # iterate_items()로 수집된 아이템들의 self_ref 추적
        processed_refs = set()

        # 모든 아이템 순회
        for item, level in dl_doc.iterate_items(
            included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}, traverse_pictures=True
        ):
            if hasattr(item, "self_ref"):
                processed_refs.add(item.self_ref)

            if not isinstance(item, DocItem):
                continue

            # 리스트 아이템 병합 처리
            if self.merge_list_items:
                if isinstance(item, ListItem) or (isinstance(item, TextItem) and item.label == DocItemLabel.LIST_ITEM):
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

            # 섹션 헤더 처리
            if isinstance(item, SectionHeaderItem) or (
                isinstance(item, TextItem) and item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]
            ):
                # 새로운 헤더 레벨 설정
                header_level = (
                    item.level
                    if isinstance(item, SectionHeaderItem)
                    else (0 if item.label == DocItemLabel.TITLE else 1)
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
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})
                continue

            # Domain heading promotion (doc title / chapter(jang) / section(jeol) / article(jo) / addendum).
            if isinstance(item, TextItem):
                _domain_skip = False
                # UI chrome (nav/print/caption buttons, page header/footer) can sit between the
                # URL and the real title in the item stream; don't let it be mistaken for the
                # title just because it's "the next text item". Let it fall through as plain
                # content without advancing the title-hunting state.
                _is_chrome = item.label in (DocItemLabel.CAPTION, DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER)
                if _domain_title_cnt in (1, 2) and _is_chrome:
                    pass
                elif _domain_title_cnt == 1:
                    if item.text.startswith("http://"):
                        _domain_url_temp = item.text
                        _domain_title_cnt += 1
                    else:
                        current_heading_by_level[1] = item.text
                        current_heading_short_by_level[1] = item.text
                        _domain_title_cnt += 1
                        _domain_skip = True
                elif _domain_title_cnt == 2 and _domain_url_temp != "":
                    current_heading_by_level[1] = item.text
                    current_heading_short_by_level[1] = item.text
                    _domain_title_cnt += 1
                    _domain_skip = True
                else:
                    _domain_title_cnt += 1
                    if re.match(_RE_JANG, item.text):
                        current_heading_by_level[2] = item.text
                        current_heading_short_by_level[2] = item.text
                        current_heading_by_level[3] = ""
                        current_heading_short_by_level[3] = ""
                        current_heading_by_level[4] = ""
                        current_heading_short_by_level[4] = ""
                        _domain_skip = True
                    elif re.match(_RE_JEOL, item.text):
                        current_heading_by_level[3] = item.text
                        current_heading_short_by_level[3] = item.text
                        current_heading_by_level[4] = ""
                        current_heading_short_by_level[4] = ""
                        _domain_skip = True
                    elif re.match(_RE_JO, item.text):
                        _cleaned = re.sub(_RE_CLEAN, "", item.text)
                        _m = re.match("제.*?조\\s?\\([^()]*\\)", _cleaned)
                        if _m:
                            _heading = _m.group(0)
                        else:
                            _m2 = re.match("^제.*?조", _cleaned)
                            _heading = _m2.group(0) if _m2 else item.text
                        current_heading_by_level[4] = _heading
                        current_heading_short_by_level[4] = _heading
                    elif item.text[:2] == "부칙":
                        current_heading_by_level[2] = ""
                        current_heading_short_by_level[2] = ""
                        current_heading_by_level[3] = ""
                        current_heading_short_by_level[3] = ""
                        current_heading_by_level[4] = ""
                        current_heading_short_by_level[4] = ""
                    else:
                        current_heading_by_level[4] = ""
                        current_heading_short_by_level[4] = ""

                if _domain_skip:
                    all_items.append(item)
                    all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                    all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})
                    continue

            if (
                isinstance(item, TextItem)
                or isinstance(item, ListItem)
                or isinstance(item, CodeItem)
                or isinstance(item, TableItem)
                or isinstance(item, PictureItem)
            ):
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
            table_ref = getattr(table, "self_ref", None)
            if table_ref not in processed_refs:
                missing_tables.append(table)

        # 누락된 테이블들을 문서 앞부분에 추가 (페이지 1의 테이블들일 가능성이 높음)
        if missing_tables:
            for missing_table in missing_tables:
                # 첫 번째 위치에 삽입 (헤더 테이블일 가능성이 높음)
                all_items.insert(0, missing_table)
                all_header_info.insert(0, {})  # 빈 헤더 정보
                all_header_short_info.insert(0, {})  # 빈 짧은 헤더 정보

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

        if self._tokenizer is None:  # 문자 수 기반
            return len(text)

        # 텍스트를 더 작은 단위로 분할하여 계산
        max_chunk_length = 300  # 더 안전한 길이로 설정
        total_tokens = 0

        # 텍스트를 줄 단위로 먼저 분할
        lines = text.split("\n")
        current_chunk = ""

        for line in lines:
            # 현재 청크에 줄을 추가했을 때 길이 확인
            temp_chunk = current_chunk + "\n" + line if current_chunk else line

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

    def _generate_text_from_items_with_headers(
        self, items: list[DocItem], header_info_list: list[dict], dl_doc: DoclingDocument, **kwargs
    ) -> str:
        """DocItem 리스트로부터 헤더 정보를 포함한 텍스트 생성"""
        text_parts = []
        current_section_headers = {}  # 현재 섹션의 헤더 정보

        for i, item in enumerate(items):
            item_headers = header_info_list[i] if i < len(header_info_list) else {}

            # 헤더 정보가 변경된 경우 (새로운 섹션 시작)
            if item_headers != current_section_headers:
                # 변경된 헤더 레벨들만 추가
                headers_to_add = []
                for level in sorted(item_headers.keys()):
                    # 이전 섹션과 다른 헤더만 추가
                    if level not in current_section_headers or current_section_headers[level] != item_headers[level]:
                        # 해당 레벨까지의 모든 상위 헤더 포함
                        for l in sorted(item_headers.keys()):
                            if l < level:
                                headers_to_add.append(item_headers[l])
                            elif l == level:
                                headers_to_add.append("")

                        break

                # 헤더가 있으면 추가
                if headers_to_add:
                    header_text = ", ".join(headers_to_add)
                    if header_text not in text_parts:
                        text_parts.append(header_text)

                current_section_headers = item_headers.copy()

            # 아이템 텍스트 추가
            if isinstance(item, TableItem):
                table_text = self._extract_table_text(item, dl_doc, **kwargs)
                if table_text:
                    text_parts.append(table_text)
            elif hasattr(item, "text") and item.text:
                # 타이틀과 섹션 헤더 처리 개선
                # is_section_header = (
                #     isinstance(item, SectionHeaderItem) or
                #     (isinstance(item, TextItem) and
                #      item.label in [DocItemLabel.SECTION_HEADER])  # TITLE은 제외
                # )

                # 타이틀은 항상 포함, 섹션 헤더는 중복 방지를 위해 스킵
                # if not is_section_header:
                # 20250909, shkim, text_parts에 없는 경우만 추가. 섹션헤더가 반복해서 추가되는 것 방지
                if item.text not in text_parts:
                    text_parts.append(item.text)
            elif isinstance(item, PictureItem):
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

    def _extract_table_text(self, table_item: TableItem, dl_doc: DoclingDocument, **kwargs) -> str:
        """테이블에서 텍스트를 추출하는 일반화된 메서드"""
        try:
            if self._resolve_table_format(kwargs) == "markdown":
                table_text = table_item.export_to_markdown(dl_doc)
            else:
                table_text = table_item.export_to_html(dl_doc)
            if table_text and table_text.strip():
                return table_text
        except Exception:
            pass

        # export_to_markdown 실패 시 테이블 셀 데이터에서 직접 텍스트 추출
        try:
            if hasattr(table_item, "data") and table_item.data:
                cell_texts = []

                # table_cells에서 텍스트 추출
                if hasattr(table_item.data, "table_cells"):
                    for cell in table_item.data.table_cells:
                        if hasattr(cell, "text") and cell.text and cell.text.strip():
                            cell_texts.append(cell.text.strip())

                # grid에서 텍스트 추출 (table_cells가 없는 경우)
                elif hasattr(table_item.data, "grid") and table_item.data.grid:
                    for row in table_item.data.grid:
                        if isinstance(row, list):
                            for cell in row:
                                if hasattr(cell, "text") and cell.text and cell.text.strip():
                                    cell_texts.append(cell.text.strip())

                # 추출된 셀 텍스트들을 결합
                if cell_texts:
                    return " ".join(cell_texts)
        except Exception:
            pass

        # 모든 방법 실패 시 item.text 사용 (있는 경우)
        if hasattr(table_item, "text") and table_item.text:
            return table_item.text

        return ""

    @staticmethod
    def _render_table_row_html(row: list, num_cols: int) -> str:
        """grid 한 행을 <tr>..</tr> HTML 로 렌더(docling HTMLTableSerializer 형식 모방).
        colspan 중복 셀은 제거하고 헤더 계열 셀은 <th>, 그 외는 <td> 로 낸다.
        (row_span==1 전제 — 호출부에서 세로 병합 표는 분할하지 않음)
        """
        import html as _html

        cells = []
        for j in range(num_cols):
            cell = row[j]
            if cell.start_col_offset_idx != j:  # colspan 으로 이미 렌더된 셀 스킵
                continue
            is_header = bool(
                getattr(cell, "column_header", False)
                or getattr(cell, "row_header", False)
                or getattr(cell, "row_section", False)
            )
            tag = "th" if is_header else "td"
            attrs = f' colspan="{cell.col_span}"' if cell.col_span > 1 else ""
            cells.append(f"<{tag}{attrs}>{_html.escape((cell.text or '').strip())}</{tag}>")
        return "<tr>" + "".join(cells) + "</tr>"

    @staticmethod
    def _render_table_row_md(row: list, num_cols: int) -> str:
        """grid 한 행을 markdown 표 행 `| c1 | c2 | ... |` 로 렌더(파이프는 이스케이프).
        markdown 은 colspan/rowspan 미지원이라 num_cols 전 컬럼을 그대로 낸다."""
        cells = []
        for j in range(num_cols):
            text = (row[j].text or "").strip().replace("|", "\\|").replace("\n", " ")
            cells.append(text)
        return "| " + " | ".join(cells) + " |"

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
        if name.startswith("sheet: "):
            name = name[len("sheet: ") :]
        name = name.strip()
        return f"시트명: {name}\n" if name else ""

    def _table_item_to_texts(
        self, table_item: TableItem, dl_doc: DoclingDocument, h_short: dict, **kwargs
    ) -> list[str]:
        """표를 청크 텍스트 목록으로 변환. chunk_size(max_tokens) 초과 시 row 단위로 분할하고
        각 분할 청크에 헤더 행(선두 column_header 행 + 다음 컬럼명 행)을 반복 포함한다.

        미초과(또는 max_tokens<=0)면 현행과 동일하게 단일 청크(docling export_to_html) 1개를 반환.
        모든 청크(단일/분할)에 시트명 접두(`시트명: X\\n`)를 붙인다.
        """
        sheet_prefix = self._sheet_prefix(table_item, dl_doc)
        single = sheet_prefix + self._generate_section_text_with_heading([table_item], [h_short], dl_doc, **kwargs)

        if self.max_tokens is None or self.max_tokens <= 0:
            return [single]
        if self._count_tokens(single) <= self.max_tokens:
            return [single]

        try:
            grid = table_item.data.grid
            num_cols = table_item.data.num_cols
        except Exception:
            return [single]
        if not grid or not num_cols:
            return [single]

        # 헤더 행 수: 선두의 연속된 헤더 플래그 행 + 바로 다음 행(컬럼명 추정)
        flag_n = 0
        for row in grid:
            if any(
                getattr(c, "column_header", False)
                or getattr(c, "row_header", False)
                or getattr(c, "row_section", False)
                for c in row
            ):
                flag_n += 1
            else:
                break
        header_n = flag_n + 1
        if header_n >= len(grid):  # 데이터 행이 없음 → 분할 불가
            return [single]

        header_rows = grid[:header_n]
        data_rows = grid[header_n:]

        # 세로 병합(row_span>1)이 데이터 행에 있으면 row 분할이 구조를 깨뜨리므로 분할하지 않는다.
        # (헤더 영역의 세로병합은 헤더 블록이 매 청크에 통째로 반복되므로 무해)
        if any(getattr(c, "row_span", 1) > 1 for r in data_rows for c in r):
            return [single]

        # heading 접두(_generate_section_text_with_heading 과 동일 규칙). xlsx 는 보통 공백.
        merged = {lvl: t for lvl, t in (h_short or {}).items() if t}
        heading = ", ".join(merged[l] for l in sorted(merged)) if merged else ""
        prefix = (heading + ", ") if heading else ""

        # table_format 에 맞춰 헤더/데이터 행을 렌더하고 버킷을 감싼다(html | markdown).
        if self._resolve_table_format(kwargs) == "markdown":
            render_row = self._render_table_row_md
            header_block = [render_row(r, num_cols) for r in header_rows]
            header_block.append("| " + " | ".join(["---"] * num_cols) + " |")

            def wrap(data_rendered: list) -> str:
                return sheet_prefix + prefix + "\n".join(header_block + data_rendered)

        else:
            render_row = self._render_table_row_html
            header_inner = "".join(render_row(r, num_cols) for r in header_rows)

            def wrap(data_rendered: list) -> str:
                return (
                    sheet_prefix
                    + prefix
                    + "<table><tbody>"
                    + header_inner
                    + "".join(data_rendered)
                    + "</tbody></table>"
                )

        texts: list[str] = []
        cur: list[str] = []
        for r in data_rows:
            rr = render_row(r, num_cols)
            if cur and self._count_tokens(wrap(cur + [rr])) > self.max_tokens:
                texts.append(wrap(cur))
                cur = [rr]
            else:
                cur.append(rr)
        if cur:
            texts.append(wrap(cur))
        return texts or [single]

    def _extract_used_headers(self, header_info_list: list[dict]) -> Optional[list[str]]:
        """헤더 정보 리스트에서 실제 사용되는 모든 헤더들을 level 순서대로 추출하고 ', '로 연결"""
        if not header_info_list:
            return None

        all_headers = []  # header 순서대로 추가
        seen_headers = set()  # 중복 방지용

        for header_info in header_info_list:
            if header_info:
                for level in sorted(header_info.keys()):
                    header_text = header_info[level]
                    if header_text and header_text not in seen_headers:
                        all_headers.append(header_text)
                        seen_headers.add(header_text)

        return all_headers if all_headers else None

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

    def _is_section_header(self, item: DocItem) -> bool:
        """아이템이 section header인지 확인"""
        return isinstance(item, SectionHeaderItem) or (
            isinstance(item, TextItem) and item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]
        )

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

    def _generate_section_text_with_heading(
        self, section_items: list[DocItem], section_header_infos: list[dict], dl_doc: DoclingDocument, **kwargs
    ) -> str:
        """섹션의 텍스트를 생성하되, 앞에 heading을 붙임"""
        # 첫 번째 item의 header_info에서 heading 추출
        if section_header_infos and section_header_infos[0]:
            merged_headers = {}
            for level, header_text in section_header_infos[0].items():
                if header_text:
                    merged_headers[level] = header_text

            # level 순서대로 정렬해서 ', '로 연결
            if merged_headers:
                sorted_levels = sorted(merged_headers.keys())
                headers = [merged_headers[level] for level in sorted_levels]
                heading_text = ", ".join(headers)
            else:
                heading_text = ""
        else:
            heading_text = ""

        # 섹션의 일반 텍스트 생성
        section_text = self._generate_text_from_items_with_headers(
            section_items, section_header_infos, dl_doc, **kwargs
        )

        # heading이 있으면 앞에 붙이기
        if heading_text:
            return heading_text + ", " + section_text
        else:
            return section_text

    def _split_document_by_tokens(self, doc_chunk: DocChunk, dl_doc: DoclingDocument, **kwargs) -> list[DocChunk]:
        """문서를 토큰 제한에 맞게 분할 (v2: 섹션 헤더 기준으로 분할 후 max_tokens로 병합)"""
        items = doc_chunk.meta.doc_items
        header_info_list = getattr(doc_chunk, "_header_info_list", [])
        header_short_info_list = getattr(doc_chunk, "_header_short_info_list", [])

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

        def get_current_chunk(
            doc_chunk: DocChunk,
            merged_texts: list[str],
            merged_header_short_infos: list[dict],
            merged_items: list[DocItem],
        ):
            """현재까지 병합된 내용으로 DocChunk 생성"""
            if not merged_texts:
                return None
            chunk_text = "\n".join(merged_texts)
            used_headers = self._extract_used_headers(merged_header_short_infos)

            return DocChunk(
                text=chunk_text,
                meta=DocMeta(
                    doc_items=merged_items,
                    headings=used_headers,
                    captions=None,
                    origin=doc_chunk.meta.origin,
                ),
            )

        def get_text_from_item(item: DocItem) -> str:
            """DocItem에서 텍스트 추출"""
            if isinstance(item, TableItem):
                return self._extract_table_text(item, dl_doc, **kwargs)
            elif hasattr(item, "text") and item.text:
                return item.text
            elif isinstance(item, PictureItem):
                text = ""
                for annotation in item.annotations:
                    if hasattr(annotation, "text"):
                        text += annotation.text
                return text
            return ""

        def split_items_evenly_by_tokens(item_token_counts, max_tokens):
            n = len(item_token_counts)
            total = sum(item_token_counts)
            if n == 0:
                return []
            if total <= max_tokens:
                return [(0, n)]  # ✅ 항상 (a,b)

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
                if 0 < j < len(P):
                    cand.append(j)
                if 0 <= j - 1 < len(P):
                    cand.append(j - 1)

                best = None
                best_dist = float("inf")
                for x in cand:
                    if x in used:
                        continue
                    if x <= cuts[-1]:
                        continue
                    if x >= len(P) - 1:  # n
                        continue
                    dist = abs(P[x] - goal)
                    if dist < best_dist:
                        best_dist = dist
                        best = x

                if best is None:
                    best = min(max(cuts[-1] + 1, 1), len(P) - 2)

                cuts.append(best)
                used.add(best)

            cuts.append(n)

            return [(a, b) for a, b in zip(cuts[:-1], cuts[1:])]

        def adjust_captions(items_group):

            b_modified = False
            for idx, group in enumerate(items_group):
                if group is None:
                    continue
                item = group[0][0]
                ref_idx_list = []
                if hasattr(item, "captions") and item.captions:
                    for cap in item.captions:
                        cap_ref = cap.cref
                        cap_idx = -1
                        for j, it in enumerate(items_group):
                            if it is None:
                                continue
                            if getattr(it[0][0], "self_ref", None) == cap_ref:
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
                if isinstance(item, TableItem) and item.prov:
                    # item.prov can be empty for HTML-sourced items (no page/bbox concept),
                    # so this table<->picture bbox matching only applies when prov exists.
                    table_bbox = item.prov[0].bbox
                    table_page_no = item.prov[0].page_no

                    for j in range(len(items_group)):
                        if items_group[j] is None:
                            continue
                        pic_item = items_group[j][0][0]
                        if isinstance(pic_item, PictureItem) and pic_item.prov:
                            # table 안의 picture인지 확인. iou 사용
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

        # ================================================================
        # 표 단위 청크 분리 (xlsx docling 전용, kwargs: table_as_chunk)
        #   각 TableItem 을 독립 청크로, 사이의 연속 비표 아이템은 별도 청크로 묶는다.
        #   chunk_size(max_tokens) 와 무관하게 표가 병합되지 않도록 토큰 단계 이전에 확정 반환한다.
        # ================================================================
        if kwargs.get("table_as_chunk"):
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
            text = self._generate_section_text_with_heading(items, header_short_infos, dl_doc, **kwargs)
            sections_with_text.append((text, items, header_infos, header_short_infos))

        # ================================================================
        # 2.5단계: 너무 긴 청크는 분할
        # ================================================================
        if self.max_tokens > 0:
            for i in range(len(sections_with_text)):
                text, items, h_infos, h_short = sections_with_text[i]
                token_count = self._count_tokens(text)
                if token_count < self.max_tokens:
                    continue

                # caption 및 table 내 그림은 같은 섹션에 있도록 조정
                items_group = [[(item, info, short)] for item, info, short in zip(items, h_infos, h_short)]
                items_group = adjust_captions(items_group)
                items_group = adjust_pictures_in_tables(items_group)

                # 너무 긴 섹션은 분할
                # 각 아이템 별 token 수 계산
                item_token_counts = []
                for group in items_group:
                    cur_count = 0
                    for g in group:
                        cur_count += self._count_tokens(get_text_from_item(g[0]))
                    item_token_counts.append(cur_count)

                # 아이템 그룹들을 토큰 기준으로 균등 분할
                split_info = split_items_evenly_by_tokens(item_token_counts, self.max_tokens)

                # item_groups를 섹션으로 다시 구성
                new_sections = []
                for a, b in split_info:

                    # 각 그룹에서 items, h_infos, h_short로 분리
                    group_items = []
                    group_h_infos = []
                    group_h_short = []
                    for idx in range(a, b):
                        for g in items_group[idx]:
                            group_items.append(g[0])
                            group_h_infos.append(g[1])
                            group_h_short.append(g[2])

                    new_text = self._generate_section_text_with_heading(group_items, group_h_short, dl_doc, **kwargs)
                    new_sections.append((new_text, group_items, group_h_infos, group_h_short))

                # 원래 섹션을 새로 분할된 섹션들로 교체
                sections_with_text.pop(i)
                for new_section in reversed(new_sections):
                    sections_with_text.insert(i, new_section)

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

            # 다음 섹션과 병합
            sections_with_text[i] = (text + "\n" + n_text, items + n_items, h_infos + n_h_infos, h_short + n_h_short)
            sections_with_text.pop(i + 1)

        # ================================================================
        # 4단계: 토큰 기준 병합 (1차 — 섹션 구조 경계 기준 그룹 생성)
        # ================================================================

        groups: list[dict] = []
        merged_texts, merged_items = [], []
        merged_header_infos, merged_header_short_infos = [], []

        def flush_group():
            if merged_texts:
                groups.append(
                    {
                        "texts": list(merged_texts),
                        "items": list(merged_items),
                        "h_infos": list(merged_header_infos),
                        "h_short": list(merged_header_short_infos),
                    }
                )

        for text, items, header_infos, header_short_infos in sections_with_text:

            b_new_chunk = False

            # ----------------------------------
            # 병합 가능 여부 판단

            # 병합 가능 토큰 수 계산
            test_tokens = self._count_tokens("\n".join(merged_texts + [text]))

            # 현재 섹션헤더 레벨과 병합된 섹션헤더 레벨
            section_level = get_header_level(header_infos, first=True)
            merged_level = get_header_level(merged_header_infos, first=False)

            # 토큰 수 초과 시 새로운 청크 생성
            if test_tokens > self.max_tokens and len(merged_texts) > 0:
                b_new_chunk = True
            # 현재 섹션헤더 레벨이 더 높으면 새로운 청크 생성
            elif 0 <= section_level < merged_level:
                b_new_chunk = True
            # ----------------------------------

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
        if self.max_tokens > 0 and groups:

            def _size(g):
                text = "\n".join(g["texts"])
                headings = self._extract_used_headers(g["h_short"]) or []
                header_line = ("HEADER: " + ", ".join(headings) + "\n") if headings else ""
                # char 모드면 문자 수, huggingface 모드면 토큰 수로 산정 (max_tokens 단위와 일치)
                return self._count_tokens(header_line + text)

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
        # 6단계: 최종 DocChunk 생성
        # ================================================================
        result_chunks = []
        for g in groups:
            cur_chunk = get_current_chunk(doc_chunk, g["texts"], g["h_short"], g["items"])
            if cur_chunk:
                result_chunks.append(cur_chunk)

        return result_chunks

    def _hard_cap_chunk(self, doc_chunk: DocChunk) -> list[DocChunk]:
        """Final defensive size cap.

        _split_document_by_tokens redistributes *existing* doc_items across more
        sections, but cannot subdivide a single doc_item's own text. Unstructured
        HTML sources (e.g. an entire law document dumped into one <pre> block) can
        produce a single DocItem whose text alone is far larger than max_tokens; in
        that case the section-based splitter above yields it as one oversized chunk.
        Cap it here via semchunk (same counter as the rest of this class: char count
        or the configured HF tokenizer) so no chunk that reaches the caller can ever
        exceed max_tokens, regardless of how coarsely the source document was parsed.
        """
        if self.max_tokens is None or self.max_tokens <= 0:
            return [doc_chunk]
        other_len = self._count_tokens(", ".join(doc_chunk.meta.headings or []))
        if self._count_tokens(doc_chunk.text) + other_len <= self.max_tokens:
            return [doc_chunk]
        available = max(self.max_tokens - other_len, 1)
        counter = len if self._tokenizer is None else self._tokenizer
        sem_chunker = semchunk.chunkerify(counter, chunk_size=available)
        segments = sem_chunker(doc_chunk.text) if doc_chunk.text else []
        if not segments:
            return [doc_chunk]
        return [DocChunk(text=seg, meta=doc_chunk.meta) for seg in segments]

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
        capped_chunks = [c for fc in final_chunks for c in self._hard_cap_chunk(fc)]

        return iter(capped_chunks)


class GenOSVectorMeta(BaseModel):
    class Config:
        extra = "allow"

    text: str = None
    n_char: int = None
    n_word: int = None
    n_line: int = None
    i_page: int = None
    i_chunk_on_page: int = None
    n_chunk_of_page: int = None
    i_chunk_on_doc: int = None
    n_chunk_of_doc: int = None
    n_page: int = None
    reg_date: str = None
    bboxes: str = None
    doc_items: list = None


class GenOSVectorMetaBuilder:
    def __init__(self):
        """빌더 초기화"""
        self.text: Optional[str] = None
        self.n_char: Optional[int] = None
        self.n_word: Optional[int] = None
        self.n_line: Optional[int] = None
        self.i_page: Optional[int] = None
        self.i_chunk_on_page: Optional[int] = None
        self.n_chunk_of_page: Optional[int] = None
        self.i_chunk_on_doc: Optional[int] = None
        self.n_chunk_of_doc: Optional[int] = None
        self.n_page: Optional[int] = None
        self.reg_date: Optional[str] = None
        self.bboxes: str = None
        self.doc_items = []
        self.doc_items: list = None
        self.url: str = None
        self.title: str = None
        self.chapter: str = None
        self.section: str = None
        self.article: str = None

    def set_text(self, text: str) -> "GenOSVectorMetaBuilder":
        """텍스트와 관련된 데이터를 설정"""
        self.text = self.title + text
        self.n_char = len(text)
        self.n_word = len(text.split())
        self.n_line = len(text.splitlines())
        return self

    def set_page_info(self, i_page: int, i_chunk_on_page: int, n_chunk_of_page: int) -> "GenOSVectorMetaBuilder":
        """페이지 정보 설정"""
        self.i_page = i_page
        self.i_chunk_on_page = i_chunk_on_page
        self.n_chunk_of_page = n_chunk_of_page
        return self

    def set_chunk_index(self, i_chunk_on_doc: int) -> "GenOSVectorMetaBuilder":
        """문서 전체의 청크 인덱스 설정"""
        self.i_chunk_on_doc = i_chunk_on_doc
        return self

    def set_bboxes(self, bbox: BoundingBox) -> "GenOSVectorMetaBuilder":
        """Bounding Boxes 정보 설정"""
        #         bboxes.append({
        #             'p1': {'x': rect[0] / fitz_page.rect.width, 'y': rect[1] / fitz_page.rect.height},
        #             'p2': {'x': rect[2] / fitz_page.rect.width, 'y': rect[3] / fitz_page.rect.height},
        #         })
        # NOTE: docling은 BOTTOMLEFT인데 해당 좌표 그대로 활용되는지 ?
        conv = []
        if bbox != []:
            conv.append(
                {
                    "p1": {"x": bbox.l, "y": bbox.t},
                    "p2": {"x": bbox.r, "y": bbox.b},
                }
            )
        else:
            # conv.append({
            #     'p1': {'x': 0, 'y': 0},
            #     'p2': {'x': 0, 'y': 0},
            # })
            conv.append({})
        self.bboxes = json.dumps(conv)
        return self

    def set_global_metadata(self, **global_metadata) -> "GenOSVectorMetaBuilder":
        """글로벌 메타데이터 병합"""
        for key, value in global_metadata.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self

    def set_doc_items(self, doc_items: list) -> "GenOSVectorMetaBuilder":
        self.doc_items = doc_items
        return self

    def set_doc_url(self, url: str) -> "GenOSVectorMetaBuilder":
        self.url = url
        return self

    def set_doc_headings(self, headings: list) -> "GenOSVectorMetaBuilder":
        re_pattern_jang = r"^제\s*\d{1,3}\s*장.*"
        re_pattern_jeol = r"^제\s*\d{1,3}\s*절.*"
        re_pattern_jo = r"^제\s*\d{1,3}\s*조.*"
        for h in headings:
            if re.match(re_pattern_jang, h):
                self.chapter = h
            elif re.match(re_pattern_jeol, h):
                self.section = h
            elif re.match(re_pattern_jo, h):
                if ")" in h:
                    self.article = h[: h.find(")") + 1]
                else:
                    self.article = h[: h.find("조") + 1]
            elif h != "":
                self.title = h
        return self

    def build(self) -> GenOSVectorMeta:
        """설정된 데이터를 사용해 최종적으로 GenOSVectorMeta 객체 생성"""
        return GenOSVectorMeta(
            text=self.text,
            n_char=self.n_char,
            n_word=self.n_word,
            n_line=self.n_line,
            i_page=self.i_page,
            i_chunk_on_page=self.i_chunk_on_page,
            n_chunk_of_page=self.n_chunk_of_page,
            i_chunk_on_doc=self.i_chunk_on_doc,
            n_chunk_of_doc=self.n_chunk_of_doc,
            n_page=self.n_page,
            reg_date=self.reg_date,
            bboxes=self.bboxes,
            # doc_items=self.doc_items,
            doc_url=self.url,
            title=self.title,
            chapter=self.chapter,
            section=self.section,
            article=self.article,
        )


class DocumentProcessor:

    def __init__(self, config_path: str | None = None):
        """
        initialize Document Converter

        config_path defaults to resource_dev/administrative_rule_processor_config.yaml
        (falling back to resource/administrative_rule_processor_config.yaml).
        """
        if config_path is None:
            config_path = _resolve_default_config_path()
        cfg = _load_config(config_path)
        chunking_cfg = _as_dict(cfg.get("chunking"))
        self._tokenizer = _resolve_tokenizer(chunking_cfg)
        self._tokenizer_type = str(chunking_cfg.get("tokenizer_type", "char")).strip().lower()
        if self._tokenizer_type not in {"char", "huggingface"}:
            _log.warning(
                f"[DocumentProcessor] Unknown chunking.tokenizer_type '{self._tokenizer_type}', fallback to 'char'."
            )
            self._tokenizer_type = "char"
        chunk_size = _parse_optional_int(chunking_cfg.get("chunk_size"), "chunking.chunk_size")
        self._chunk_size = chunk_size if chunk_size is not None else 1024

        self.page_chunk_counts = defaultdict(int)
        device = AcceleratorDevice.AUTO
        num_threads = 4
        table_mode = TableFormerMode.FAST
        accelerator_options = AcceleratorOptions(num_threads=num_threads, device=device)
        pipe_line_options = PdfPipelineOptions()
        pipe_line_options.generate_page_images = True
        # pipe_line_options.generate_table_images = True
        pipe_line_options.generate_picture_images = True
        pipe_line_options.do_ocr = True
        pipe_line_options.ocr_options.lang = ["ko", "en"]
        pipe_line_options.ocr_options.model_storage_directory = "/nfs-root/aiModel/.EasyOCR/model"
        # pipe_line_options.ocr_options.model_storage_directory = "/home/mnc/temp/.EasyOCR/model"
        # pipe_line_options.artifacts_path = Path("/home/mnc/temp/.cache/huggingface/hub/models--ds4sd--docling-models/snapshots/36bebf56681740529abd09f5473a93a69373fbf0/")
        pipe_line_options.do_table_structure = True
        pipe_line_options.images_scale = 2
        pipe_line_options.table_structure_options.do_cell_matching = True
        pipe_line_options.accelerator_options = accelerator_options

        self.converter = DocumentConverter(
            format_options={
                InputFormat.HTML: HTMLFormatOption(
                    # pipeline_options=pipe_line_options,
                    # backend=HTMLDocumentBackend
                )
            }
        )

    def load_documents_with_docling(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        # docling 설정
        # 필요에 따라 사용자 지정 가능. 여기서는 genos_vanilla 와 비슷하게 PDF를 처리한다 가정.
        # TODO: kwargs 와의 연결
        # TODO: Langchain document 를 꼭 써야하나?
        # 실제 변환 실행
        # ConversionResult 리스트를 받는다.
        #
        # NOTE: 처리시 파일 하나를 병렬로 처리하는지?? 아니면 폴더 단위로 병렬 처리 하는지??
        # NOTE: 파일 하나 처리 시 convert로 변경.
        # conv_results = doc_converter.convert_all([file_path], raises_on_error=True)
        conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)

        return conv_result.document

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        # ducling 방식으로 문서 로드
        return self.load_documents_with_docling(file_path, **kwargs)
        # return documents

    def split_documents(self, documents: DoclingDocument, **kwargs: dict) -> List[DocChunk]:
        # chunk_size priority: kwargs > yaml(chunking.chunk_size) > class default (1024)
        chunk_size = _parse_optional_int(kwargs.get("chunk_size"), "chunk_size")
        if chunk_size is None:
            chunk_size = self._chunk_size
        chunker: GenosSmartChunker = GenosSmartChunker(
            max_tokens=chunk_size if chunk_size is not None else 0,
            merge_peers=True,
            tokenizer=self._tokenizer,
            tokenizer_type=self._tokenizer_type,
        )
        chunks: List[DocChunk] = list(chunker.chunk(dl_doc=documents, **kwargs))
        for chunk in chunks:
            self.page_chunk_counts[1] += 1
        return chunks

    def safe_join(self, iterable):
        if not isinstance(iterable, (list, tuple, set)):
            return ""
        return "".join(map(str, iterable)) + "\n"

    def compose_vectors(
        self, document: DoclingDocument, chunks: List[DocChunk], file_path: str, **kwargs: dict
    ) -> list[dict]:
        pdf_path = file_path.replace(".hwp", ".pdf").replace(".txt", ".pdf").replace(".json", ".pdf")

        # if os.path.exists(pdf_path):
        #     doc = fitz.open(pdf_path)

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec="seconds") + "Z",
        )

        current_page = None
        chunk_index_on_page = 0
        vectors = []
        doc_url = ""

        for chunk_idx, chunk in enumerate(chunks):
            ## NOTE: chunk가 두 페이지에 걸쳐 있는 경우 첫번째 아이템을 사용
            ## NOTE: chunk가 두 페이지에 걸쳐서 있는 경우 bounding box 처리를 어떻게 해야하는 지...
            ## NOTE: 현재 구조에서는 처리가 불가
            ## NOTE: 임시로 페이지 넘어가는 경우 chunk를 분할해서 처리
            # chunk_page = chunk.meta.doc_items[0].prov[0].page_no
            chunk_page = 1
            content = chunk.text
            if chunk_idx == 0 and chunk.text.startswith("http://"):
                # chunk.text can have later merged items (tables/pictures/captions)
                # appended after the URL, so only take the first line for the URL,
                # not the whole merged chunk text. A large enough chunk_size can
                # merge the URL together with the rest of the document's real
                # content into this single chunk (instead of the URL/nav-button
                # boilerplate staying alone, as it does at smaller chunk_size) —
                # only treat that leading boilerplate as droppable when nothing
                # real (i.e. no heading) got merged into it.
                doc_url, _, rest = chunk.text.partition("\n")
                global_metadata["url"] = doc_url
                if not rest.strip() or not chunk.meta.headings:
                    continue
                content = rest
            # content = self.safe_join(chunk.meta.headings) + content
            vector = (
                GenOSVectorMetaBuilder()
                .set_doc_headings(chunk.meta.headings)
                .set_text(content)
                .set_page_info(chunk_page, chunk_index_on_page, self.page_chunk_counts[chunk_page])
                .set_chunk_index(chunk_idx)
                #   .set_bboxes(chunk.meta.doc_items[0].prov[0].bbox)
                .set_bboxes([])
                .set_global_metadata(**global_metadata)
                # .set_doc_items(chunk.meta.doc_items)
                .set_doc_url(doc_url)
            ).build()
            vectors.append(vector)

            # page = chunk_page
            # text = chunk.page_content

            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0

            # bboxes_json = None
            # if os.path.exists(pdf_path):
            #     fitz_page = doc.load_page(page)
            #     bboxes = []
            #     # text 검색 시 fitz의 search_for 문맥이 주어진 text chunk 에 매칭되는 바운딩박스를 찾을 수 있는지 확인
            #     # 많은 경우 chunk가 PDF 내 같은 text를 그대로 match하지 못할 수 있음.
            #     # 여기서는 원본 genos_vanilla와 동일한 로직 유지.
            #     # 특정 성능 문제나 결과 없을 경우 try-except 추가 가능.
            #     search_results = fitz_page.search_for(text)
            #     for rect in search_results:
            #         bboxes.append({
            #             'p1': {'x': rect[0] / fitz_page.rect.width, 'y': rect[1] / fitz_page.rect.height},
            #             'p2': {'x': rect[2] / fitz_page.rect.width, 'y': rect[3] / fitz_page.rect.height},
            #         })
            #     bboxes_json = json.dumps(bboxes)

            chunk_index_on_page += 1

        return vectors

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):

        # in_doc = InputDocument(
        #     path_or_stream=file_path,
        #     format=InputFormat.HTML,
        #     backend=HTMLDocumentBackend,
        # )
        # backend = HTMLDocumentBackend(
        #     in_doc=in_doc,
        #     path_or_stream=file_path,
        # )
        # document = backend.convert()

        # old_file = file_path
        # file_path = file_path[:-4] + ".html"
        # subprocess.run(["mv", old_file, file_path])
        # subprocess.run(["touch", old_file+".testfile.txt"])
        code = """
from bs4 import BeautifulSoup
import sys
file = sys.argv[1]
with open(file, "r", encoding="utf-8") as f:
    html_content = f.read()
soup = BeautifulSoup(html_content, "html.parser")
first_script = soup.find('script')
if first_script:
    first_script.decompose()
head_title = soup.find('title')
if head_title:
    head_title.decompose()
if not soup.body:
    sys.exit()
nav_buttons = soup.find(id="tbon")
if nav_buttons:
    nav_buttons.decompose()
original_table = soup.find("table", {"align":"center"})
if original_table:
    new_tables = []
    trs = original_table.find_all("tr")
    for tr in trs:
        if tr.find("span", {"class":"titleText"}):
            currnet_table = soup.new_tag("table")
            new_tables.append(currnet_table)
        currnet_table.append(tr.extract())
    for new_table in new_tables:
        original_table.insert_before(new_table)
    original_table.decompose()
for span in soup.find_all('span'):
    span.name = 'p'
for pre in soup.find_all('pre'):
    lines = pre.get_text().split("\\n")
    new_tags = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        p = soup.new_tag('p')
        p.string = line
        new_tags.append(p)
    for p in new_tags:
        pre.insert_before(p)
    pre.decompose()
with open(file, "w", encoding="utf-8") as f:
    f.write(str(soup))
        """
        subprocess.run(["python3", "-c", code, file_path])
        document: DoclingDocument = self.load_documents(file_path, **kwargs)
        await assert_cancelled(request)

        # Extract Chunk from DoclingDocument
        chunks: List[DocChunk] = self.split_documents(document, **kwargs)
        await assert_cancelled(request)

        vectors = []
        if len(chunks) > 0:
            vectors: list[dict] = self.compose_vectors(document, chunks, file_path, **kwargs)
            print("@@@@", vectors[0])
        else:
            raise GenosServiceException(1, f"chunk length is 0")
        return vectors


class GenosServiceException(Exception):
    # GenOS 와의 의존성 부분 제거를 위해 추가
    def __init__(self, error_code: str, error_msg: Optional[str] = None, msg_params: Optional[dict] = None) -> None:
        self.code = 1
        self.error_code = error_code
        self.error_msg = error_msg or "GenOS Service Exception"
        self.msg_params = msg_params or {}

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        return f"{class_name}(code={self.code!r}, errMsg={self.error_msg!r})"


# GenOS 와의 의존성 제거를 위해 추가
async def assert_cancelled(request: Request):
    if await request.is_disconnected():
        raise GenosServiceException(1, f"Cancelled")
