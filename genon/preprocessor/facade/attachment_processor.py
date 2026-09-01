# 첨부용 전처리기 v.2.2.4 (2026-07-30 Release)
from __future__ import annotations

from collections import defaultdict

import asyncio
import fitz
import os
import subprocess
from datetime import datetime
import logging
from fastapi import Request
from PIL import Image

_log = logging.getLogger(__name__)


# ── 공용 하위 모듈로 옮긴 헬퍼들의 별칭 ──────────────────────────────
# 구현은 facade/common/, facade/chunking/ 에 한 벌만 둔다. 여기서는 기존 이름을
# 그대로 유지해 호출부를 건드리지 않는다. 사이트별 조정 대상 상수(구분자, 최소
# 청크 크기, 토크나이저 경로)는 이 파일에 남아 있으므로 래퍼가 넘겨준다.
from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.common import loaders as ld
from genon.preprocessor.facade.common import vector_meta as vm
from genon.preprocessor.facade.common import runtime as rt
from genon.preprocessor.facade.common import file_probe as fp
from genon.preprocessor.facade.common import pdf_convert as pc
from genon.preprocessor.facade.chunking import hybrid_chunker as hc

_as_dict = cp.as_dict
_detect_unsupported_file = fp.detect_unsupported_file
_is_encrypted_office = fp.is_encrypted_office
_is_encrypted_pdf = fp.is_encrypted_pdf
_is_protected_hwp = fp.is_protected_hwp
_looks_like_text = fp.looks_like_text
_parse_optional_bool = cp.parse_optional_bool
_parse_optional_float = cp.parse_optional_float
_parse_optional_int = cp.parse_optional_int
_warn_unresolved_placeholders = cp.warn_unresolved_placeholders


def _load_config(config_path: str) -> dict:
    return cp.load_config(config_path, strict=False)

def _resolve_tokenizer(chunking_cfg: dict):
    return cp.resolve_tokenizer(
        chunking_cfg, local_path=_DEFAULT_TOKENIZER_LOCAL_PATH, hf_id=_DEFAULT_TOKENIZER_ID)


from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import (
    # TextLoader,                       # TXT
    PyMuPDFLoader,  # PDF
    UnstructuredWordDocumentLoader,  # DOC and DOCX
    UnstructuredPowerPointLoader,  # PPT and PPTX
    UnstructuredImageLoader,  # JPG, PNG
    UnstructuredMarkdownLoader,  # Markdown
    UnstructuredFileLoader,  # Generic fallback
)
from langchain_core.documents import Document
from markdown2 import markdown
from pathlib import Path
from pydantic import BaseModel
from typing import Any, List, Optional

try:
    import chardet
except ImportError:
    raise RuntimeError("Module 'chardet' not imported. Run `pip install chardet`.")
try:
    from weasyprint import HTML
except (ImportError, OSError):
    # OSError: weasyprint 는 설치돼 있으나 네이티브 라이브러리(libgobject-2.0 등)가 없는 환경.
    # parser_processor.py 의 동일 블록과 같은 형태를 유지한다.
    print("Warning: WeasyPrint could not be imported. PDF conversion features will be disabled.")
    HTML = None

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PipelineOptions
from docling.datamodel.document import ConversionResult
from docling.pipeline.simple_pipeline import SimplePipeline
from docling.document_converter import (
    DocumentConverter, HwpxFormatOption, WordFormatOption,
)
from genon.preprocessor.facade.enrichment.page_description import (
    PageDescriptionOptions,
    describe_page_images,
    should_describe,
)
from genon.preprocessor.facade.chunking.table_splitter import (
    leading_header_row_count,
    split_table_rows,
)
from docling_core.transforms.chunker import DocChunk
from genon.preprocessor.facade.common.markdown_export import (
    export_markdown,
    markdown_params,
)
from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
from docling_core.types.doc import (
    DocItem, DoclingDocument, PictureItem, TableItem
)
from docling.backend.genos_msword_backend import GenosMsWordDocumentBackend
from docling.backend.genos_hwp_backend import GenosHwpDocumentBackend
from docling.backend.hwp_backend import HwpDocumentBackend
from docling.backend.xml.hwpx_backend import HwpxDocumentBackend
from docling.exceptions import HwpConversionError

try:
    from genos_utils import upload_files
except ImportError:
    upload_files = None


import logging

for n in ("fontTools", "fontTools.ttLib", "fontTools.ttLib.ttFont"):
    lg = logging.getLogger(n)
    lg.setLevel(logging.CRITICAL)
    lg.propagate = False
    logging.getLogger().setLevel(logging.WARNING)
# pdf 변환 대상 확장자
CONVERTIBLE_EXTENSIONS = ['.hwp', '.txt', '.json', '.md', '.ppt', '.pptx', '.docx']

_DEFAULT_TOKENIZER_LOCAL_PATH = "/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2"
_DEFAULT_TOKENIZER_ID = "sentence-transformers/all-MiniLM-L6-v2"
_DEFAULT_HYBRID_MAX_TOKENS = int(1e30)


_resolve_compact_tables = hc.resolve_compact_tables


def _resolve_default_attachment_config_path() -> str:
    base_dir = Path(__file__).resolve().parent
    local_config = (base_dir / "../resource_dev/attachment_processor_config.yaml").resolve()
    default_config = (base_dir / "../resource/attachment_processor_config.yaml").resolve()

    if local_config.exists():
        return str(local_config)
    return str(default_config)


def convert_to_pdf(file_path: str, use_pdf_sdk: bool = True) -> str | None:
    """PDF 변환을 시도한다. 실패해도 예외를 던지지 않고 None 을 반환한다.

    chain (HWP/HWPX 입력):
      use_pdf_sdk=True  → pdf_sdk → rhwp → libreoffice
      use_pdf_sdk=False → rhwp → libreoffice
    chain (그 외 입력, 예: docx/pptx):
      use_pdf_sdk=True  → pdf_sdk → libreoffice
      use_pdf_sdk=False → libreoffice

    구현은 facade/common/pdf_convert.py 에 있다(변환 backend 는
    genon.preprocessor.converters.hwp_to_pdf).
    """
    return pc.convert_to_pdf(file_path, use_pdf_sdk=use_pdf_sdk)


def _has_any_pdf_converter() -> bool:
    return fp.has_any_pdf_converter()


def _get_pdf_path(file_path: str) -> str:
    """변환 가능한 확장자면 PDF 경로로 바꾼다(구현은 facade/common/file_probe.py)."""
    return fp.get_pdf_path(file_path, CONVERTIBLE_EXTENSIONS)


install_packages = ld.install_packages
# 민감정보 분류/마스킹(#315)은 facade/guardrail 모듈로 분리 — gr.* 로 사용.
from genon.preprocessor.facade import guardrail as gr


class GenOSVectorMeta(BaseModel):
    class Config:
        extra = 'allow'

    text: str | None = None
    n_char: int | None = None
    n_word: int | None = None
    n_line: int | None = None
    i_page: int | None = None
    e_page: int | None = None
    i_chunk_on_page: int | None = None
    n_chunk_of_page: int | None = None
    i_chunk_on_doc: int | None = None
    n_chunk_of_doc: int | None = None
    n_page: int | None = None
    reg_date: str | None = None
    chunk_bboxes: str | None = None
    media_files: str | None = None
    guardrail_categories: Optional[list] = None    # #315 민감정보 분류 라벨(부동산/인사/민감 등). 미적용 시 None
    # 표 메타(#360). 첨부 경로는 표 단위 분할 순서를 기록하지 않아 refs 까지만 채운다.
    has_table: bool = False
    table_refs: Optional[str] = None


class GenOSVectorMetaBuilder(vm.VectorMetaBuilderBase):
    """공통 세터는 facade/common/vector_meta.py 에 있다.

    첨부 프로세서는 벡터 고유 필드가 없고, 미디어 파일 처리만 다르다."""

    def set_media_files(self, doc_items: list) -> "GenOSVectorMetaBuilder":
        # 첨부는 표 이미지를 싣지 않고, 빈 목록이면 "[]" 대신 "" 를 넣는다(기존 동작).
        if not doc_items:
            self.media_files = ""
            return self
        return super().set_media_files(doc_items)

    def build(self) -> GenOSVectorMeta:
        """설정된 데이터를 사용해 최종적으로 GenOSVectorMeta 객체 생성"""
        return GenOSVectorMeta(**self.core_payload())

class TextLoader(ld.TextLoaderBase):
    pass


class TabularLoader(ld.TabularLoaderBase):
    def return_vectormeta_format(self):
        if not self.data_dict:
            return None

        text = "[DA] " + str(self.data_dict)  # Add a token to indicate this string is for data analysis
        vectors = [GenOSVectorMeta.model_validate({
            'text': text,
            'n_char': 1,
            'n_word': 1,
            'n_line': 1,
            'i_page': 1,
            'e_page': 1,
            'n_page': 1,
            'i_chunk_on_page': 1,
            'n_chunk_of_page': 1,
            'i_chunk_on_doc': 1,
            'reg_date': datetime.now().isoformat(timespec='seconds') + 'Z',
            'chunk_bboxes': ".",
            'media_files': "."
        })]
        return vectors


class AudioLoader(ld.AudioLoaderBase):
    def return_vectormeta_format(self):
        audio_chunks = self.split_file_as_chunks()
        transcribed_text = self.transcribe_audio(audio_chunks)
        res = [GenOSVectorMeta.model_validate({
            'text': transcribed_text,
            'n_char': 1,
            'n_word': 1,
            'n_line': 1,
            'i_page': 1,
            'e_page': 1,
            'n_page': 1,
            'i_chunk_on_page': 1,
            'n_chunk_of_page': 1,
            'i_chunk_on_doc': 1,
            'reg_date': datetime.now().isoformat(timespec='seconds') + 'Z',
            'chunk_bboxes': ".",
            'media_files': "."
        })]
        return res


### for HWPX from 지능형 전처리기 ###
#  * GenOSVectorMetaBuilder     #
#  * HwpxProcessor              #
#  * GenosServiceException      #

# HierarchicalChunker / HybridChunker 는 docling_core 포크본이라
# facade/chunking/hybrid_chunker.py 로 옮겼다. 아래는 호출부를 그대로 두기 위한 별칭이다.
# 이름을 바꾼 이유와 업스트림과 갈라진 지점은 그 모듈 docstring 에 있다.
HierarchicalChunker = hc.HierarchicalDocChunker
HybridChunker = hc.TokenAwareHybridChunker


# --- 이슈 #183 / #80 -------------------------------------------------------
# DoclingDocument를 markdown으로 export한 뒤 RecursiveCharacterTextSplitter로 분할.
# 페이지 정보는 export_to_markdown(page_break_placeholder=...)로 삽입한 마커를
# 청크별로 카운트해 복원한다. 한 청크가 여러 페이지에 걸칠 수 있다.
_RECURSIVE_PAGE_BREAK = "<!-- PB -->"


def _char_split_text(text: str, chunk_size=None, chunk_overlap=None) -> list[str]:
    """문자수 기반 청킹 공용 헬퍼 (generic/recursive 경로 공유).

    chunk_size 가 0 이하/None 이면 분할하지 않고 전체를 1청크로 둔다.
    chunk_size > 0 이면 RecursiveCharacterTextSplitter 로 문자 단위 분할한다.
    """
    if not text:
        return []

    cs = int(chunk_size) if chunk_size is not None else 0
    co = max(int(chunk_overlap), 0) if chunk_overlap is not None else 100

    if cs > 0:
        # overlap >= size 면 RecursiveCharacterTextSplitter 가 ValueError 로 크래시하므로 size-1 이하로 클램프.
        co = min(co, cs - 1)
        raw_chunks = RecursiveCharacterTextSplitter(
            chunk_size=cs, chunk_overlap=co,
        ).split_text(text)
    else:
        raw_chunks = [text]

    return [c for c in raw_chunks if c]


def _split_with_recursive_chunker(
    document: DoclingDocument,
    chunk_size=None,
    chunk_overlap=None,
    compact_tables: bool = True,
) -> List[dict]:
    """Markdown export + 문자수 기반 청킹(_char_split_text)으로 docling 문서를 분할.

    chunk_size 로 문자 분할 (0 이하이면 분할 안 함 = 전체 1청크).
    compact_tables=True 면 markdown 표의 컬럼 정렬 패딩을 제거한다(대형 표 축소).

    Returns: list of dict {text, page_no, pages, doc_items}
    """
    md_full = export_markdown(
        document,
        page_break_placeholder=_RECURSIVE_PAGE_BREAK,
        compact_tables=compact_tables,
    )
    if not md_full:
        return []

    cs = int(chunk_size) if chunk_size is not None else 0
    co = max(int(chunk_overlap), 0) if chunk_overlap is not None else 100

    # (text, 원본 markdown 시작 위치, 끝 위치, 명시적 doc_items) 목록. chunk_size가
    # 활성화된 경우 TableItem의 정확한 직렬화 구간을 먼저 찾아 표 밖 텍스트만
    # RecursiveCharacterTextSplitter에 보낸다. 따라서 표의 행/헤더 구조를 잃지 않는다.
    positioned_chunks: list[tuple[str, int, int, Optional[list[DocItem]]]] = []
    table_spans: list[tuple[int, int, TableItem]] = []
    if cs > 0:
        search_cursor = 0
        serializer = MarkdownDocSerializer(
            doc=document,
            params=markdown_params(compact_tables=compact_tables),
        )
        for item, _ in document.iterate_items():
            if not isinstance(item, TableItem):
                continue
            try:
                table_md = serializer.serialize(item=item).text
            except Exception:
                table_md = export_markdown(document, item=item)
            if not table_md:
                continue
            pos = md_full.find(table_md, search_cursor)
            if pos < 0:
                _log.warning(
                    "[recursive chunker] Markdown 원문에서 표 구간을 찾지 못해 해당 표는 "
                    "기존 문자 분할 경로를 사용합니다: table=%s",
                    getattr(item, "self_ref", ""),
                )
                continue
            end = pos + len(table_md)
            table_spans.append((pos, end, item))
            search_cursor = end

    def _append_plain_segment(start: int, end: int) -> None:
        segment = md_full[start:end]
        local_cursor = 0
        search_backoff = max(co * 4, 200)
        for raw in _char_split_text(segment, chunk_size=chunk_size, chunk_overlap=chunk_overlap):
            local_pos = segment.find(raw, max(0, local_cursor - search_backoff))
            if local_pos < 0:
                local_pos = local_cursor
            local_end = local_pos + len(raw)
            positioned_chunks.append((raw, start + local_pos, start + local_end, None))
            local_cursor = local_end

    if table_spans:
        cursor = 0
        for start, end, table_item in table_spans:
            if start > cursor:
                _append_plain_segment(cursor, start)
            table_md = md_full[start:end]
            try:
                grid = table_item.data.grid
                num_cols = table_item.data.num_cols
            except Exception:
                grid, num_cols = None, 0
            if grid and num_cols:
                result = split_table_rows(
                    grid=grid,
                    num_cols=num_cols,
                    single_text=table_md,
                    limit=cs,
                    count_text=len,
                    table_format="markdown",
                    header_row_count=max(leading_header_row_count(grid), 1),
                )
                for index in result.oversized_piece_indexes:
                    _log.warning(
                        "[recursive chunker] 표의 단일 행이 chunk_size(%d)를 초과해 "
                        "행 구조를 보존한 채 유지합니다: table=%s size=%d",
                        cs, getattr(table_item, "self_ref", ""), len(result.pieces[index]),
                    )
                positioned_chunks.extend(
                    (piece, start, end, [table_item]) for piece in result.pieces
                )
            else:
                positioned_chunks.append((table_md, start, end, [table_item]))
            cursor = end
        if cursor < len(md_full):
            _append_plain_segment(cursor, len(md_full))
    else:
        _append_plain_segment(0, len(md_full))

    # 페이지별 doc_items 캐시 (반복 조회 방지)
    page_items_cache: dict[int, list] = {}

    def _items_for_page(p: int):
        if p not in page_items_cache:
            page_items_cache[p] = [
                it for it, _ in document.iterate_items(page_no=p)
                if isinstance(it, DocItem)
            ]
        return page_items_cache[p]

    results: list[dict] = []
    for raw, pos, end_pos, explicit_items in positioned_chunks:

        start_page = md_full[:pos].count(_RECURSIVE_PAGE_BREAK) + 1
        end_page = md_full[:end_pos].count(_RECURSIVE_PAGE_BREAK) + 1

        text = raw.replace(_RECURSIVE_PAGE_BREAK, "").strip()
        if not text:
            continue

        doc_items: list = list(explicit_items or [])
        if explicit_items is None:
            for p in range(start_page, end_page + 1):
                doc_items.extend(_items_for_page(p))

        results.append({
            "text": text,
            "page_no": start_page,
            "pages": list(range(start_page, end_page + 1)),
            "doc_items": doc_items,
        })

    return results


class DocxProcessor:
    def __init__(self, tokenizer=None, guardrail_url="", guardrail_workflow_id=None, guardrail_api_key="", guardrail_timeout=30, guardrail_masking_enabled=False):
        # 청킹용 토크나이저 (config 기반; 미지정 시 현행 기본값)
        self._tokenizer = tokenizer if tokenizer is not None else _resolve_tokenizer({})
        # PII 마스킹(#315) 접속 정보 — DocumentProcessor 가 config 에서 읽어 주입.
        self._guardrail_url = guardrail_url
        self._guardrail_workflow_id = guardrail_workflow_id
        self._guardrail_api_key = guardrail_api_key
        self._guardrail_timeout = guardrail_timeout
        self._guardrail_masking_enabled = guardrail_masking_enabled
        self.page_chunk_counts = defaultdict(int)
        self.pipeline_options = PipelineOptions()
        self.converter = DocumentConverter(
            format_options={
                InputFormat.DOCX: WordFormatOption(
                pipeline_cls=SimplePipeline, backend=GenosMsWordDocumentBackend
                ),
            }
        )

    def get_paths(self, file_path: str):
        output_path, output_file = os.path.split(file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(f"{output_path}/{filename}")
        if artifacts_dir.is_absolute():
            reference_path = None
        else:
            reference_path = artifacts_dir.parent
        return artifacts_dir, reference_path

    def get_media_files(self, doc_items: list):
        temp_list = []
        for item in doc_items:
            if isinstance(item, PictureItem) and item.image:
                path = str(item.image.uri)
                name = path.rsplit("/", 1)[-1]
                temp_list.append({'path': path, 'name': name})
        return temp_list

    def safe_join(self, iterable):
        if not isinstance(iterable, (list, tuple, set)):
            return ''
        return ''.join(map(str, iterable)) + '\n'

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)
        return conv_result.document

    def split_documents(self, document: DoclingDocument, **kwargs: dict):
        """chunker_type에 따라 HybridChunker 또는 RecursiveCharacterTextSplitter로 분할.

        반환 형식이 chunker_type에 따라 다르다 (DocChunk 리스트 또는 dict 리스트).
        compose_vectors가 동일한 chunker_type 분기로 처리한다.
        """
        # 같은 DocxProcessor 인스턴스가 여러 요청에서 재사용되므로 매 호출마다 초기화
        self.page_chunk_counts = defaultdict(int)
        chunker_type = kwargs.get("chunker_type", "recursive")

        if chunker_type == "recursive":
            recursive_chunk_size = kwargs.get("chunk_size")
            if recursive_chunk_size is None:
                recursive_chunk_size = kwargs.get("recursive_chunk_size")
            recursive_chunk_overlap = kwargs.get("chunk_overlap")
            if recursive_chunk_overlap is None:
                recursive_chunk_overlap = kwargs.get("recursive_chunk_overlap")
            chunks = _split_with_recursive_chunker(
                document,
                chunk_size=recursive_chunk_size,
                chunk_overlap=recursive_chunk_overlap,
                compact_tables=_resolve_compact_tables(kwargs),
            )
            for ch in chunks:
                self.page_chunk_counts[ch["page_no"]] += 1
            return chunks

        # hybrid
        hybrid_chunk_size = _parse_optional_int(kwargs.get("hybrid_chunk_size"), "hybrid_chunk_size")
        if hybrid_chunk_size is None or hybrid_chunk_size <= 0:
            hybrid_chunk_size = _DEFAULT_HYBRID_MAX_TOKENS
        hybrid_merge_peers = _parse_optional_bool(kwargs.get("hybrid_merge_peers"), "hybrid_merge_peers")
        if hybrid_merge_peers is None:
            hybrid_merge_peers = True
        chunker_kwargs = {
            "max_tokens": hybrid_chunk_size,
            "merge_peers": hybrid_merge_peers,
            "tokenizer": self._tokenizer,
            "tokenizer_type": kwargs.get("hybrid_tokenizer_type", "char"),
        }
        chunker = HybridChunker(**chunker_kwargs)
        chunks: List[DocChunk] = list(chunker.chunk(dl_doc=document, **kwargs))
        for chunk in chunks:
            if chunk.meta.doc_items[0].prov:
                self.page_chunk_counts[chunk.meta.doc_items[0].prov[0].page_no] += 1
        return chunks

    async def compose_vectors(self, document: DoclingDocument, chunks, file_path: str, request: Request,
                              **kwargs: dict) -> list[dict]:
        chunker_type = kwargs.get("chunker_type", "recursive")
        _sensitive_infos: list = kwargs.get("_sensitive_infos") or []      # #315 분류 결과
        _gr_masking: bool = bool(kwargs.get("_guardrail_masking", False))   # #315 마스킹 치환 on/off

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
        )

        current_page = None
        chunk_index_on_page = 0
        vectors = []
        upload_tasks = []
        scheduled_upload_paths = set()  # 청크 간 동일 이미지(헤더 그림 등) 중복 업로드 방지
        for chunk_idx, chunk in enumerate(chunks):
            if chunker_type == "recursive":
                chunk_page = chunk["page_no"]
                content = chunk["text"]
                doc_items = chunk["doc_items"]
            else:
                chunk_page = chunk.meta.doc_items[0].prov[0].page_no if chunk.meta.doc_items[0].prov else 0
                content = self.safe_join(chunk.meta.headings) + chunk.text
                doc_items = chunk.meta.doc_items

            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0

            # #315 가드레일 분류 후처리: quote 매칭 → guardrail_categories 부착(항상) + 마스킹 치환(옵션)
            content, chunk_cats = gr.apply_to_text(content, _sensitive_infos, _gr_masking)

            vector = (GenOSVectorMetaBuilder()
                      .set_text(content)
                      .set_page_info(chunk_page, chunk_index_on_page, self.page_chunk_counts[chunk_page])
                      .set_chunk_index(chunk_idx)
                      .set_global_metadata(**global_metadata)
                      .set_chunk_bboxes(doc_items, document)
                      .set_media_files(doc_items)
                      .set_table_info(doc_items)
                      .set_guardrail_categories(sorted(chunk_cats) if chunk_cats else None)
                      ).build()
            vectors.append(vector)

            chunk_index_on_page += 1
            if upload_files:
                # 동일 이미지(#56 docx 헤더 그림 등)가 여러 청크에 반복 참조되면 upload_files 가
                # 같은 파일을 중복 업로드·삭제해 FileNotFoundError 로 전체 요청이 실패한다.
                # 경로 기준으로 최초 1회만 업로드하도록 dedupe.
                file_list = []
                for f in self.get_media_files(doc_items):
                    if f['path'] not in scheduled_upload_paths:
                        scheduled_upload_paths.add(f['path'])
                        file_list.append(f)
                if file_list:
                    upload_tasks.append(asyncio.create_task(
                        upload_files(file_list, request=request)
                    ))

        if upload_tasks:
            await asyncio.gather(*upload_tasks)
        return vectors

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        document: DoclingDocument = self.load_documents(file_path, **kwargs)

        artifacts_dir, reference_path = self.get_paths(file_path)
        document = document._with_pictures_refs(image_dir=artifacts_dir, page_no=None, reference_path=reference_path)

        # 민감정보 분류(#315): 청킹 전, 문서 전체를 분류 워크플로우에 1회 호출 → sensitive_infos.
        sensitive_infos: list = []
        if gr.call_enabled(kwargs):
            sensitive_infos = gr.classify_document(
                gr.doc_text(document), self._guardrail_url, self._guardrail_workflow_id,
                self._guardrail_api_key, self._guardrail_timeout,
            )

        chunks = self.split_documents(document, **kwargs)
        if len(chunks) == 0:
            raise GenosServiceException(1, "chunk length is 0")
        return await self.compose_vectors(
            document, chunks, file_path, request, _sensitive_infos=sensitive_infos, _guardrail_masking=(gr.call_enabled(kwargs) and self._guardrail_masking_enabled), **kwargs
        )


class HwpProcessor:
    def __init__(self, tokenizer=None, guardrail_url="", guardrail_workflow_id=None, guardrail_api_key="", guardrail_timeout=30, guardrail_masking_enabled=False):
        # 청킹용 토크나이저 (config 기반; 미지정 시 현행 기본값)
        self._tokenizer = tokenizer if tokenizer is not None else _resolve_tokenizer({})
        # PII 마스킹(#315) 접속 정보 — DocumentProcessor 가 config 에서 읽어 주입.
        self._guardrail_url = guardrail_url
        self._guardrail_workflow_id = guardrail_workflow_id
        self._guardrail_api_key = guardrail_api_key
        self._guardrail_timeout = guardrail_timeout
        self._guardrail_masking_enabled = guardrail_masking_enabled

    def get_paths(self, file_path: str):
        """이미지 등 리소스가 저장될 경로 계산 (기존 로직 유지)"""
        output_path, output_file = os.path.split(file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(f"{output_path}/{filename}")
        reference_path = None if artifacts_dir.is_absolute() else artifacts_dir.parent
        return artifacts_dir, reference_path

    def safe_join(self, iterable):
        """청크 내 헤딩들을 텍스트로 합침"""
        if not isinstance(iterable, (list, tuple, set)):
            return ''
        return ' '.join(map(str, iterable)) + '\n'

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        """SDK 백엔드를 통해 문서를 로드"""
        # 요청마다 독립적인 pipeline_options 생성 (공유 상태 변이 방지) --> save_images, dump_sdk_output
        pipeline_options = PipelineOptions()
        pipeline_options.save_images = kwargs.get('save_images', True)

        use_hwp_sdk = kwargs.get('use_hwp_sdk', True)
        pipeline_options.dump_sdk_output = kwargs.get('dump_sdk_output', False) if use_hwp_sdk else False

        if use_hwp_sdk:
            converter = DocumentConverter(
                format_options={
                    InputFormat.HWP: HwpxFormatOption(
                        pipeline_options=pipeline_options,
                        backend=GenosHwpDocumentBackend
                    ),
                    InputFormat.XML_HWPX: HwpxFormatOption(
                        pipeline_options=pipeline_options,
                        backend=GenosHwpDocumentBackend
                    ),
                }
            )
        else:
            converter = DocumentConverter(
                format_options={
                    InputFormat.HWP: HwpxFormatOption(
                        pipeline_options=pipeline_options,
                        backend=HwpDocumentBackend
                    ),
                    InputFormat.XML_HWPX: HwpxFormatOption(
                        pipeline_options=pipeline_options,
                        backend=HwpxDocumentBackend
                    ),
                }
            )

        conv_result: ConversionResult = converter.convert(Path(file_path).resolve(), raises_on_error=True)
        return conv_result.document

    @staticmethod
    def _hwp_sdk_text_is_empty(document: DoclingDocument) -> bool:
        """GenosHwp SDK 결과 문서에 본문 텍스트가 전혀 없는지 판단(레거시 폴백 트리거용).

        SDK 가 exit 0 으로 "성공"해도 본문을 한 글자도 못 뽑는 경우가 있다(일부 .hwp/.hwpx;
        DRM/암호화 등). 텍스트 run 이 하나도 없으면 True. (convert_processor 와 형평성)
        """
        texts = getattr(document, "texts", None) or []
        return not any((getattr(t, "text", "") or "").strip() for t in texts)

    def split_documents(self, document: DoclingDocument, **kwargs: dict):
        """chunker_type에 따라 HybridChunker 또는 RecursiveCharacterTextSplitter로 분할.

        반환: (chunks, page_chunk_counts). chunks 형식은 chunker_type에 따라 다르다
        (DocChunk 리스트 또는 dict 리스트). compose_vectors가 동일한 chunker_type 분기로 처리한다.
        """
        chunker_type = kwargs.get("chunker_type", "recursive")
        page_chunk_counts: dict[int, int] = defaultdict(int)

        if chunker_type == "recursive":
            recursive_chunk_size = kwargs.get("chunk_size")
            if recursive_chunk_size is None:
                recursive_chunk_size = kwargs.get("recursive_chunk_size")
            recursive_chunk_overlap = kwargs.get("chunk_overlap")
            if recursive_chunk_overlap is None:
                recursive_chunk_overlap = kwargs.get("recursive_chunk_overlap")
            chunks = _split_with_recursive_chunker(
                document,
                chunk_size=recursive_chunk_size,
                chunk_overlap=recursive_chunk_overlap,
                compact_tables=_resolve_compact_tables(kwargs),
            )
            for ch in chunks:
                page_chunk_counts[ch["page_no"]] += 1
            return chunks, page_chunk_counts

        # hybrid
        hybrid_chunk_size = _parse_optional_int(kwargs.get("hybrid_chunk_size"), "hybrid_chunk_size")
        if hybrid_chunk_size is None or hybrid_chunk_size <= 0:
            hybrid_chunk_size = _DEFAULT_HYBRID_MAX_TOKENS
        hybrid_merge_peers = _parse_optional_bool(kwargs.get("hybrid_merge_peers"), "hybrid_merge_peers")
        if hybrid_merge_peers is None:
            hybrid_merge_peers = True
        chunker_kwargs = {
            "max_tokens": hybrid_chunk_size,
            "merge_peers": hybrid_merge_peers,
            "tokenizer": self._tokenizer,
            "tokenizer_type": kwargs.get("hybrid_tokenizer_type", "char"),
        }
        chunker = HybridChunker(**chunker_kwargs)
        chunks: List[DocChunk] = list(chunker.chunk(dl_doc=document, **kwargs))
        for chunk in chunks:
            if chunk.meta.doc_items[0].prov:
                page_chunk_counts[chunk.meta.doc_items[0].prov[0].page_no] += 1
        return chunks, page_chunk_counts

    async def compose_vectors(self, document: DoclingDocument, chunks, page_chunk_counts: dict[int, int],
                              request: Any, **kwargs: dict) -> list[dict]:
        """빌더를 사용하여 최종 GenOSVectorMeta 리스트 생성"""
        chunker_type = kwargs.get("chunker_type", "recursive")
        _sensitive_infos: list = kwargs.get("_sensitive_infos") or []      # #315 분류 결과
        _gr_masking: bool = bool(kwargs.get("_guardrail_masking", False))   # #315 마스킹 치환 on/off

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
        )

        current_page = None
        chunk_index_on_page = 0
        vectors = []
        upload_tasks = []

        for chunk_idx, chunk in enumerate(chunks):
            if chunker_type == "recursive":
                chunk_page = chunk["page_no"]
                content = chunk["text"]
                doc_items = chunk["doc_items"]
            else:
                chunk_page = chunk.meta.doc_items[0].prov[0].page_no if chunk.meta.doc_items[0].prov else 0
                content = self.safe_join(chunk.meta.headings) + chunk.text
                doc_items = chunk.meta.doc_items

            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0

            # #315 가드레일 분류 후처리: quote 매칭 → guardrail_categories 부착(항상) + 마스킹 치환(옵션)
            content, chunk_cats = gr.apply_to_text(content, _sensitive_infos, _gr_masking)

            builder = GenOSVectorMetaBuilder()
            vector_obj = (builder
                      .set_text(content)
                      .set_page_info(chunk_page, chunk_index_on_page, page_chunk_counts[chunk_page])
                      .set_chunk_index(chunk_idx)
                      .set_global_metadata(**global_metadata)
                      .set_chunk_bboxes(doc_items, document)
                      .set_media_files(doc_items)
                      .set_table_info(doc_items)
                      .set_guardrail_categories(sorted(chunk_cats) if chunk_cats else None)
                      ).build()
            vectors.append(vector_obj)
            chunk_index_on_page += 1

        if upload_tasks:
            await asyncio.gather(*upload_tasks)

        return vectors

    async def __call__(self, request: Any, file_path: str, **kwargs: dict):
        """외부에서 호출되는 통합 프로세서 입구"""
        ext = os.path.splitext(file_path)[-1].lower()

        # 1. SDK 백엔드로 문서 변환 (실패 시 폴백)
        document: DoclingDocument = None
        try:
            document = self.load_documents(file_path, **kwargs)
        except Exception as sdk_err:
            _log.warning(f"[HwpProcessor] GenosHwp SDK 변환 실패: {sdk_err}")
            if ext in ('.hwp', '.hwpx'):
                # GenosHwp SDK 실패 시 레거시 백엔드로 폴백 (.hwp → HwpDocumentBackend, .hwpx → HwpxDocumentBackend)
                backend_name = "HwpDocumentBackend" if ext == '.hwp' else "HwpxDocumentBackend"
                try:
                    _log.info(f"[HwpProcessor] {backend_name}로 폴백 시도: {file_path}")
                    kwargs_fallback = dict(kwargs, use_hwp_sdk=False)
                    document = self.load_documents(file_path, **kwargs_fallback)
                    _log.info(f"[HwpProcessor] {backend_name} 폴백 성공")
                except Exception as fallback_err:
                    _log.warning(f"[HwpProcessor] {backend_name} 폴백도 실패: {fallback_err}")
                    raise sdk_err
            else:
                raise

        # 1-b. SDK 가 예외 없이(exit 0) 끝났어도 본문 텍스트가 비어 있으면(빈 doc_items 로
        #      다운스트림이 깨지거나 무의미한 표 청크만 나오는 경우) 레거시 백엔드로 폴백한다.
        #      그래도 본문을 못 얻으면 예외로 올려 DocumentProcessor.__call__ 의 PDF 변환 폴백에
        #      위임한다. (convert_processor 와 형평성 — convert 는 GenosSmartChunker 예외로 잡히지만
        #      attachment 는 recursive splitter 라 예외가 안 나므로 여기서 명시적으로 처리한다.)
        # .hml(HWPML)은 GenosHwp SDK 전용 포맷 — 레거시 백엔드가 없어 빈 결과면 바로
        # 상위(DocumentProcessor.__call__)의 PDF 변환 폴백으로 위임한다 (이슈 #323).
        if ext == '.hml' and self._hwp_sdk_text_is_empty(document):
            raise HwpConversionError(
                f"HML SDK 결과가 비어 있음(hml 은 레거시 백엔드 없음): {file_path}"
            )
        if ext in ('.hwp', '.hwpx') and self._hwp_sdk_text_is_empty(document):
            backend_name = "HwpDocumentBackend" if ext == '.hwp' else "HwpxDocumentBackend"
            _log.warning(f"[HwpProcessor] GenosHwp SDK 결과에 본문 텍스트가 없어 {backend_name} 폴백 시도: {file_path}")
            fallback_doc = None
            try:
                fallback_doc = self.load_documents(file_path, **dict(kwargs, use_hwp_sdk=False))
            except Exception as fallback_err:
                _log.warning(f"[HwpProcessor] {backend_name} 폴백 실패, 상위 PDF 폴백으로 위임: {fallback_err}")
            if fallback_doc is not None and not self._hwp_sdk_text_is_empty(fallback_doc):
                _log.info(f"[HwpProcessor] {backend_name} 폴백 성공(본문 텍스트 확보)")
                document = fallback_doc
            else:
                _log.info(f"[HwpProcessor] {backend_name} 폴백으로도 본문 복구 실패, 상위 PDF 폴백으로 위임")
                raise HwpConversionError(
                    f"HWP/HWPX SDK 결과가 비어 있고 레거시 백엔드로도 본문 복구 실패: {file_path}"
                )

        # 2. 이미지 참조 경로 설정
        artifacts_dir, reference_path = self.get_paths(file_path)
        document = document._with_pictures_refs(
            image_dir=artifacts_dir,
            page_no=None,
            reference_path=reference_path
        )

        # 민감정보 분류(#315): 청킹 전, 문서 전체를 분류 워크플로우에 1회 호출 → sensitive_infos.
        sensitive_infos: list = []
        if gr.call_enabled(kwargs):
            sensitive_infos = gr.classify_document(
                gr.doc_text(document), self._guardrail_url, self._guardrail_workflow_id,
                self._guardrail_api_key, self._guardrail_timeout,
            )

        # 3. 청킹 + 4. 벡터화
        chunks, page_chunk_counts = self.split_documents(document, **kwargs)
        if len(chunks) == 0:
            raise GenosServiceException(1, "chunk length is 0")
        return await self.compose_vectors(
            document, chunks, page_chunk_counts, request, _sensitive_infos=sensitive_infos, _guardrail_masking=(gr.call_enabled(kwargs) and self._guardrail_masking_enabled), **kwargs
        )

class GenosServiceException(Exception):
    """GenOS 와의 의존성 부분 제거를 위해 추가"""

    def __init__(self, error_code: str, error_msg: Optional[str] = None, msg_params: Optional[dict] = None) -> None:
        self.code = 1
        self.error_code = error_code
        self.error_msg = error_msg or "GenOS Service Exception"
        self.msg_params = msg_params or {}

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        return f"{class_name}(code={self.code!r}, errMsg={self.error_msg!r})"


class DocumentProcessor:
    def __init__(self, config_path: str | None = None):
        if config_path is None:
            config_path = _resolve_default_attachment_config_path()
        cfg = _load_config(config_path)
        self._config_dir = Path(config_path).resolve().parent

        defaults_cfg = _as_dict(cfg.get("defaults"))
        chunking_cfg = _as_dict(cfg.get("chunking"))
        generic_chunk_cfg = _as_dict(chunking_cfg.get("generic"))
        recursive_chunk_cfg = _as_dict(chunking_cfg.get("recursive"))
        hybrid_chunk_cfg = _as_dict(chunking_cfg.get("hybrid"))
        loaders_cfg = _as_dict(cfg.get("loaders"))
        image_loader_cfg = _as_dict(loaders_cfg.get("image"))
        tabular_loader_cfg = _as_dict(loaders_cfg.get("tabular"))
        whisper_cfg = _as_dict(cfg.get("whisper"))

        # PPT 페이지 단위 설명(page-level image description) 설정.
        # config 위치: formats.ppt.page_description. 공통 모듈(enrichment/page_description)로 파싱.
        formats_cfg = _as_dict(cfg.get("formats"))
        ppt_fmt_cfg = _as_dict(formats_cfg.get("ppt"))
        hwp_fmt_cfg = _as_dict(formats_cfg.get("hwp"))
        ppt_pd_cfg = _as_dict(ppt_fmt_cfg.get("page_description"))
        self._page_desc_options = PageDescriptionOptions.from_config(ppt_pd_cfg, self._config_dir)

        # 청킹용 토크나이저 (chunking config 기반; 미지정 시 현행 기본값)
        self._tokenizer = _resolve_tokenizer(chunking_cfg)

        # 청킹 모드는 chunking.chunker_type 에서 읽는다(구버전 호환: 없으면 defaults.chunker_type).
        chunker_type = str(
            chunking_cfg.get("chunker_type", defaults_cfg.get("chunker_type", "recursive"))
        ).strip().lower()
        if chunker_type not in {"recursive", "hybrid"}:
            _log.warning(
                f"[DocumentProcessor] Unknown defaults.chunker_type '{chunker_type}', fallback to 'recursive'."
            )
            chunker_type = "recursive"

        # markdown 표 compact(컬럼 정렬 패딩 제거) 여부. 기본 True.
        output_cfg = _as_dict(cfg.get("output"))
        compact_tables = _parse_optional_bool(
            output_cfg.get("compact_tables"), "output.compact_tables"
        )
        if compact_tables is None:
            compact_tables = True

        use_pdf_sdk = _parse_optional_bool(defaults_cfg.get("use_pdf_sdk"), "defaults.use_pdf_sdk")

        # HWP/HWPX 전용 옵션은 formats.hwp 에서 읽는다(구버전 호환: 없으면 defaults 폴백).
        use_hwp_sdk = _parse_optional_bool(hwp_fmt_cfg.get("use_hwp_sdk"), "formats.hwp.use_hwp_sdk")
        if use_hwp_sdk is None:
            use_hwp_sdk = _parse_optional_bool(defaults_cfg.get("use_hwp_sdk"), "defaults.use_hwp_sdk")
        dump_sdk_output = _parse_optional_bool(
            hwp_fmt_cfg.get("dump_sdk_output"), "formats.hwp.dump_sdk_output"
        )
        if dump_sdk_output is None:
            dump_sdk_output = _parse_optional_bool(
                defaults_cfg.get("dump_sdk_output"), "defaults.dump_sdk_output"
            )
        save_images = _parse_optional_bool(hwp_fmt_cfg.get("save_images"), "formats.hwp.save_images")
        if save_images is None:
            save_images = _parse_optional_bool(defaults_cfg.get("save_images"), "defaults.save_images")

        log_level = _parse_optional_int(defaults_cfg.get("log_level"), "defaults.log_level")
        if log_level is None:
            log_level = 4

        # 청크 크기 공통 옵션(chunking.chunk_size). recursive/hybrid 는 chunker_type 으로
        # 택일되므로 값 하나를 활성 모드가 자기 단위(recursive=문자 수 · hybrid=토큰 수)로 해석한다.
        common_chunk_size = _parse_optional_int(chunking_cfg.get("chunk_size"), "chunking.chunk_size")

        # 문자수 기반 통합 청킹 설정. 우선순위: recursive.chunk_size > chunking.chunk_size(공통)
        # > (레거시)chunking.generic.chunk_size > 0.
        recursive_chunk_size = _parse_optional_int(
            recursive_chunk_cfg.get("chunk_size"), "chunking.recursive.chunk_size"
        )
        if recursive_chunk_size is None:
            recursive_chunk_size = common_chunk_size
        if recursive_chunk_size is None:
            recursive_chunk_size = _parse_optional_int(
                generic_chunk_cfg.get("chunk_size"), "chunking.generic.chunk_size"
            )
        if recursive_chunk_size is None or recursive_chunk_size < 0:
            recursive_chunk_size = 0  # 0 = 전체 문서를 1청크로 (문자수 분할 안 함)
        recursive_chunk_overlap = _parse_optional_int(
            recursive_chunk_cfg.get("chunk_overlap", generic_chunk_cfg.get("chunk_overlap")),
            "chunking.recursive.chunk_overlap",
        )
        if recursive_chunk_overlap is None or recursive_chunk_overlap < 0:
            recursive_chunk_overlap = 100

        # hybrid(토큰 수). 우선순위: hybrid.chunk_size > chunking.chunk_size(공통) > 무제한 기본값.
        hybrid_chunk_size = _parse_optional_int(
            hybrid_chunk_cfg.get("chunk_size"), "chunking.hybrid.chunk_size"
        )
        if hybrid_chunk_size is None:
            hybrid_chunk_size = common_chunk_size
        if hybrid_chunk_size is None or hybrid_chunk_size <= 0:
            hybrid_chunk_size = _DEFAULT_HYBRID_MAX_TOKENS
        hybrid_merge_peers = _parse_optional_bool(
            hybrid_chunk_cfg.get("merge_peers"), "chunking.hybrid.merge_peers"
        )
        if hybrid_merge_peers is None:
            hybrid_merge_peers = True
        hybrid_tokenizer_type = str(hybrid_chunk_cfg.get("tokenizer_type", "char")).strip().lower()
        if hybrid_tokenizer_type not in {"char", "huggingface"}:
            _log.warning(
                f"[DocumentProcessor] Unknown chunking.hybrid.tokenizer_type '{hybrid_tokenizer_type}', fallback to 'char'."
            )
            hybrid_tokenizer_type = "char"

        image_ocr_languages = image_loader_cfg.get("ocr_languages", ["kor", "eng"])
        if isinstance(image_ocr_languages, (list, tuple, set)):
            image_ocr_languages = [str(v).strip() for v in image_ocr_languages if str(v).strip()]
        else:
            image_ocr_languages = ["kor", "eng"]
        if not image_ocr_languages:
            image_ocr_languages = ["kor", "eng"]

        tabular_sample_bytes = _parse_optional_int(
            tabular_loader_cfg.get("encoding_detect_sample_bytes"),
            "loaders.tabular.encoding_detect_sample_bytes",
        )
        if tabular_sample_bytes is None or tabular_sample_bytes <= 0:
            tabular_sample_bytes = 10000

        whisper_chunk_sec = _parse_optional_int(whisper_cfg.get("chunk_sec"), "whisper.chunk_sec")
        if whisper_chunk_sec is None or whisper_chunk_sec <= 0:
            whisper_chunk_sec = 29
        whisper_chunk_overlap_ms = _parse_optional_int(
            whisper_cfg.get("chunk_overlap_ms"), "whisper.chunk_overlap_ms"
        )
        if whisper_chunk_overlap_ms is None or whisper_chunk_overlap_ms < 0:
            whisper_chunk_overlap_ms = 300
        whisper_tmp_dir_prefix = str(
            whisper_cfg.get("tmp_dir_prefix", "./tmp_audios_")
        ).strip() or "./tmp_audios_"

        self._default_kwargs = {
            "log_level": log_level,
            "chunker_type": chunker_type,
            "compact_tables": compact_tables,
            "use_pdf_sdk": True if use_pdf_sdk is None else use_pdf_sdk,
            "use_hwp_sdk": True if use_hwp_sdk is None else use_hwp_sdk,
            "dump_sdk_output": False if dump_sdk_output is None else dump_sdk_output,
            "save_images": True if save_images is None else save_images,
            "recursive_chunk_size": recursive_chunk_size,
            "recursive_chunk_overlap": recursive_chunk_overlap,
            "hybrid_chunk_size": hybrid_chunk_size,
            "hybrid_merge_peers": hybrid_merge_peers,
            "hybrid_tokenizer_type": hybrid_tokenizer_type,
            "image_ocr_languages": image_ocr_languages,
            "tabular_encoding_detect_sample_bytes": tabular_sample_bytes,
            # 사내 주소를 코드 기본값으로 두지 않는다(배포본에 그대로 나간다).
            # 음성 전사를 쓰는 사이트는 yaml 의 whisper.url 을 반드시 지정해야 한다.
            "whisper_url": str(whisper_cfg.get("url", "")).strip(),
            "whisper_model": str(whisper_cfg.get("model", "model")).strip() or "model",
            "whisper_language": str(whisper_cfg.get("language", "ko")).strip() or "ko",
            "whisper_response_format": str(
                whisper_cfg.get("response_format", "json")
            ).strip() or "json",
            "whisper_temperature": str(whisper_cfg.get("temperature", "0")).strip() or "0",
            "whisper_stream": str(whisper_cfg.get("stream", "false")).strip() or "false",
            "whisper_timestamp_granularities": str(
                whisper_cfg.get("timestamp_granularities", "word")
            ).strip() or "word",
            "whisper_chunk_sec": whisper_chunk_sec,
            "whisper_chunk_overlap_ms": whisper_chunk_overlap_ms,
            "whisper_tmp_dir_prefix": whisper_tmp_dir_prefix,
        }

        # 민감정보 분류(#315): GenOS 분류 워크플로우 접속 정보(환경 종속값). on/off 는 요청별 kwargs.
        gm_cfg = _as_dict(cfg.get("guardrail"))
        self._guardrail_url = str(gm_cfg.get("url") or "").strip()
        self._guardrail_workflow_id = _parse_optional_int(gm_cfg.get("workflow_id"), "guardrail.workflow_id")
        self._guardrail_api_key = str(gm_cfg.get("api_key") or "").strip()
        gm_timeout = _parse_optional_int(gm_cfg.get("timeout"), "guardrail.timeout")
        self._guardrail_timeout = gm_timeout if gm_timeout and gm_timeout > 0 else 60
        self._guardrail_masking_enabled = bool(_parse_optional_bool(gm_cfg.get("masking_enabled"), "guardrail.masking_enabled"))

        self.page_chunk_counts = defaultdict(int)
        _gm = dict(
            guardrail_url=self._guardrail_url,
            guardrail_workflow_id=self._guardrail_workflow_id,
            guardrail_api_key=self._guardrail_api_key,
            guardrail_timeout=self._guardrail_timeout,
            guardrail_masking_enabled=self._guardrail_masking_enabled,
        )
        self.hwp_processor = HwpProcessor(tokenizer=self._tokenizer, **_gm)
        self.docx_processor = DocxProcessor(tokenizer=self._tokenizer, **_gm)

    def _merge_runtime_kwargs(self, kwargs: dict) -> dict:
        merged = dict(self._default_kwargs)
        for k, v in kwargs.items():
            if v is not None:
                merged[k] = v
        return merged

    @staticmethod
    def _render_pdf_page_images(pdf_path: str, scale: float) -> "dict[int, Image.Image]":
        """PDF 각 페이지를 PIL 이미지로 렌더한다(page_description 전용).

        scale 은 docling images_scale 과 같은 의미(1.0 = 72 DPI 기준 배율)다.
        반환: {page_no(1-based): PIL Image}
        """
        images: "dict[int, Image.Image]" = {}
        matrix = fitz.Matrix(scale, scale)
        with fitz.open(pdf_path) as pdf:
            for idx, page in enumerate(pdf):
                pixmap = page.get_pixmap(matrix=matrix)
                images[idx + 1] = Image.frombytes(
                    "RGB", (pixmap.width, pixmap.height), pixmap.samples
                )
        return images

    def _load_ppt_page_documents(self, file_path: str, **kwargs: dict) -> "Optional[list[Document]]":
        """PPT/PPTX → PDF 변환 후 PyMuPDF 페이지 파싱 + 페이지 단위 image description.

        페이지별 Document(metadata['page']=0-based) 리스트를 반환한다. PDF 변환이 불가하면
        None 을 반환해 호출부가 레거시 langchain 경로로 폴백하도록 한다.

        파싱은 .pdf 첨부와 동일한 PyMuPDFLoader 를 쓴다. docling StandardPdfPipeline 은
        레이아웃 모델을 무조건 태우면서(끌 수 없음) 정작 첨부가 쓰는 건 페이지 텍스트뿐이었고,
        레이아웃 클러스터에 안 잡힌 텍스트(표지 문구·차트 라벨·리스트 번호 등)를 누락시켰다.
        """
        pdf_path = convert_to_pdf(file_path, use_pdf_sdk=kwargs.get('use_pdf_sdk', True))
        if not pdf_path or not os.path.exists(pdf_path):
            candidate = _get_pdf_path(file_path)
            pdf_path = candidate if os.path.exists(candidate) else None
        if not pdf_path:
            _log.warning(f"[ppt] PDF 변환 실패 — 레거시 경로로 폴백: {os.path.basename(file_path)}")
            return None

        # 페이지별 네이티브 텍스트 수집(page_no 는 1-based 로 정규화)
        page_documents = PyMuPDFLoader(pdf_path, mode="page").load()
        page_texts: dict[int, str] = {}
        for page_document in page_documents:
            text = str(page_document.page_content or "").strip()
            if text:
                page_texts[int(page_document.metadata.get('page', 0)) + 1] = text

        # 페이지 단위 image description(옵션). enable=false 이거나 url 미설정이면 렌더/요청을 모두
        # skip 한다(파싱은 유지). 렌더가 describe_page_images 의 인자라 먼저 평가되므로,
        # 설정 판정을 호출 전에 해야 헛렌더를 막을 수 있다.
        # native text 가 있으면 프롬프트({{page_text}})에 반영해 요청한다.
        page_descs: dict[int, str] = {}
        if should_describe(self._page_desc_options):
            page_descs = describe_page_images(
                self._render_pdf_page_images(pdf_path, self._page_desc_options.images_scale),
                self._page_desc_options,
                page_texts=page_texts,
            )

        all_pages: set[int] = set(range(1, len(page_documents) + 1))
        all_pages |= set(page_texts.keys()) | set(page_descs.keys())
        if not all_pages:
            all_pages = {1}

        # 같은 페이지의 native text 와 설명을 동일 청크(=동일 Document)로 병합한다.
        documents: list[Document] = []
        for page_no in sorted(all_pages):
            native = page_texts.get(page_no, "").strip()
            desc = page_descs.get(page_no, "").strip()
            if native and desc:
                content = f"{native}\n\n[페이지 이미지 설명]\n{desc}"
            elif desc:
                content = desc
            else:
                content = native
            if not content:
                # 빈 페이지(텍스트/설명 모두 없음) → '.' 폴백으로 Empty document 예외 방지
                content = "."
            documents.append(
                Document(
                    page_content=content,
                    metadata={'source': file_path, 'page': page_no - 1},
                )
            )

        _log.info(
            f"[ppt] page documents 생성: pages={len(documents)}, "
            f"described={len(page_descs)}, description_enabled={self._page_desc_options.enabled}"
        )
        return documents

    def _chunk_ppt_pages(self, documents: "list[Document]", **kwargs: dict) -> "list[Document]":
        """PPT 페이지 Document 를 청크로 구성한다.

        기본: 1 page = 1 chunk. chunk_size(kwargs, 명시된 경우만) 가 주어지면 연속 페이지를
        합친 길이가 chunk_size 이하가 되도록 greedy 병합한다. 병합 청크는 metadata['page']=시작,
        metadata['end_page']=끝(0-based) 을 갖는다.
        """
        self.page_chunk_counts = defaultdict(int)
        if not documents:
            raise Exception('Empty document')

        # 모든 페이지에 추출 가능한 텍스트/설명이 없는 경우(이미지 기반 PPT 등): 페이지별 sentinel('.')
        # 을 이어붙이지 않고, 페이지 전 범위를 span 하는 단일 빈 텍스트('') 청크로 반환한다.
        if all(doc.page_content.strip() in ("", ".") for doc in documents):
            last_page = documents[-1].metadata.get('page', 0)
            self.page_chunk_counts[0] += 1
            return [Document(
                page_content="",
                metadata={
                    'source': documents[0].metadata.get('source'),
                    'page': 0,
                    'end_page': last_page,
                },
            )]

        # chunk_size 우선순위: kwargs['chunk_size'] > chunking.recursive.chunk_size(recursive_chunk_size).
        # 값이 없거나 <=0 이면 1 page = 1 chunk, 있으면 연속 페이지를 그 길이까지 결합.
        chunk_size = _parse_optional_int(kwargs.get('chunk_size'), 'chunk_size')
        if chunk_size is None:
            chunk_size = _parse_optional_int(kwargs.get('recursive_chunk_size'), 'recursive_chunk_size')

        chunks: list[Document] = []
        if chunk_size is None or chunk_size <= 0:
            # 1 page = 1 chunk
            for doc in documents:
                page = doc.metadata.get('page', 0)
                chunks.append(Document(
                    page_content=doc.page_content,
                    metadata={**doc.metadata, 'end_page': page},
                ))
        else:
            # 연속 페이지 greedy 병합
            cur_parts: list[str] = []
            cur_start: Optional[int] = None
            cur_end: Optional[int] = None
            cur_source = documents[0].metadata.get('source')

            def _flush():
                if cur_parts:
                    chunks.append(Document(
                        page_content="\n\n".join(cur_parts),
                        metadata={'source': cur_source, 'page': cur_start, 'end_page': cur_end},
                    ))

            for doc in documents:
                page = doc.metadata.get('page', 0)
                text = doc.page_content
                if cur_parts and len("\n\n".join(cur_parts + [text])) > chunk_size:
                    _flush()
                    cur_parts = [text]
                    cur_start = page
                    cur_end = page
                else:
                    cur_parts.append(text)
                    if cur_start is None:
                        cur_start = page
                    cur_end = page
            _flush()

        chunks = [c for c in chunks if c.page_content]
        if not chunks:
            raise Exception('Empty document')
        for chunk in chunks:
            self.page_chunk_counts[chunk.metadata.get('page', 0)] += 1
        return chunks

    def get_loader(
        self,
        file_path: str,
        use_pdf_sdk: bool = True,
        image_ocr_languages: Optional[list[str]] = None,
    ):
        ext = os.path.splitext(file_path)[-1].lower()
        real_type = self.get_real_file_type(file_path)

        # 확장자와 실제 파일 타입이 다를 때만 real_type 사용
        if ext != real_type and real_type == 'pdf':
            return PyMuPDFLoader(file_path)
        elif ext != real_type and real_type in ['txt', 'json', 'md']:
            return TextLoader(file_path)
        # 원래 확장자 기반 로직
        elif ext == '.pdf':
            return PyMuPDFLoader(file_path)
        elif ext == '.doc':
            convert_to_pdf(file_path, use_pdf_sdk=use_pdf_sdk)
            return UnstructuredWordDocumentLoader(file_path)
        elif ext in ['.ppt', '.pptx']:
            convert_to_pdf(file_path, use_pdf_sdk=use_pdf_sdk)
            return UnstructuredPowerPointLoader(file_path)
        elif ext in ['.jpg', '.jpeg', '.png']:
            convert_to_pdf(file_path, use_pdf_sdk=use_pdf_sdk)
            languages = image_ocr_languages or ["kor", "eng"]
            if not isinstance(languages, list):
                languages = [str(languages)]
            languages = [str(lang).strip() for lang in languages if str(lang).strip()]
            if not languages:
                languages = ["kor", "eng"]
            # 한국어 OCR 지원을 위한 언어 설정
            return UnstructuredImageLoader(
                file_path,
                languages=languages,  # 한국어 + 영어 OCR
            )
        elif ext in ['.txt', '.json', '.md']:
            return TextLoader(file_path)
        elif ext == '.md':
            return UnstructuredMarkdownLoader(file_path)
        else:
            return UnstructuredFileLoader(file_path)

    def get_real_file_type(self, file_path: str) -> str:
        """파일 확장자가 아닌 실제 내용으로 파일 타입 판단"""
        with open(file_path, 'rb') as f:
            header = f.read(8)
        if header.startswith(b'%PDF-'):
            return 'pdf'
        elif header.startswith(b'\x89PNG'):
            return 'png'
        elif header.startswith(b'\xff\xd8\xff'):
            return 'jpg'

        # 매직 헤더로 판단할 수 없으면 확장자 사용
        return os.path.splitext(file_path)[-1].lower()

    def convert_md_to_pdf(self, md_path):
        """Markdown 파일을 PDF로 변환"""
        install_packages(['chardet'])
        import chardet

        pdf_path = md_path.replace('.md', '.pdf')
        with open(md_path, 'rb') as f:
            raw_file = f.read()
        candidates = ['utf-8', 'utf-8-sig']
        try:
            det = (chardet.detect(raw_file) or {}).get('encoding') or ''
            # chardet가 ascii/unknown이면 무시. 그 외면 후보에 추가
            if det and det.lower() not in ('ascii', 'unknown'):
                if det.lower() not in [c.lower() for c in candidates]:
                    candidates.append(det)
        except Exception:
            pass
        candidates += ['cp949', 'euc-kr', 'iso-8859-1', 'latin-1']
        md_content = None
        for enc in candidates:
            try:
                md_content = raw_file.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if md_content is None:
            md_content = raw_file.decode('utf-8', errors='replace')

        html_content = markdown(md_content)
        if HTML:
            HTML(string=html_content).write_pdf(pdf_path)
        return pdf_path

    def load_documents(self, file_path: str, **kwargs: dict) -> list[Document]:
        loader = self.get_loader(
            file_path,
            use_pdf_sdk=kwargs.get('use_pdf_sdk', True),
            image_ocr_languages=kwargs.get("image_ocr_languages"),
        )
        documents = loader.load()

        # 이미지 파일의 경우 텍스트 추출 안되었을 시 기본 텍스트 제공
        ext = os.path.splitext(file_path)[-1].lower()
        if ext in ['.jpg', '.jpeg', '.png']:
            # documents가 없거나, 있어도 모든 page_content가 비어있는 경우
            if not documents or not any(doc.page_content.strip() for doc in documents):
                documents = [Document(page_content=".", metadata={'source': file_path, 'page': 0})]

        return documents

    def split_documents(self, documents, **kwargs: dict) -> list[Document]:
        # 문자수 기반 통합 청킹 (chunking.recursive 설정 공유). chunk_size<=0 이면 문서당 1청크.
        chunk_size = kwargs.get('chunk_size')
        if chunk_size is None:
            chunk_size = kwargs.get('recursive_chunk_size', 0)
        chunk_overlap = kwargs.get('chunk_overlap')
        if chunk_overlap is None:
            chunk_overlap = kwargs.get('recursive_chunk_overlap', 100)

        chunks = [
            Document(page_content=part, metadata=dict(doc.metadata))
            for doc in documents
            for part in _char_split_text(
                doc.page_content,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        ]
        chunks = [chunk for chunk in chunks if chunk.page_content]
        if not chunks:
            raise Exception('Empty document')

        for chunk in chunks:
            page = chunk.metadata.get('page', 0)
            self.page_chunk_counts[page] += 1
        return chunks

    def compose_vectors(self, file_path: str, chunks: list[Document], **kwargs: dict) -> list[dict]:
        _sensitive_infos: list = kwargs.get("_sensitive_infos") or []      # #315 분류 결과
        _gr_masking: bool = bool(kwargs.get("_guardrail_masking", False))   # #315 마스킹 치환 on/off
        ext = os.path.splitext(file_path)[-1].lower()
        real_type = self.get_real_file_type(file_path)

        # 확장자와 실제 파일 타입이 다를 때만 real_type 사용
        if ext != real_type and real_type == 'pdf':
            pdf_path = file_path
        elif ext != real_type and real_type in ['txt', 'json', 'md']:
            pdf_path = _get_pdf_path(file_path)
        # 원래 확장자 기반 로직
        elif file_path.endswith('.md'):
            pdf_path = self.convert_md_to_pdf(file_path)
        elif file_path.endswith(('.ppt', '.pptx')):
            pdf_path = _get_pdf_path(file_path)
        else:
            pdf_path = _get_pdf_path(file_path)

        # doc = fitz.open(pdf_path) if (pdf_path and os.path.exists(pdf_path)) else None

        if file_path.endswith(('.ppt', '.pptx')):
            if os.path.exists(pdf_path):
                subprocess.run(["rm", pdf_path], check=True)

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=max([chunk.metadata.get('page', 0) for chunk in chunks]),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z'
        )
        current_page = None
        chunk_index_on_page = 0

        vectors = []
        for chunk_idx, chunk in enumerate(chunks):
            page = chunk.metadata.get('page', 1)
            # PPT 페이지 결합 청크는 end_page 로 페이지 범위를 표현(미설정 시 단일 페이지).
            end_page = chunk.metadata.get('end_page', page)
            if ext not in ['.hwpx', '.docx']:
                page += 1
                end_page += 1
            text = chunk.page_content
            # #315 가드레일 분류 후처리: quote 매칭 → guardrail_categories 부착(항상) + 마스킹 치환(옵션)
            text, chunk_cats = gr.apply_to_text(text, _sensitive_infos, _gr_masking)

            if page != current_page:
                current_page = page
                chunk_index_on_page = 0

            # 첨부용에서는 bbox 정보 추출 X
            # if doc:
            #     fitz_page = doc.load_page(page)
            #     global_metadata['chunk_bboxes'] = json.dumps(merge_overlapping_bboxes([{
            #         'page': page + 1,
            #         'type': 'text',
            #         'bbox': {
            #             'l': rect[0] / fitz_page.rect.width,
            #             't': rect[1] / fitz_page.rect.height,
            #             'r': rect[2] / fitz_page.rect.width,
            #             'b': rect[3] / fitz_page.rect.height,
            #         }
            #     } for rect in fitz_page.search_for(text)], x_tolerance=1 / fitz_page.rect.width,
            #         y_tolerance=1 / fitz_page.rect.height))

            vectors.append(GenOSVectorMeta.model_validate({
                'text': text,
                'n_char': len(text),
                'n_word': len(text.split()),
                'n_line': len(text.splitlines()),
                'i_page': page,
                'e_page': end_page,
                'i_chunk_on_page': chunk_index_on_page,
                'n_chunk_of_page': self.page_chunk_counts[page],
                'i_chunk_on_doc': chunk_idx,
                'guardrail_categories': sorted(chunk_cats) if chunk_cats else None,  # #315 민감정보 분류 라벨
                **global_metadata
            }))
            chunk_index_on_page += 1

        return vectors

    def setup_logging(self, level_num: int):
        # 첨부 프로세서만 stdout 대신 로거로 알린다(기존 동작 유지).
        rt.setup_logging(level_num, announce=_log.info)

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        kwargs = self._merge_runtime_kwargs(kwargs)
        self.setup_logging(kwargs.get('log_level', 4))

        _log.info(f"file_path: {file_path}")
        _log.info(f"kwargs: {kwargs}")

        # 비정상/암호화 파일 사전 감지(이슈 #278/#307): 지원 포맷 매직헤더에 하나도 안 맞고
        # 텍스트도 아니면(=DRM 암호화/손상 바이너리) 파싱/변환 단계의 garbage 처리를 유발하므로
        # 진입부에서 컷한다. 확장자와 무관하게 실제 헤더로 판정.
        bad_reason = _detect_unsupported_file(file_path)
        if bad_reason:
            _log.warning(f"[attachment] 비정상 파일 감지({bad_reason}) — 처리 중단: {file_path}")
            raise GenosServiceException(
                "1", f"{bad_reason} 입니다. 정상 문서로 다시 업로드하세요: {os.path.basename(file_path)}"
            )

        ext = os.path.splitext(file_path)[-1].lower()
        if ext in ('.wav', '.mp3', '.m4a'):
            # TODO(#315): PII 마스킹 미적용(보류) — AudioLoader 는 자체 vector 포맷이라 별도 논의 후 적용.
            # Generate a temporal path saving audio chunks: the audio file is supposed to be splited to several chunks due to limitted length by the model
            file_stem = os.path.basename(file_path).split('.')[0]
            tmp_prefix = str(kwargs.get("whisper_tmp_dir_prefix", "./tmp_audios_"))
            if tmp_prefix.endswith("/"):
                tmp_path = os.path.join(tmp_prefix, file_stem)
            else:
                tmp_path = f"{tmp_prefix}{file_stem}"
            if not os.path.exists(tmp_path):
                os.makedirs(tmp_path)

            # Use 'Whisper' model served in-house
            # [!] Modify the request parameters to change a STT model to be used
            loader = AudioLoader(
                file_path=file_path,
                req_url=str(kwargs.get("whisper_url", "")),
                req_data={
                    'model': str(kwargs.get("whisper_model", "model")),
                    'language': str(kwargs.get("whisper_language", "ko")),
                    'response_format': str(kwargs.get("whisper_response_format", "json")),
                    'temperature': str(kwargs.get("whisper_temperature", "0")),
                    'stream': str(kwargs.get("whisper_stream", "false")),
                    'timestamp_granularities[]': str(
                        kwargs.get("whisper_timestamp_granularities", "word")
                    ),
                },
                chunk_sec=int(kwargs.get("whisper_chunk_sec", 29)),
                chunk_overlap_ms=int(kwargs.get("whisper_chunk_overlap_ms", 300)),
                tmp_path=tmp_path
            )
            vectors = loader.return_vectormeta_format()

            # Remove the temporal chunks
            try:
                subprocess.run(['rm', '-r', tmp_path], check=True)
            except:
                pass
            return vectors

        elif ext in ('.csv', '.xlsx'):
            # TODO(#315): PII 마스킹 미적용(보류) — TabularLoader 는 자체 vector 포맷이라 별도 논의 후 적용.
            loader = TabularLoader(
                file_path,
                ext,
                encoding_detect_sample_bytes=int(
                    kwargs.get("tabular_encoding_detect_sample_bytes", 10000)
                ),
            )
            vectors = loader.return_vectormeta_format()
            return vectors

        # [핵심 수정] HWP와 HWPX를 하나의 프로세서로 통합 실행
        # .hml(HWPML)은 hwp_sdk 260713+ 에서 지원되어 같은 프로세서로 라우팅 (이슈 #323)
        elif ext in ('.hwp', '.hwpx', '.hml'):
            _log.info(f"Processing Korean Document ({ext}) with Unified HwpProcessor")
            try:
                return await self.hwp_processor(request, file_path, **kwargs)
            except Exception as hwp_err:
                # 모든 docling 백엔드 실패 시 LibreOffice PDF 변환으로 최종 폴백
                _log.warning(f"[DocumentProcessor] HWP/HWPX 처리기 전체 실패, PDF 변환 폴백 시도: {hwp_err}")
                converted = convert_to_pdf(file_path, use_pdf_sdk=kwargs.get('use_pdf_sdk', True))
                if converted:
                    _log.info(f"[DocumentProcessor] PDF 변환 성공: {converted}")
                    documents: list[Document] = self.load_documents(converted, **kwargs)
                    # 민감정보 분류(#315): 청킹 전, 문서 전체를 분류 워크플로우에 1회 호출.
                    sensitive_infos = (gr.classify_document(
                        gr.docs_text(documents), self._guardrail_url, self._guardrail_workflow_id,
                        self._guardrail_api_key, self._guardrail_timeout)
                        if gr.call_enabled(kwargs) else [])
                    chunks: list[Document] = self.split_documents(documents, **kwargs)
                    vectors: list[dict] = self.compose_vectors(
                        converted, chunks, _sensitive_infos=sensitive_infos, _guardrail_masking=(gr.call_enabled(kwargs) and self._guardrail_masking_enabled), **kwargs)
                    return vectors
                else:
                    # 이슈 #286 — HWP SDK 도 실패하고 PDF 변환기마저 없으면, 원인을 명확히
                    # 안내한다 (혼란스러운 SDK 에러 대신 PDF 직접 입력/재빌드 안내).
                    if not _has_any_pdf_converter():
                        raise GenosServiceException(
                            1,
                            f"이 전처리기 이미지에는 PDF 변환기(rhwp/LibreOffice/PDF SDK)가 설치되어 "
                            f"있지 않아 '{os.path.basename(file_path)}' 처리에 실패했습니다. "
                            f"PDF 로 변환한 파일을 입력하거나, 변환기를 포함해 전처리기 이미지를 다시 "
                            f"빌드하세요 (genon/README.md 참고).",
                        ) from hwp_err
                    raise hwp_err

        elif ext == '.docx':
            return await self.docx_processor(request, file_path, **kwargs)

        elif ext in ('.ppt', '.pptx'):
            # PPT: PDF 변환 → 경량 docling 파싱 → 페이지 단위 image description(옵션) →
            # 페이지 기반 청킹(기본 1 page 1 chunk, chunk_size 지정 시 페이지 결합).
            # 변환 실패 시에만 레거시 langchain 경로로 폴백한다.
            documents: Optional[list[Document]] = self._load_ppt_page_documents(file_path, **kwargs)
            if documents is None:
                documents = self.load_documents(file_path, **kwargs)
                # 민감정보 분류(#315): 청킹 전 1회 호출.
                sensitive_infos = (gr.classify_document(
                    gr.docs_text(documents), self._guardrail_url, self._guardrail_workflow_id,
                    self._guardrail_api_key, self._guardrail_timeout)
                    if gr.call_enabled(kwargs) else [])
                chunks: list[Document] = self.split_documents(documents, **kwargs)
            else:
                # 민감정보 분류(#315): 페이지 결합 청킹 전 1회 호출.
                sensitive_infos = (gr.classify_document(
                    gr.docs_text(documents), self._guardrail_url, self._guardrail_workflow_id,
                    self._guardrail_api_key, self._guardrail_timeout)
                    if gr.call_enabled(kwargs) else [])
                chunks = self._chunk_ppt_pages(documents, **kwargs)
            vectors: list[dict] = self.compose_vectors(
                file_path, chunks, _sensitive_infos=sensitive_infos, _guardrail_masking=(gr.call_enabled(kwargs) and self._guardrail_masking_enabled), **kwargs)
            return vectors

        else:
            documents: list[Document] = self.load_documents(file_path, **kwargs)

            # 민감정보 분류(#315): 청킹 전, 문서 전체를 분류 워크플로우에 1회 호출.
            sensitive_infos = (gr.classify_document(
                gr.docs_text(documents), self._guardrail_url, self._guardrail_workflow_id,
                self._guardrail_api_key, self._guardrail_timeout)
                if gr.call_enabled(kwargs) else [])

            chunks: list[Document] = self.split_documents(documents, **kwargs)

            vectors: list[dict] = self.compose_vectors(
                file_path, chunks, _sensitive_infos=sensitive_infos, _guardrail_masking=(gr.call_enabled(kwargs) and self._guardrail_masking_enabled), **kwargs)

            return vectors
