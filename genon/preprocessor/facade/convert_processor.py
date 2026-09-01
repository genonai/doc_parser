# 변환용 전처리기 v.2.2.4 (2026-07-30 Release)
from __future__ import annotations

import json
import os
import logging
from pathlib import Path

from collections import defaultdict
from datetime import datetime
from typing import Optional, List

from fastapi import Request

_log = logging.getLogger(__name__)


# ── 공용 하위 모듈로 옮긴 헬퍼들의 별칭 ──────────────────────────────
# 구현은 facade/common/, facade/chunking/ 에 한 벌만 둔다. 여기서는 기존 이름을
# 그대로 유지해 호출부를 건드리지 않는다. 사이트별 조정 대상 상수(구분자, 최소
# 청크 크기, 토크나이저 경로)는 이 파일에 남아 있으므로 래퍼가 넘겨준다.
from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.common import pipeline_setup as ps
from genon.preprocessor.facade.common import runtime_kwargs as rk
from genon.preprocessor.facade.enrichment.page_description import inject_page_descriptions
from genon.preprocessor.facade.chunking import page_split
from genon.preprocessor.facade.chunking import smart_chunker as sc
from genon.preprocessor.facade.common import vector_meta as vm
from genon.preprocessor.facade.common import docling_ops as dops
from genon.preprocessor.facade.common import runtime as rt
from genon.preprocessor.facade.common import file_probe as fp
from genon.preprocessor.facade.common import pdf_convert as pc
from genon.preprocessor.facade.chunking import header_path as hp
from genon.preprocessor.facade.chunking import table_blocks as tbk
from genon.preprocessor.facade.chunking import table_variants as tv

_as_dict = cp.as_dict
_as_int_flag = cp.as_int_flag
_copy_enrichment_options = cp.copy_enrichment_options
_detect_unsupported_file = fp.detect_unsupported_file
_filename_title_candidates = hp.filename_title_candidates
_is_encrypted_office = fp.is_encrypted_office
_is_encrypted_pdf = fp.is_encrypted_pdf
_is_protected_hwp = fp.is_protected_hwp
_looks_like_text = fp.looks_like_text
_normalize_filename_title = hp.normalize_filename_title
_parse_optional_bool = cp.parse_optional_bool
_parse_optional_float = cp.parse_optional_float
_parse_optional_int = cp.parse_optional_int
_resolve_chunk_mode = cp.resolve_chunk_mode
_resolve_include_chunk_header = cp.resolve_include_chunk_header
_union_paths = hp.union_paths
_warn_unresolved_placeholders = cp.warn_unresolved_placeholders


def _build_header_line(headings, include_header: bool) -> str:
    return hp.build_header_line(
        headings, include_header, _CHUNK_HEADER_SEP, _CHUNK_PATH_SEP, _CHUNK_PATH_MAX_LEAVES)

def _clamp_chunk_size(size):
    return cp.clamp_chunk_size(size, _MIN_CHUNK_SIZE)

def _collapse_paths(paths) -> list:
    return hp.collapse_paths(paths, _CHUNK_HEADER_SEP)

def _load_config(config_path: str) -> dict:
    return cp.load_config(config_path, strict=True)

def _render_header_paths(headings) -> str:
    return hp.render_header_paths(
        headings, _CHUNK_HEADER_SEP, _CHUNK_PATH_SEP, _CHUNK_PATH_MAX_LEAVES)

def _resolve_tokenizer(chunking_cfg: dict):
    return cp.resolve_tokenizer(
        chunking_cfg, local_path=_DEFAULT_TOKENIZER_LOCAL_PATH, hf_id=_DEFAULT_TOKENIZER_ID)


# from utils import assert_cancelled
import fitz
import uuid
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import (
    # TextLoader,                       # TXT
    UnstructuredPowerPointLoader,  # PPT and PPTX
    UnstructuredFileLoader,  # Generic fallback
)
# docling imports

# HWP/HWPX 레거시 백엔드 (GenosHwp SDK 실패 시 폴백용; olefile/xml 순수 파이썬, SDK 미사용)
from docling.backend.hwp_backend import HwpDocumentBackend
from docling.backend.xml.hwpx_backend import HwpxDocumentBackend
from docling.datamodel.base_models import InputFormat
# from docling.datamodel.document import ConversionStatus
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    PdfPipelineOptions,
    TableFormerMode,
    PipelineOptions,
    UpstageOcrOptions,
)

from docling.document_converter import (
    DocumentConverter,
    HwpxFormatOption
)
from docling.datamodel.pipeline_options import DataEnrichmentOptions
from docling.prompts.prompt_manager import LLMApiError
from docling.utils.document_enrichment import enrich_document, check_document
from docling.utils.llm_cache import (
    log_summary as _log_cache_summary,
    reset_context as _reset_cache_context,
    resolve_context as _resolve_cache_context,
    set_context as _set_cache_context,
)
from docling.datamodel.document import ConversionResult
from docling.exceptions import HwpConversionError
from docling_core.transforms.chunker import (
    DocChunk,
)


import asyncio
from docling_core.types.doc import (
    DocItemLabel,
    DoclingDocument,
)
from docling.datamodel.settings import settings



from pydantic import BaseModel

try:
    import semchunk
    from transformers import AutoTokenizer, PreTrainedTokenizerBase
except ImportError:
    raise RuntimeError(
        "Module requires 'chunking' extra; to install, run: "
        "`pip install 'docling-core[chunking]'`"
    )

try:
    from genos_utils import upload_files
except ImportError:
    upload_files = None

from genon.preprocessor.facade.enrichment.enrichment_config import EnrichmentConfig
from genon.preprocessor.facade.enrichment.field_transforms import (
    DEFAULT_METADATA_FIELD_TRANSFORMS,
    apply_field_transforms,
    extract_metadata_from_document,
    serialize_metadata_value_for_output,
    store_metadata_in_document,
)
from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
    build_document_custom_fields_enrichers,
    normalize_doc_type,
)
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    build_tabular_custom_fields_mappers,
    warn_tabular_llm_fields_unsupported as _warn_tabular_llm_fields_unsupported,
)
from genon.preprocessor.facade.enrichment.metadata_enricher import MetadataEnricher

from genon.preprocessor.facade.enrichment.page_description import (
    PageDescriptionOptions,
)
from genon.preprocessor.facade.enrichment.image_description import (
    ImageDescriptionOptions,
    ImageDescriptionEnricher,
)
from genon.preprocessor.facade.enrichment.table_text_description import (
    TableTextDescriptionEnricher,
    apply_table_description_stage,
)
from genon.preprocessor.facade.enrichment.table_description import (
    TableDescriptionOptions,
    TableDescriptionEnricher,
)
from genon.preprocessor.facade.enrichment.doc_summary import (
    DocSummaryOptions,
    DocSummaryEnricher,
)
from genon.preprocessor.facade.chunking import text_norm as tn


# ============================================================
# 설정 로딩 헬퍼 (from parser_processor.py)
# ============================================================


# 한 경로 안의 레벨 구분자(부모 → 자식). heading 자체에 콤마가 들어있는 경우가 있어
# (실측 409건 중 20건) 콤마로는 경로를 레벨 단위로 되돌릴 수 없다 —
# 예: "제4조(여비) ① 여비는 여객운임, 숙박비, 식비 …". " > " 는 실측 충돌이 0 이다.
# (검색 신호로서의 효과는 미검증이다. 표준 BM25 분석기는 문장부호를 제거한다.
#  이 구분자의 근거는 파싱 안정성과 사람이 읽을 때의 명료성이다.)
_CHUNK_HEADER_SEP = " > "

# 서로 다른 경로(형제 섹션) 사이 구분자. 경로 내부 구분자와 반드시 달라야
# `A > B`(부모-자식)와 `A | B`(형제)가 구분된다. 한 청크가 여러 섹션에 걸치면
# 예전에는 전부 평탄하게 dedup 해서 형제를 부모-자식처럼 표기했다
# (실측: `상품 안내 > 우대금리 조건 > 가입 제한` — 뒤 둘은 형제).
_CHUNK_PATH_SEP = " | "


# 다경로 청크에서 나열할 리프 최대 개수. 초과분은 "… 외 N개" 로 접는다.
# resize_all 로 수십 개 섹션이 한 청크에 뭉치면 경로를 전부 나열한 헤더가 노이즈가 된다
# (실측: hwp 71경로 → 헤더 3,239자, 청크가 chunk_size 를 30% 초과).
_CHUNK_PATH_MAX_LEAVES = 5


_MIN_CHUNK_SIZE = 1024


# pdf_pipeline.device / pdf_pipeline.table_structure_mode 의 yaml 문자열 → docling enum 매핑.
# 키가 없거나 알 수 없는 값이면 호출부에서 경고 + 기본값으로 폴백한다 (startup 견고성).
_ACCELERATOR_DEVICE_MAP = {
    "auto": AcceleratorDevice.AUTO,
    "cpu": AcceleratorDevice.CPU,
    "cuda": AcceleratorDevice.CUDA,
    "mps": AcceleratorDevice.MPS,
}

_TABLE_FORMER_MODE_MAP = {
    "accurate": TableFormerMode.ACCURATE,
    "fast": TableFormerMode.FAST,
}


def _resolve_default_convert_config_path() -> str:
    base_dir = Path(__file__).resolve().parent
    local_config = (base_dir / "../resource_dev/convert_processor_config.yaml").resolve()
    default_config = (base_dir / "../resource/convert_processor_config.yaml").resolve()

    if local_config.exists():
        return str(local_config)
    return str(default_config)


# 청킹용 토크나이저 기본 경로 (config 미지정 시 현행 동작 유지)
_DEFAULT_TOKENIZER_LOCAL_PATH = "/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2"
_DEFAULT_TOKENIZER_ID = "sentence-transformers/all-MiniLM-L6-v2"

# tabular 모드로 직접 처리(행=벡터)할 엑셀 계열 포맷(이슈 #288).
# docling 모드(기본)에서는 xlsx 가 self.converter 의 docling 기본 백엔드(MsExcel)로 처리되므로
# 별도 인터셉트 없이 기존 경로를 그대로 탄다.
_XLSX_DIRECT_EXTS = {".xlsx", ".xlsm", ".csv"}


# ============================================
#
# Copyright IBM Corp. 2024 - 2024
# SPDX-License-Identifier: MIT
#

"""Chunker implementation leveraging the document structure."""
CONVERTIBLE_EXTENSIONS = ['.txt', '.json', '.md', '.docx', '.ppt', '.pptx']


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


class GenosSmartChunker(sc.SmartChunkerBase):
    """청킹 본체는 facade/chunking/smart_chunker.py 에 있다.

    여기에는 이 facade 가 고른 동작 옵션과 헤더 구분자만 둔다. 값을 바꾸면 청킹
    동작이 바로 달라지므로, 사이트에서 손댈 지점은 사실상 이 블록이다.
    """

    # 그림 annotation 텍스트를 청크 본문에 싣는다 — 끄면 이미지 자리에 빈 문자열이 들어간다.
    PICTURE_ANNOTATION_TEXT = False
    # 표 설명 annotation 반영 범위: refine HTML + 검색 설명 접두 + 요약 접미.
    TABLE_DESCRIPTION_MODE = "full"

    # 헤더 경로 구분자는 이 파일의 모듈 상수를 그대로 쓴다 — 청크 크기 산정과
    # compose_vectors 의 실제 부착이 반드시 같은 문자열을 봐야 한다.
    CHUNK_HEADER_SEP = _CHUNK_HEADER_SEP
    CHUNK_PATH_SEP = _CHUNK_PATH_SEP
    CHUNK_PATH_MAX_LEAVES = _CHUNK_PATH_MAX_LEAVES
# 민감정보 분류/마스킹(#315)은 facade/guardrail 모듈로 분리 — gr.* 로 사용.
from genon.preprocessor.facade import guardrail as gr


class GenOSVectorMeta(BaseModel):
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
    created_date: Optional[int] = None  # YYYYMMDD 형식의 정수
    authors: Optional[str] = None      # 팀 리스트
    title: Optional[str] = None         # 문서 제목
    guardrail_categories: Optional[list] = None    # #315 민감정보 분류 라벨(부동산/인사/민감 등). 미적용 시 None
    # 표 메타(#360). 표 청크만 골라 검색하거나 나뉜 조각을 원래 순서로 잇는 데 쓴다.
    has_table: bool = False
    table_refs: Optional[str] = None
    table_split_index: Optional[int] = None
    table_split_total: Optional[int] = None

class GenOSVectorMetaBuilder(vm.VectorMetaBuilderBase):
    """공통 세터(텍스트 통계·페이지·bbox·미디어·글로벌 메타데이터)는
    facade/common/vector_meta.py 에 있다. 여기에는 이 facade 고유 필드만 둔다."""

    def __init__(self):
        """빌더 초기화"""
        super().__init__()
        self.created_date: Optional[int] = None
        self.authors: Optional[str] = None      # 팀 리스트
        self.title: Optional[str] = None

    def build(self) -> GenOSVectorMeta:
        """설정된 데이터를 사용해 최종적으로 GenOSVectorMeta 객체 생성"""
        payload = {
            **self.core_payload(),
            "created_date": self.created_date,
            "authors": self.authors,      # 팀 리스트
            "title": self.title,
            **self.extra_metadata,
        }
        return GenOSVectorMeta.model_validate(payload)


class DocumentProcessor:

    def __init__(self, config_path: str | None = None):
        '''
        initialize Document Converter (config 기반)

        config_path 가 None 이면 resource_dev/convert_processor_config.yaml
        (없으면 resource/convert_processor_config.yaml) 을 사용한다.
        GenOS 는 DocumentProcessor() 무인자로 호출하므로 기본 경로 resolve 필수.
        '''
        if config_path is None:
            config_path = _resolve_default_convert_config_path()

        cfg = _load_config(config_path)
        self._config_dir = Path(config_path).resolve().parent
        # 런타임 kwargs 기본값(img_desc/chart_desc/chart_detection/doc_summary) 용도
        self._runtime_cfg = _as_dict(cfg.get("runtime"))

        defaults_cfg = _as_dict(cfg.get("defaults"))
        log_level = _parse_optional_int(defaults_cfg.get("log_level"), "defaults.log_level")
        if log_level is None:
            log_level = 4
        self._log_level = log_level

        ocr_cfg = _as_dict(cfg.get("ocr"))
        layout_cfg = _as_dict(cfg.get("layout"))
        pdf_cfg = _as_dict(cfg.get("pdf_pipeline"))
        models_cfg = _as_dict(cfg.get("models"))
        chunking_cfg = _as_dict(cfg.get("chunking"))
        ec = EnrichmentConfig.from_raw(cfg.get("enrichment"), self._config_dir, parent_cfg=cfg)

        # 청킹용 토크나이저 (chunking config 기반; 미지정 시 현행 기본값)
        self._tokenizer = _resolve_tokenizer(chunking_cfg)

        # 토큰 수 계산 방식 (chunking 섹션). "char"(default)=문자 수 기준 | "huggingface"=HF 토크나이저 기준
        self._tokenizer_type = str(chunking_cfg.get("tokenizer_type", "char")).strip().lower()
        if self._tokenizer_type not in {"char", "huggingface"}:
            _log.warning(
                f"[DocumentProcessor] Unknown chunking.tokenizer_type '{self._tokenizer_type}', fallback to 'char'."
            )
            self._tokenizer_type = "char"

        # 청크 최대 크기(GenosSmartChunker.max_tokens) 기본값. kwargs 의 chunk_size 가 우선.
        self._chunk_size = _parse_optional_int(chunking_cfg.get("chunk_size"), "chunking.chunk_size")

        # 청킹 모드: "split_only"(기본, chunk_size 초과 청크만 분할) | "resize_all"(모든 청크를 chunk_size 에 맞게 병합/분할)
        self._chunk_mode = str(chunking_cfg.get("chunk_mode", "split_only")).strip().lower()
        if self._chunk_mode not in {"split_only", "resize_all"}:
            _log.warning(f"[DocumentProcessor] Unknown chunking.chunk_mode '{self._chunk_mode}', fallback to 'split_only'.")
            self._chunk_mode = "split_only"

        # 청크 선두 "HEADER: <섹션 경로>" 라인 부착 여부(기본 True). kwargs 의 include_chunk_header 가 우선.
        _ich = _parse_optional_bool(chunking_cfg.get("include_chunk_header"), "chunking.include_chunk_header")
        self._include_chunk_header = True if _ich is None else _ich

        # 표를 본문과 섞지 않고 독자 청크로 낼지(chunking.table_as_chunk, 기본 true).
        # kwargs 의 table_as_chunk 가 우선.
        _tac = _parse_optional_bool(chunking_cfg.get("table_as_chunk"), "chunking.table_as_chunk")
        self._table_as_chunk = True if _tac is None else _tac

        # 청크 텍스트 정규화(chunking.text_cleanup): "off"(기본) | "safe".
        # safe 면 청킹 입력에 문자 위생(tn.sanitize)을, 벡터 생성 직전에 표현 정리(tn.tidy)를 적용한다.
        # 우선순위: kwargs.text_cleanup > 아래 > "off".
        self._text_cleanup = tn.mode_from_cfg(chunking_cfg)

        # xlsx(엑셀) 처리 설정(이슈 #288). formats.xlsx 아래에 둔다(포맷별 옵션 컨테이너).
        #   docling(기본): xlsx 를 docling MsExcel 백엔드로 처리(현행) → 기존 청킹/벡터 파이프라인.
        #   tabular: 데이터 행마다 1벡터 + 컬럼 헤더→메타(병합셀 unmerge+forward-fill).
        #   tabular.{header_row, multi_table}: tabular 모드 전용 세부 옵션
        formats_cfg = _as_dict(cfg.get("formats"))
        xlsx_cfg = _as_dict(formats_cfg.get("xlsx"))
        tabular_cfg = _as_dict(xlsx_cfg.get("tabular"))
        xlsx_mode = str(xlsx_cfg.get("processing_mode", "docling")).strip().lower()
        if xlsx_mode not in {"docling", "tabular"}:
            _log.warning(
                f"[DocumentProcessor] Unknown formats.xlsx.processing_mode '{xlsx_mode}', fallback to 'docling'."
            )
            xlsx_mode = "docling"
        self._xlsx_cfg = {
            "processing_mode": xlsx_mode,
            "header_row": _parse_optional_int(tabular_cfg.get("header_row"), "formats.xlsx.tabular.header_row") or 0,
            "multi_table": bool(_parse_optional_bool(tabular_cfg.get("multi_table"), "formats.xlsx.tabular.multi_table")),
        }

        # 표 텍스트 직렬화 형식(청크 text 내 docling 표 표현). "html"(default) | "markdown".
        output_cfg = _as_dict(cfg.get("output"))
        # auto 는 여기서 확정하지 않는다 - 표마다 grid 구조를 봐야 정해지므로 청커로 넘긴다.
        self._table_format = cp.resolve_table_format_setting(output_cfg)
        # markdown 표 compact(컬럼 정렬 패딩 제거) 여부. 기본 True. html 포맷엔 무관.
        self._compact_tables = cp.resolve_compact_tables(output_cfg)
        # 병합 셀 표에 행 문장을 덧붙일지. 기본 off(청크가 커진다).
        self._table_row_serialization = cp.resolve_table_row_serialization(output_cfg)
        # 같은 청크 전문을 표만 다른 표기형태로 렌더한 텍스트를 추가 필드로 실을지.
        # 기본은 빈 목록(추가 필드 없음) — 켜면 본문이 형식 수만큼 복제되어 페이로드가 커진다.
        self._table_text_formats = cp.resolve_table_text_formats(output_cfg)

        # OCR 엔드포인트는 ocr.paddle.ocr_endpoint 가 정식 위치.
        # 구버전 호환: ocr.ocr_endpoint(상위) / 최상위 ocr_endpoint 도 폴백으로 인식.
        # 해석은 facade/common/pipeline_setup.py 로 모았다(조정 지점은 yaml 의 ocr 섹션).
        _ocr_rt = ps.resolve_ocr_runtime(cfg, ocr_cfg)
        ocr_ep = _ocr_rt.endpoint
        self.ocr_mode = _ocr_rt.mode
        self._table_cell_ocr_timeout = _ocr_rt.table_cell_ocr_timeout
        self._glyph_table_cell_threshold = _ocr_rt.glyph_table_cell_threshold
        self._glyph_document_threshold = _ocr_rt.glyph_document_threshold

        # (PDF 입력에만 적용. DOCX/기타 포맷은 ocr_mode 무관)

        ocr_options = self._build_ocr_options(ocr_cfg, paddle_endpoint=ocr_ep)
        if isinstance(ocr_options, UpstageOcrOptions):
            self.ocr_endpoint = ocr_options.api_endpoint
        else:
            self.ocr_endpoint = ocr_ep

        # 민감정보 분류(#315): GenOS 분류 워크플로우 접속 정보. on/off 는 요청별 kwargs(guardrail_call).
        gm_cfg = _as_dict(cfg.get("guardrail"))
        self._guardrail_url = str(gm_cfg.get("url") or "").strip()
        self._guardrail_workflow_id = _parse_optional_int(gm_cfg.get("workflow_id"), "guardrail.workflow_id")
        self._guardrail_api_key = str(gm_cfg.get("api_key") or "").strip()
        gm_timeout = _parse_optional_int(gm_cfg.get("timeout"), "guardrail.timeout")
        self._guardrail_timeout = gm_timeout if gm_timeout and gm_timeout > 0 else 60
        self._guardrail_masking_enabled = bool(_parse_optional_bool(gm_cfg.get("masking_enabled"), "guardrail.masking_enabled"))

        self.page_chunk_counts = defaultdict(int)

        # pdf_pipeline 섹션 해석은 facade/common/pipeline_setup.py 로 모았다.
        _pdf = ps.resolve_pdf_basics(pdf_cfg)
        accelerator_options = _pdf.accelerator_options
        images_scale = _pdf.images_scale
        generate_page_images = _pdf.generate_page_images
        generate_picture_images = _pdf.generate_picture_images
        table_structure_mode = _pdf.table_structure_mode

        # 표 이미지(table_image) 옵션: 표를 picture 와 동일하게 이미지로 잘라 저장하고,
        # media_files 에 type='table_image' 로 기록한다(검색=청크 텍스트 / 답변=표 이미지).
        # 기본 False 라 미설정 시 기존 동작과 동일(하위 호환).
        table_image_cfg = _as_dict(cfg.get("table_image"))
        self.table_image_enabled = bool(
            _parse_optional_bool(table_image_cfg.get("enable"), "table_image.enable")
        )

        # PPT 페이지 단위 image description(page-level). config: formats.ppt.page_description.
        # 공통 모듈(enrichment/page_description)로 파싱. PPT(.pptx) 원본에만 적용.
        ppt_fmt_cfg = _as_dict(formats_cfg.get("ppt"))
        page_img_cfg = _as_dict(ppt_fmt_cfg.get("page_description"))
        self._page_desc_options = PageDescriptionOptions.from_config(page_img_cfg, self._config_dir)

        # PDF 파이프라인 옵션 설정
        self.pipe_line_options = PdfPipelineOptions()
        self.pipe_line_options.generate_page_images = (
            True if generate_page_images is None else generate_page_images
        )
        self.pipe_line_options.generate_picture_images = (
            True if generate_picture_images is None else generate_picture_images
        )
        # 표 이미지 크롭(TableItem.get_image)/페이지 설명은 페이지 이미지를 소스로 하므로,
        # table_image 또는 page_description 이 켜지면 generate_page_images 를 True 로 강제 보장한다.
        if self.table_image_enabled or self._page_desc_options.enabled:
            self.pipe_line_options.generate_page_images = True
        self.pipe_line_options.do_ocr = False
        self.pipe_line_options.ocr_options = ocr_options
        self.pipe_line_options.images_scale = images_scale

        # layout 모델 선택. "genos_layout"(default) / "docling_layout". 잘못된 값은 경고 후 폴백.
        # 해석·적용은 facade/common/pipeline_setup.py 로 모았다(조정 지점은 yaml 의 layout 섹션).
        _layout = ps.resolve_layout_settings(cfg, layout_cfg)
        ps.apply_layout_settings(self.pipe_line_options, _layout)
        settings.perf.page_batch_size = _layout.page_batch_size

        self.pipe_line_options.do_table_structure = True
        self.pipe_line_options.table_structure_options.do_cell_matching = True
        self.pipe_line_options.table_structure_options.mode = table_structure_mode
        self.pipe_line_options.accelerator_options = accelerator_options

        # docling 모델(TableFormer 등) 로컬 경로. config 에 값이 있을 때만 설정하고,
        # 비어있으면 설정하지 않아 docling 기본 캐시 동작을 그대로 유지(backward compat).
        # (아래 ocr_pipe_line_options 는 pipe_line_options 의 deep copy 라 자동 전파됨)
        artifacts_path = models_cfg.get("artifacts_path")
        if artifacts_path:
            self.pipe_line_options.artifacts_path = Path(artifacts_path)

        # Simple 파이프라인 옵션을 인스턴스 변수로 저장
        self.simple_pipeline_options = PipelineOptions()
        self.simple_pipeline_options.save_images = False

        # 이미지/차트 description 옵션. chart.enable 이면 변환 단계에서 그림 분류가 필요하므로
        # 컨버터(ocr 포함) 생성 전에 옵션을 결정하고 do_picture_classification 을 켜 둔다.
        self.image_description_options = ImageDescriptionOptions.from_config(
            image_desc_cfg=ec.image_description_cfg,
            fallback_api_url=ec.api_url,
            fallback_api_key=ec.api_key,
            fallback_model=ec.model,
            config_dir=self._config_dir,
        )
        # 런타임 kwargs 오버라이드의 기준(base) 옵션 보관
        self._base_image_description_options = self.image_description_options
        # chart.enable=true 이면 그림 분류를 켠다(런타임 chart_detection=auto 전환 허용).
        if self.image_description_options.chart_enabled:
            try:
                self.pipe_line_options.do_picture_classification = True
            except Exception as exc:
                _log.warning(
                    f"[DocumentProcessor] do_picture_classification 설정 실패: {exc}"
                )

        # 표 description 옵션. VLM 이 표 영역을 crop 하려면 페이지 이미지가 필요하므로
        # base 옵션이 켜져 있으면 컨버터 생성 전에 generate_page_images 를 강제한다.
        self.table_description_options = TableDescriptionOptions.from_config(
            table_desc_cfg=ec.table_description_cfg,
            fallback_api_url=ec.api_url,
            fallback_api_key=ec.api_key,
            fallback_model=ec.model,
            config_dir=self._config_dir,
        )
        self._base_table_description_options = self.table_description_options
        if self.table_description_options.enabled:
            self.pipe_line_options.generate_page_images = True

        # 문서 본문요약(doc_summary) 옵션. image/table 이 공유하는 {{doc_summary}} 를 1회 계산.
        self.doc_summary_options = DocSummaryOptions.from_config(
            doc_summary_cfg=ec.doc_summary_cfg,
            fallback_api_url=ec.api_url,
            fallback_api_key=ec.api_key,
            fallback_model=ec.model,
            config_dir=self._config_dir,
        )
        self._base_doc_summary_options = self.doc_summary_options

        # ocr 파이프라인 옵션
        self.ocr_pipe_line_options = PdfPipelineOptions()
        self.ocr_pipe_line_options = self.pipe_line_options.model_copy(deep=True)
        self.ocr_pipe_line_options.do_ocr = True
        self.ocr_pipe_line_options.ocr_options = ocr_options.model_copy(deep=True)
        self.ocr_pipe_line_options.ocr_options.force_full_page_ocr = True

        # 기본 컨버터들 생성
        self._create_converters()

        self.image_description_enricher = ImageDescriptionEnricher(
            self.image_description_options
        )
        self.table_description_enricher = TableDescriptionEnricher(
            self.table_description_options
        )
        # 텍스트 표 설명. 자체 url/model 이 있으면 custom_fields 의 LLM 사용 여부와 무관하게
        # 이 실행기가 표 설명을 맡는다(table_text_description 모듈 docstring 참고).
        self.table_text_description_enricher = TableTextDescriptionEnricher(
            ec.table_text_description_cfg
        )
        self.doc_summary_enricher = DocSummaryEnricher(self.doc_summary_options)
        self.custom_fields_enrichers: list = (
            build_document_custom_fields_enrichers(ec.custom_fields_cfgs)
        )
        # enrichment.custom_fields 중 tabular_mapping handler(요청 doc_type=faq 등 xlsx 행별 매핑).
        # LLM enricher 와 달리 파싱 조기 분기(_process_xlsx)에서 소비한다.
        self._tabular_custom_fields_mappers: list = (
            build_tabular_custom_fields_mappers(ec.custom_fields_cfgs)
        )
        _warn_tabular_llm_fields_unsupported(self._tabular_custom_fields_mappers, "convert")
        self.metadata_enricher = (
            MetadataEnricher(
                url=ec.metadata.url,
                api_key=ec.metadata.api_key,
                model=ec.metadata.model,
                system_prompt=ec.metadata.system_prompt,
                user_prompt=ec.metadata.user_prompt,
                output_fields=ec.metadata.output_fields,
                parser=ec.metadata.parser,
                pages=ec.metadata.pages,
                max_tokens=ec.metadata.max_tokens,
                temperature=ec.metadata.temperature,
                timeout=ec.metadata.timeout,
                config_dir=self._config_dir,
                variables=ec.metadata.variables,
                template_mode=ec.metadata.template_mode,
                thinking=ec.metadata.thinking,
                thinking_dialect=ec.metadata.thinking_dialect,
            )
            if ec.metadata.do_metadata and ec.metadata.has_custom_metadata
            else None
        )
        # 추출 메타데이터 → typed 벡터 필드 매핑(설정 기반). 설정이 비어있으면
        # 기존 created_date 동작을 그대로 재현한다(하위 호환).
        self._metadata_field_transforms = (
            ec.metadata.field_transforms or DEFAULT_METADATA_FIELD_TRANSFORMS
        )

        # enrichment 옵션 설정 (yaml 의 enrichment 섹션을 EnrichmentConfig 로 파싱)
        self.enrichment_options = DataEnrichmentOptions(
            do_toc_enrichment=ec.toc.do_toc,
            toc_doc_type=ec.toc.doc_type,
            extract_metadata=ec.metadata.do_metadata and self.metadata_enricher is None,
            toc_api_provider="custom",
            metadata_api_provider="custom",
            toc_api_base_url=ec.toc.url,
            metadata_api_base_url=ec.metadata.url,
            toc_api_key=ec.toc.api_key,
            metadata_api_key=ec.metadata.api_key,
            toc_model=ec.toc.model,
            metadata_model=ec.metadata.model,
            toc_temperature=ec.toc.temperature,
            toc_top_p=ec.toc.top_p,
            toc_seed=ec.toc.seed,
            toc_max_tokens=ec.toc.max_tokens,
            toc_repetition_penalty=ec.toc.repetition_penalty,
            toc_precheck_enabled=ec.toc.precheck_enabled,
            toc_max_context_tokens=ec.toc.precheck_max_context_tokens,
            toc_completion_reserved_tokens=ec.toc.precheck_completion_reserved_tokens,
            toc_split_enabled=ec.toc.split_enabled,
            toc_pages_per_chunk=ec.toc.split_pages_per_chunk,
            toc_page_overlap=ec.toc.split_page_overlap,
            toc_carryover_max_tokens=ec.toc.split_carryover_max_tokens,
            metadata_precheck_enabled=ec.metadata.precheck_enabled,
            metadata_max_context_tokens=ec.metadata.precheck_max_context_tokens,
            metadata_completion_reserved_tokens=ec.metadata.precheck_completion_reserved_tokens,
            toc_system_prompt=ec.toc.system_prompt,
            toc_user_prompt=ec.toc.user_prompt,
            toc_thinking=ec.toc.thinking,
            toc_thinking_dialect=ec.toc.thinking_dialect,
            metadata_thinking=ec.metadata.thinking,
            metadata_thinking_dialect=ec.metadata.thinking_dialect,
        )

    @staticmethod
    def _build_ocr_options(ocr_cfg: dict, paddle_endpoint: str):
        return dops.build_ocr_options(ocr_cfg, paddle_endpoint)

    def _create_converters(self):
        """컨버터들을 생성하는 헬퍼 메서드"""
        (self.converter, self.second_converter,
         self.ocr_converter, self.ocr_second_converter) = dops.create_converters(
            self.pipe_line_options, self.ocr_pipe_line_options)

    def load_documents_with_docling(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        try:
            conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)
        except Exception as e:
            conv_result: ConversionResult = self.second_converter.convert(file_path, raises_on_error=True)
        return conv_result.document

    def load_documents_with_docling_ocr(self, file_path: str, **kwargs: dict) -> DoclingDocument:

        try:
            conv_result: ConversionResult = self.ocr_converter.convert(file_path, raises_on_error=True)
        except Exception as e:
            conv_result: ConversionResult = self.ocr_second_converter.convert(file_path, raises_on_error=True)
        return conv_result.document

    def _load_hwp_with_legacy_backend(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        """HWP/HWPX 레거시 백엔드(SDK 미사용) 전용 변환 — GenosHwp SDK 폴백용.

        HWP/HWPX 는 docling 기본 백엔드(GenosHwpDocumentBackend=convtext SDK)로 처리되는데,
        SDK 가 UNSUPPORTED_TYPE(exit code 3) 등으로 실패할 때 이 메서드로 폴백한다.
        .hwp → HwpDocumentBackend, .hwpx → HwpxDocumentBackend (둘 다 olefile/xml 기반
        순수 파이썬이라 SDK 의존이 없음). attachment_processor.HwpProcessor 의 폴백과 동일 구성.
        """
        pipeline_options = PipelineOptions()
        pipeline_options.save_images = kwargs.get('save_images', True)
        converter = DocumentConverter(
            format_options={
                InputFormat.HWP: HwpxFormatOption(
                    pipeline_options=pipeline_options,
                    backend=HwpDocumentBackend,
                ),
                InputFormat.XML_HWPX: HwpxFormatOption(
                    pipeline_options=pipeline_options,
                    backend=HwpxDocumentBackend,
                ),
            }
        )
        conv_result: ConversionResult = converter.convert(
            Path(file_path).resolve(), raises_on_error=True
        )
        return conv_result.document

    @staticmethod
    def _hwp_sdk_text_is_empty(document: DoclingDocument) -> bool:
        """GenosHwp SDK 결과 문서에 본문 텍스트가 전혀 없는지 판단(레거시 폴백 트리거용).

        SDK 가 exit 0 으로 "성공"해도 본문을 한 글자도 못 뽑는 경우가 있다(일부 .hwp/.hwpx).
        이때 doc_items 가 비어 다운스트림 GenosSmartChunker 의 DocMeta(min_length=1) 검증이
        깨진다(too_short). 텍스트 run 이 하나도 없으면 True.
        """
        texts = getattr(document, "texts", None) or []
        return not any((getattr(t, "text", "") or "").strip() for t in texts)

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        ext = os.path.splitext(file_path)[-1].lower()
        is_hwp = ext in ('.hwp', '.hwpx')
        backend_name = "HwpDocumentBackend" if ext == '.hwp' else "HwpxDocumentBackend"
        try:
            document = self.load_documents_with_docling(file_path, **kwargs)
        except Exception as sdk_err:
            # (1) GenosHwp SDK 가 예외로 실패(예: exit code 3) → HWP/HWPX 만 레거시 폴백.
            if not is_hwp:
                raise
            _log.warning(f"[DocumentProcessor] GenosHwp SDK 변환 실패: {sdk_err}")
            try:
                _log.info(f"[DocumentProcessor] {backend_name}로 폴백 시도: {file_path}")
                document = self._load_hwp_with_legacy_backend(file_path, **kwargs)
                _log.info(f"[DocumentProcessor] {backend_name} 폴백 성공")
                return document
            except Exception as fallback_err:
                _log.warning(f"[DocumentProcessor] {backend_name} 폴백도 실패: {fallback_err}")
                raise sdk_err

        # .hml(HWPML)은 GenosHwp SDK 전용 포맷 — 레거시 백엔드가 없어 빈 결과면 바로
        # 상위(__call__)의 PDF 변환 폴백으로 위임한다 (이슈 #323).
        if ext == '.hml' and self._hwp_sdk_text_is_empty(document):
            raise HwpConversionError(
                f"HML SDK 결과가 비어 있음(hml 은 레거시 백엔드 없음): {file_path}"
            )

        # (2) SDK 가 예외 없이(exit 0) 끝났지만 본문 텍스트가 비어 있으면(빈 doc_items 로
        #     다운스트림 DocMeta 검증이 깨지는 케이스) 레거시 백엔드로 폴백 시도한다.
        #     폴백 결과도 비었거나 폴백이 실패하면 원 SDK 결과를 그대로 유지(무회귀).
        if is_hwp and self._hwp_sdk_text_is_empty(document):
            _log.warning(
                f"[DocumentProcessor] GenosHwp SDK 결과에 본문 텍스트가 없어 {backend_name} 폴백 시도: {file_path}"
            )
            try:
                fallback_doc = self._load_hwp_with_legacy_backend(file_path, **kwargs)
                if not self._hwp_sdk_text_is_empty(fallback_doc):
                    _log.info(f"[DocumentProcessor] {backend_name} 폴백 성공(본문 텍스트 확보)")
                    return fallback_doc
                _log.info(f"[DocumentProcessor] {backend_name} 폴백 결과도 비어 상위 PDF 폴백으로 위임")
            except Exception as fallback_err:
                _log.warning(f"[DocumentProcessor] {backend_name} 폴백 실패, 상위 PDF 폴백으로 위임: {fallback_err}")
            # SDK·레거시 모두 본문을 못 얻음 → 예외로 올려 __call__ 의 PDF 변환 폴백에 위임한다.
            raise HwpConversionError(
                f"HWP/HWPX SDK 결과가 비어 있고 레거시 백엔드로도 본문 복구 실패: {file_path}"
            )
        return document

    def get_loader_langchain(self, file_path: str, use_pdf_sdk: bool = True):
        """PPT 파일용 langchain 로더"""
        ext = os.path.splitext(file_path)[-1].lower()
        if ext == '.ppt':
            convert_to_pdf(file_path, use_pdf_sdk=use_pdf_sdk)
            return UnstructuredPowerPointLoader(file_path)
        else:
            return UnstructuredFileLoader(file_path)

    def load_documents_langchain(self, file_path: str, **kwargs: dict):
        """langchain으로 문서 로드"""
        loader = self.get_loader_langchain(file_path, use_pdf_sdk=kwargs.get('use_pdf_sdk', True))
        documents = loader.load()
        return documents

    def split_documents_langchain(self, documents, **kwargs: dict):
        """langchain 문서를 청킹"""

        splitter_params = {}
        chunk_size = kwargs.get('chunk_size')
        chunk_overlap = kwargs.get('chunk_overlap')

        if chunk_size is not None:
            splitter_params['chunk_size'] = chunk_size

        if chunk_overlap is not None:
            splitter_params['chunk_overlap'] = chunk_overlap

        # 청크 텍스트 정규화(text_cleanup=safe): 분할 전에 문자 위생을 적용한다.
        _cleanup = tn.enabled_for(kwargs, self)
        if _cleanup:
            tn.sanitize_langchain_docs(documents)

        text_splitter = RecursiveCharacterTextSplitter(**splitter_params)
        chunks = text_splitter.split_documents(documents)
        # 내용이 남지 않는 청크는 page_chunk_counts 집계 전에 제거한다.
        if _cleanup:
            chunks = tn.drop_blank_chunks(chunks, "page_content")
        else:
            chunks = [chunk for chunk in chunks if chunk.page_content]
        if not chunks:
            raise Exception('Empty document')

        for chunk in chunks:
            page = chunk.metadata.get('page', 1)

            source = chunk.metadata.get('source', '')
            file_ext = os.path.splitext(source)[-1].lower() if source else ''

            if file_ext in ['.jpg', '.jpeg', '.png']:
                # 이미지 파일: 이미 1-based이므로 그대로 사용
                if isinstance(page, int) and page <= 0:
                    page = 1  # 0이거나 음수인 경우에만 1로 설정
            else:
                # 다른 파일들: 0-based를 1-based로 변환
                if isinstance(page, int) and page >= 0:
                    page += 1

            chunk.metadata['page'] = page
            self.page_chunk_counts[page] += 1
        return chunks

    def split_documents(self, documents: DoclingDocument, **kwargs: dict) -> List[DocChunk]:
        # chunk_size 우선순위: kwargs > yaml(chunking.chunk_size) > 0
        chunk_size = _parse_optional_int(kwargs.get('chunk_size'), 'chunk_size')
        if chunk_size is None:
            chunk_size = self._chunk_size
        chunk_size = _clamp_chunk_size(chunk_size)
        # chunk_mode 우선순위: kwargs > yaml(chunking.chunk_mode) > "split_only"
        # chunk_mode(0/1 또는 'split_only'/'resize_all') > yaml > "split_only"
        chunk_mode = _resolve_chunk_mode(kwargs, self._chunk_mode)
        chunker: GenosSmartChunker = GenosSmartChunker(
            max_tokens = chunk_size if chunk_size is not None else 0,
            merge_peers = True,
            tokenizer = self._tokenizer,
            tokenizer_type = self._tokenizer_type,
            chunk_mode = chunk_mode,
            # 크기 산정(_size)이 compose_vectors 의 실제 부착 여부와 같은 값을 보게 한다.
            include_chunk_header = _resolve_include_chunk_header(kwargs, self._include_chunk_header),
            table_as_chunk = cp.resolve_table_as_chunk(kwargs, self._table_as_chunk),
        )

        # 표 직렬화 형식(html|markdown)을 청커로 전달(런타임 kwarg 가 있으면 우선).
        cp.apply_table_output_defaults(kwargs, self)
        # 청크 텍스트 정규화(text_cleanup=safe): 문자 위생을 청킹 입력에 먼저 적용한다.
        # 출력에서만 정규화하면 청크 경계가 노이즈 문자를 센 채로 잡힌다.
        _cleanup = tn.prepare_document(documents, kwargs, self)
        chunks: List[DocChunk] = list(chunker.chunk(dl_doc=documents, **kwargs))
        # 표별 분할 조각 수는 청커만 안다. compose_vectors 가 조각 순서 메타를 매길 때 읽는다.
        self._table_split_totals = getattr(chunker, "_table_split_totals", {})
        # 표 표기형태별 변형 텍스트도 같은 방식으로 청커에서 받는다.
        self._table_variants = getattr(chunker, "_table_variants", None)
        if _cleanup:
            chunks = tn.drop_blank_chunks(chunks)
        for chunk in chunks:
            if chunk.meta.doc_items[0].prov:
                self.page_chunk_counts[chunk.meta.doc_items[0].prov[0].page_no] += 1
        return chunks

    def split_documents_by_page(self, documents: DoclingDocument, **kwargs: dict) -> List[DocChunk]:
        """PPT 전용 페이지 기반 청킹. 본체는 facade/chunking/page_split.py 에 있다."""
        return page_split.split_documents_by_page(
            self, documents, GenosSmartChunker, min_chunk_size=_MIN_CHUNK_SIZE, **kwargs)

    def safe_join(self, iterable):
        if not isinstance(iterable, (list, tuple, set)):
            return ''
        return ''.join(map(str, iterable)) + '\n'

    def enrichment(self, document: DoclingDocument, is_ppt: bool = False, **kwargs: dict) -> DoclingDocument:
        options = self.enrichment_options
        # 런타임 toc(0/1) — config 기본값(do_toc_enrichment)을 요청별로 켜고/끈다.
        # 활성화(0→1)는 TOC endpoint 가 config 에 구성된 경우에만 유효(미구성 시 무시).
        cur_toc = bool(getattr(options, "do_toc_enrichment", False))
        want_toc = bool(_as_int_flag(kwargs.get("toc"), 1 if cur_toc else 0))
        if want_toc != cur_toc:
            if want_toc and not str(getattr(options, "toc_api_base_url", "") or ""):
                _log.warning("[convert] toc=1 요청이지만 TOC endpoint 미구성 → 무시")
            else:
                options = _copy_enrichment_options(options, do_toc_enrichment=want_toc)
                _log.info("[convert] runtime toc override → %s", want_toc)
        # PPT 는 페이지 기반 1chunk 라 목차 계층이 무의미 → TOC 만 비활성(다른 enrichment 는 유지).
        if is_ppt and getattr(options, "do_toc_enrichment", False):
            options = _copy_enrichment_options(options, do_toc_enrichment=False)
            _log.info("[convert] PPT — TOC enrichment skip")
        try:
            # 새로운 enriched result 받기
            document = enrich_document(document, options, **kwargs)
            return document
        except LLMApiError as e:
            # Preserve provider error payload as-is for load status error message.
            raise GenosServiceException("1", e.raw_error_message) from e

    def _normalize_runtime_kwargs(self, kwargs: dict) -> dict:
        return rk.normalize_runtime_kwargs(self, kwargs)

    def _configure_runtime_image_mode(self, kwargs: dict):
        rk.configure_runtime_image_mode(self, kwargs)

    def _get_or_create_image_description_enricher(self):
        enricher = getattr(self, "image_description_enricher", None)
        if enricher is None:
            # 테스트 등에서 __init__ 우회 시 legacy attribute 기반으로 재구성
            legacy_options = ImageDescriptionOptions.from_legacy_processor(self)
            enricher = ImageDescriptionEnricher(legacy_options)
            self.image_description_enricher = enricher
        return enricher

    def enrich_image_descriptions(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = self._get_or_create_image_description_enricher()
        if enricher is None:
            return document
        return enricher.enrich(document, **kwargs)

    def _get_or_create_doc_summary_enricher(self):
        enricher = getattr(self, "doc_summary_enricher", None)
        if enricher is None:
            base = getattr(self, "_base_doc_summary_options", None)
            enricher = DocSummaryEnricher(base or DocSummaryOptions())
            self.doc_summary_enricher = enricher
        return enricher

    def enrich_doc_summary(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = self._get_or_create_doc_summary_enricher()
        if enricher is None:
            return document
        return enricher.enrich(document, **kwargs)

    def _get_or_create_table_description_enricher(self):
        enricher = getattr(self, "table_description_enricher", None)
        if enricher is None:
            base = getattr(self, "_base_table_description_options", None)
            enricher = TableDescriptionEnricher(base or TableDescriptionOptions())
            self.table_description_enricher = enricher
        return enricher

    def enrich_table_descriptions(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = self._get_or_create_table_description_enricher()
        if enricher is None:
            return document
        return enricher.enrich(document, **kwargs)

    def enrich_page_descriptions(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        return inject_page_descriptions(document, self._page_desc_options)

    async def enrich_metadata(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = getattr(self, "metadata_enricher", None)
        if enricher is not None:
            document = await enricher.enrich(document, **kwargs)
        return document

    async def enrich_custom_fields(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        for enricher in self.custom_fields_enrichers:
            document = await enricher.enrich(document, **kwargs)
        return document

    async def compose_vectors(self, document: DoclingDocument, chunks: List[DocChunk], file_path: str, request: Request, **kwargs: dict) -> \
            list[dict]:
        title = ""
        _sensitive_infos: list = kwargs.get("_sensitive_infos") or []      # #315 분류 결과
        _gr_masking: bool = bool(kwargs.get("_guardrail_masking", False))   # #315 마스킹 치환 on/off
        # 벡터 생성 직전 표현 정리(text_cleanup=safe). 마스킹 뒤에 적용해야
        # 임베딩 텍스트와 n_char/n_word/n_line 통계가 일치한다.
        _cleanup_out: bool = tn.enabled_for(kwargs, self)
        # 청크 선두 "HEADER: <섹션 경로>" 부착 여부. split_documents 와 kwargs 를 각기 언패킹해서 받으므로
        # setdefault 로 전달할 수 없어 양쪽이 같은 resolver 를 호출한다.
        _include_header: bool = _resolve_include_chunk_header(kwargs, self._include_chunk_header)
        # 표 표기형태별 변형 텍스트 기록부(청커가 남긴다). off 면 None 이라 필드가 아예 생기지 않는다.
        _table_variants = getattr(self, "_table_variants", None)
        if _table_variants is not None and not _table_variants.enabled():
            _table_variants = None
        enrichment_context = kwargs.get("_enrichment_context")
        context_metadata = (
            dict(enrichment_context.get("metadata", {}))
            if isinstance(enrichment_context, dict) and isinstance(enrichment_context.get("metadata"), dict)
            else {}
        )
        document_metadata = extract_metadata_from_document(document)
        merged_metadata = dict(document_metadata)
        merged_metadata.update(context_metadata)
        # 설정 기반 typed 필드 변환 (created_date 등). source/target 키는 passthrough 에서 제외.
        typed_values, consumed_keys = apply_field_transforms(
            self._metadata_field_transforms, merged_metadata, document)

        for item, _ in document.iterate_items():
            if hasattr(item, 'label'):
                if item.label == DocItemLabel.TITLE:
                    title = item.text.strip() if item.text else ""
                    break

        passthrough_metadata = dict(merged_metadata)
        # GenOSVectorMeta 스키마 예약 필드 + transform 이 소비한 source/target 키는 passthrough 제외.
        reserved_keys = {
            "text", "n_char", "n_word", "n_line", "e_page", "i_page",
            "i_chunk_on_page", "n_chunk_of_page", "i_chunk_on_doc", "n_chunk_of_doc",
            "n_page", "reg_date", "chunk_bboxes", "media_files", "title",
            "created_date", "guardrail_categories",
        } | set(tv.field_names()) | consumed_keys
        for reserved_key in reserved_keys:
            passthrough_metadata.pop(reserved_key, None)
        passthrough_metadata = {
            key: serialize_metadata_value_for_output(value)
            for key, value in passthrough_metadata.items()
        }

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
            title=title,
        )
        global_metadata.update(typed_values)  # 설정 기반 typed 필드 (created_date 등)
        global_metadata.update(passthrough_metadata)

        # 같은 표의 조각이 연속해서 나오는 순서가 곧 조각 번호다.
        table_piece_seen: dict = {}
        current_page = None
        chunk_index_on_page = 0
        vectors = []
        upload_tasks = []
        for chunk_idx, chunk in enumerate(chunks):
            chunk_page = chunk.meta.doc_items[0].prov[0].page_no if chunk.meta.doc_items[0].prov else 0
            # 청크 선두에 섹션 경로 부착 (HEADER: ). 여기가 유일한 부착 지점이며,
            # 청커의 크기 산정도 같은 _build_header_line 을 쓴다(한도 초과 방지).
            headers_text = _build_header_line(chunk.meta.headings, _include_header)
            content = headers_text + chunk.text

            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0

            # 표 표기형태별 변형은 마스킹·정제 이전 텍스트에서 치환하고, 변형에도 같은
            # 후처리를 적용한다. 순서를 바꾸면 가드레일로 가린 값이 변형 필드로 평문 유출된다.
            variant_values = _table_variants.field_values(
                content, [getattr(item, "self_ref", "") for item in chunk.meta.doc_items],
            ) if _table_variants else {}

            # #315 가드레일 분류 후처리: quote 매칭 → guardrail_categories 부착(항상) + 마스킹 치환(옵션)
            content, chunk_cats = gr.apply_to_text(content, _sensitive_infos, _gr_masking)
            if _cleanup_out:
                content = tn.tidy(content)
            # 문서 단위 global_metadata 를 청크마다 덮어쓰지 않도록 사본에 싣는다.
            chunk_global_metadata = global_metadata
            if variant_values:
                chunk_global_metadata = dict(global_metadata)
            for field_name, variant_text in variant_values.items():
                variant_text, _ = gr.apply_to_text(variant_text, _sensitive_infos, _gr_masking)
                chunk_global_metadata[field_name] = (
                    tn.tidy(variant_text) if _cleanup_out else variant_text)

            vector = (GenOSVectorMetaBuilder()
                      .set_text(content)
                      .set_page_info(chunk_page, chunk_index_on_page, self.page_chunk_counts[chunk_page])
                      .set_chunk_index(chunk_idx)
                      .set_global_metadata(**chunk_global_metadata)
                      .set_chunk_bboxes(chunk.meta.doc_items, document)
                      .set_media_files(chunk.meta.doc_items, include_tables=self.table_image_enabled)
                      .set_table_info(chunk.meta.doc_items,
                                      getattr(self, "_table_split_totals", {}),
                                      table_piece_seen)
                      .set_guardrail_categories(sorted(chunk_cats) if chunk_cats else None)
                      ).build()
            vectors.append(vector)

            chunk_index_on_page += 1
            if upload_files:
                file_list = self.get_media_files(chunk.meta.doc_items, include_tables=self.table_image_enabled)
                upload_tasks.append(asyncio.create_task(
                    upload_files(file_list, request=request)
                ))

        if upload_tasks:
            await asyncio.gather(*upload_tasks)

        return vectors

    def compose_vectors_langchain(self, chunks, file_path: str, **kwargs: dict) -> list[dict]:
        """langchain 청크를 벡터로 변환 (PPT용)"""
        # 이 경로는 청커를 거치지 않으므로 표 출력 설정을 직접 kwargs 로 옮긴다
        # (docling 경로의 split_documents 와 같은 배선).
        cp.apply_table_output_defaults(kwargs, self)
        pdf_path = _get_pdf_path(file_path)
        doc = None
        total_pages = 0

        try:
            if os.path.exists(pdf_path):
                doc = fitz.open(pdf_path)
                total_pages = len(doc)
        except Exception as e:
            print(f"Failed to open PDF {pdf_path}: {e}")

        # 표현 정리는 fitz search_for(원문 매칭) 뒤, 벡터 생성 직전에만 적용한다.
        # 검색어를 먼저 정규화하면 PDF 원문과 달라져 bbox 매칭이 깨진다.
        _cleanup_out: bool = tn.enabled_for(kwargs, self)

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=max([chunk.metadata['page'] for chunk in chunks]),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z'
        )

        current_page = None
        chunk_index_on_page = 0
        vectors = []

        chunk_bboxes_data = []
        i_page_value = None
        e_page_value = None

        for chunk_idx, chunk in enumerate(chunks):
            page = chunk.metadata['page']
            text = chunk.page_content

            if page != current_page:
                current_page = page
                chunk_index_on_page = 0

            i_page_value = page  # 디폴트값
            e_page_value = page  # 디폴트값

            if doc and total_pages > 0:
                page_index = page - 1
                if 0 <= page_index < total_pages:
                    fitz_page = doc.load_page(page_index)
                    try:
                        from genos_utils import merge_overlapping_bboxes
                        merged_bboxes = merge_overlapping_bboxes([
                            {
                                'page': page,
                                'type': 'text',
                                'bbox': {
                                    'l': rect[0] / fitz_page.rect.width,
                                    't': rect[1] / fitz_page.rect.height,
                                    'r': rect[2] / fitz_page.rect.width,
                                    'b': rect[3] / fitz_page.rect.height,
                                }
                            } for rect in fitz_page.search_for(text)
                        ], x_tolerance=1 / fitz_page.rect.width,
                            y_tolerance=1 / fitz_page.rect.height)
                    except ImportError:
                        merged_bboxes = []
                        for rect in fitz_page.search_for(text):
                            bbox_data = {
                                'page': page,
                                'type': 'text',
                                'bbox': {
                                    'l': rect[0] / fitz_page.rect.width,
                                    't': rect[1] / fitz_page.rect.height,
                                    'r': rect[2] / fitz_page.rect.width,
                                    'b': rect[3] / fitz_page.rect.height,
                                }
                            }
                            merged_bboxes.append(bbox_data)

                    chunk_bboxes_data = merged_bboxes
                    global_metadata['chunk_bboxes'] = json.dumps(merged_bboxes)

                    if merged_bboxes:
                        bbox_pages = [bbox.get('page') for bbox in merged_bboxes if bbox.get('page') is not None]
                        if bbox_pages:
                            i_page_value = min(bbox_pages)  # 최소값
                            e_page_value = max(bbox_pages)  # 최대값

            chunk_text = tn.tidy(text) if _cleanup_out else text
            # 표기형태 필드는 정제 이전 텍스트에서 만들고 같은 후처리를 거친다(docling 경로 규칙).
            variant_values = tv.field_values_for_text(
                text, cp.resolve_table_text_formats(kwargs),
                compact_tables=cp.resolve_compact_tables(kwargs),
                tidy=tn.tidy if _cleanup_out else None)
            vectors.append(GenOSVectorMeta.model_validate({
                **variant_values,
                'has_table': tbk.has_table(chunk_text),
                'text': chunk_text,
                'n_chars': len(chunk_text),
                'n_words': len(chunk_text.split()),
                'n_lines': len(chunk_text.splitlines()),
                'i_page': i_page_value,
                'e_page': e_page_value,
                'i_chunk_on_page': chunk_index_on_page,
                'n_chunk_of_page': self.page_chunk_counts[page],
                'i_chunk_on_doc': chunk_idx,
                'chunk_bboxes': json.dumps(chunk_bboxes_data),
                'media_files': json.dumps([]),
                **global_metadata
            }))
            chunk_index_on_page += 1

        if doc:
            doc.close()

        return vectors

    async def _extract_page_images(self, pdf_path: str, request: Request) -> dict[int, list[dict]]:
        if not os.path.exists(pdf_path):
            return {}

        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            print(f"Failed to open PDF {pdf_path}: {e}")
            return {}
        file_list: list[dict] = []
        page_meta: dict[int, list[dict]] = defaultdict(list)

        for page_index in range(len(doc)):
            page = doc.load_page(page_index)
            for img_idx, img in enumerate(page.get_images(full=True)):
                try:
                    xref = img[0]
                    pix = fitz.Pixmap(doc, xref)

                    # Convert to RGB if needed
                    if pix.n >= 5:  # CMYK
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    elif pix.n == 4:  # RGBA
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    elif pix.alpha:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    elif pix.n < 3:  # Grayscale
                        pix = fitz.Pixmap(fitz.csRGB, pix)

                    img_name = f"{uuid.uuid4()}.png"
                    img_path = os.path.join("/tmp", img_name)

                    pix.save(img_path)
                except Exception as e:
                    print(f"Failed to save image: {e}")
                    continue
                finally:
                    pix = None  # Free memory

        if file_list and upload_files:
            await upload_files(file_list, request=request)

        doc.close()
        return page_meta

    def _save_table_images(
        self,
        document: DoclingDocument,
        image_dir: Path,
        reference_path: Optional[Path] = None,
    ) -> None:
        dops.save_table_images(document, image_dir, reference_path)

    def get_media_files(self, doc_items: list, include_tables: bool = False):
        return dops.get_media_files(doc_items, include_tables)

    def check_glyph_text(self, text: str, threshold: int = 1) -> bool:
        return dops.check_glyph_text(text, threshold)

    def check_glyphs(self, document: DoclingDocument) -> bool:
        return dops.check_glyphs(document, self._glyph_document_threshold)

    def ocr_all_table_cells(self, document: DoclingDocument, pdf_path) -> DoclingDocument:
        """글리프 깨진 텍스트가 있는 표에 대해서만 셀 단위 재OCR 을 수행한다."""
        return dops.ocr_all_table_cells(
            document,
            ocr_endpoint=self.ocr_endpoint,
            cell_threshold=self._glyph_table_cell_threshold,
            timeout=self._table_cell_ocr_timeout,
        )

    def setup_logging(self, level_num: int):
        rt.setup_logging(level_num)

    async def _process_request(self, request: Request, file_path: str, **kwargs: dict):
        runtime_level = kwargs.get('log_level')
        self.setup_logging(runtime_level if runtime_level is not None else self._log_level)

        # 런타임 토글(img_desc/chart_desc/chart_detection/doc_summary)로 이미지·차트 description 재구성
        kwargs = self._normalize_runtime_kwargs(kwargs)
        self._configure_runtime_image_mode(kwargs)

        _log.info(f"file_path: {file_path}")
        _log.info(f"kwargs: {kwargs}")

        ext = Path(file_path).suffix.lower()

        # 직접 처리 가능한 엑셀 계열 포맷(이슈 #288): xlsx/xlsm + csv(본질적 tabular 이라 항상 직접 처리).
        # 포맷별 처리: 엑셀 계열은 직접 처리, ppt 는 langchain, 그 외는 docling.
        if ext in _XLSX_DIRECT_EXTS:
            return await self._process_xlsx(request, file_path, **kwargs)
        if ext == ".ppt":
            return await self._process_ppt(request, file_path, **kwargs)
        return await self._process_docling(request, file_path, **kwargs)

    async def _process_xlsx(self, request: Request, file_path: str, **kwargs: dict):
        """xlsx/csv 직접 처리(이슈 #288): PDF 변환 없이 처리해 행 분할 버그 방지.
          - tabular: 데이터 행마다 1청크(벡터)로 만들어 즉시 반환
          - docling(기본): MsExcel 백엔드로 DoclingDocument 생성 후 공유 파이프라인으로 합류
        """
        from genon.preprocessor.converters.xlsx_processor import (
            build_docling_document,
            build_tabular_custom_fields_vectors,
            build_tabular_vectors,
        )
        # enrichment.custom_fields 의 tabular_mapping handler 가 요청 doc_type 과 일치하면 행별 custom_fields
        # 벡터로 처리(LLM 미호출). processing_mode 와 무관하게 우선한다(행별 매핑이 목적).
        runtime_doc_type = normalize_doc_type(kwargs.get("doc_type"))
        matching_mappers = [
            m for m in self._tabular_custom_fields_mappers if m.matches(runtime_doc_type)
        ]
        if len(matching_mappers) > 1:
            raise GenosServiceException(
                "1", f"동일 doc_type 에 tabular custom_fields 설정이 여러 개입니다: {runtime_doc_type}"
            )
        if matching_mappers:
            _log.info(f"[DocumentProcessor] xlsx tabular custom_fields 처리(doc_type={runtime_doc_type}): {file_path}")
            try:
                vectors = build_tabular_custom_fields_vectors(
                    file_path, matching_mappers[0], runtime_doc_type,
                    header_row=self._xlsx_cfg["header_row"],
                    multi_table=self._xlsx_cfg["multi_table"],
                    expand_elements=(
                        tbk.expand_elements
                        if cp.resolve_table_as_chunk(
                            kwargs, getattr(self, "_table_as_chunk", True)) else None
                    ),
                    text_fields_hook=tv.text_fields_hook(
                        getattr(self, "_table_text_formats", ()),
                        compact_tables=getattr(self, "_compact_tables", True)),
                )
            except (FileNotFoundError, TypeError, ValueError) as exc:
                raise GenosServiceException("1", str(exc)) from exc
            if not vectors:
                raise GenosServiceException("1", "chunk length is 0")
            return vectors

        if self._xlsx_cfg["processing_mode"] == "tabular":
            _log.info(f"[DocumentProcessor] xlsx tabular 직접 처리: {file_path}")
            vectors = build_tabular_vectors(
                file_path,
                header_row=self._xlsx_cfg["header_row"],
                multi_table=self._xlsx_cfg["multi_table"],
            )
            if not vectors:
                raise GenosServiceException("1", f"chunk length is 0")
            return vectors

        _log.info(f"[DocumentProcessor] xlsx docling 직접 처리: {file_path}")
        try:
            document = build_docling_document(
                file_path, save_images=kwargs.get('save_images', False)
            )
        except Exception as e:
            raise GenosServiceException(
                "1", f"xlsx 처리 실패: {os.path.basename(file_path)} ({e})"
            )
        # openpyxl 텍스트라 글리프 깨짐이 없고 렌더 PDF 도 없으므로 테이블셀 재OCR 은 생략.
        # 시트/표마다 별도 청크가 되는 것은 이제 청커 기본값(table_as_chunk)이 보장한다.
        return await self._document_to_vectors(
            document, file_path, request, ocr_table_cells=False, **kwargs
        )

    async def _process_ppt(self, request: Request, file_path: str, **kwargs: dict):
        """PPT(.ppt)는 langchain 로더로 처리하고 페이지 이미지 메타를 부착한다."""
        documents = self.load_documents_langchain(file_path, **kwargs)
        chunks = self.split_documents_langchain(documents, **kwargs)

        pdf_path = _get_pdf_path(file_path)
        page_image_meta = {}
        try:
            page_image_meta = await self._extract_page_images(pdf_path, request)
        except:
            pass

        vectors = self.compose_vectors_langchain(chunks, file_path, **kwargs)
        for v in vectors:
            if v.i_page in page_image_meta:
                v.media_files = json.dumps(page_image_meta[v.i_page], ensure_ascii=False)
            else:
                v.media_files = json.dumps([])
        return vectors

    async def _process_docling(self, request: Request, file_path: str, **kwargs: dict):
        """PDF/DOCX/PPTX/HWP/기타를 docling 으로 로딩 후 공유 파이프라인으로 처리."""
        ext = Path(file_path).suffix.lower()
        document = self._load_document(file_path, **kwargs)

        # DOCX/PPTX 는 미리보기용 PDF 아티팩트를 생성한다(부수효과; 처리 결과엔 미사용).
        if ext in ['.docx', '.pptx']:
            convert_to_pdf(file_path, use_pdf_sdk=kwargs.get('use_pdf_sdk', True))

        # .pptx 는 페이지 단위 처리(페이지 설명 주입 + 페이지 기반 청킹 + TOC skip).
        return await self._document_to_vectors(
            document, file_path, request, ocr_table_cells=(ext == '.pdf'),
            is_ppt=(ext == '.pptx'), **kwargs
        )

    def _load_document(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        """docling 문서 로딩. pdf 는 ocr_mode 분기, 그 외 포맷은 기본 백엔드로 로딩한다.
        ocr_mode: "force"=무조건 전체 OCR / "auto"=휴리스틱 기반 재OCR / "disable"=OCR 안 함
        """
        ext = Path(file_path).suffix.lower()
        if ext != '.pdf':
            return self.load_documents(file_path, **kwargs)

        if self.ocr_mode == "force":
            return self.load_documents_with_docling_ocr(file_path, **kwargs)
        document: DoclingDocument = self.load_documents(file_path, **kwargs)
        if self.ocr_mode == "auto":
            if not check_document(document, self.enrichment_options) or self.check_glyphs(document):
                # OCR이 필요하다고 판단되면 OCR 수행
                document = self.load_documents_with_docling_ocr(file_path, **kwargs)
        return document

    async def _document_to_vectors(self, document: DoclingDocument, file_path: str,
                                   request: Request, *, ocr_table_cells: bool,
                                   is_ppt: bool = False, **kwargs: dict) -> list:
        """DoclingDocument → enrichment → 청킹 → 벡터 생성(공유 파이프라인).

        ocr_table_cells: 글리프 깨진 테이블 셀 재OCR 수행 여부(pdf 만 True).
        """
        # 글리프 깨진 텍스트가 있는 테이블에 대해서만 OCR 수행 (청크토큰 8k이상 발생 방지)
        if ocr_table_cells and self.ocr_mode != "disable" and self.ocr_endpoint:
            document = self.ocr_all_table_cells(document, file_path)

        output_path, output_file = os.path.split(file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(output_path) / filename  # 빈 output_path 가 절대경로(/filename)로 바뀌는 것 방지
        if artifacts_dir.is_absolute():
            reference_path = None
        else:
            reference_path = artifacts_dir.parent

        document = document._with_pictures_refs(image_dir=artifacts_dir, page_no=None, reference_path=reference_path)

        # 표 이미지 저장 옵션이 켜진 경우, picture 와 동일하게 표 영역을 PNG 로 저장하고
        # TableItem.image.uri 를 설정한다(_with_pictures_refs 미러).
        if self.table_image_enabled:
            self._save_table_images(document, image_dir=artifacts_dir, reference_path=reference_path)

        document = self.enrichment(document, is_ppt=is_ppt, **kwargs)
        enrichment_kwargs = dict(kwargs)
        enrichment_kwargs["_enrichment_context"] = {}
        try:
            document = self.enrich_doc_summary(document, **enrichment_kwargs)
        except Exception as exc:
            _log.warning(f"[DocumentProcessor] facade doc_summary enrichment skipped: {exc}")
        try:
            document = self.enrich_image_descriptions(document, **enrichment_kwargs)
        except Exception as exc:
            _log.warning(f"[DocumentProcessor] facade image enrichment skipped: {exc}")
        # 표 설명(독립 → 융합 → 이미지). 판정은 공용 모듈 한 곳에 있다.
        def _skip_table_stage(exc: Exception, stage: str) -> None:
            _log.warning(f"[DocumentProcessor] facade {stage} skipped: {exc}")

        document = await apply_table_description_stage(
            document,
            custom_fields_enrichers=self.custom_fields_enrichers,
            standalone=getattr(self, "table_text_description_enricher", None),
            run_image_stage=self.enrich_table_descriptions,
            handle_error=_skip_table_stage,
            kwargs=enrichment_kwargs,
        )
        # 페이지 단위 image description 은 PPT(.pptx) 원본에만 적용.
        if is_ppt:
            try:
                document = self.enrich_page_descriptions(document, **enrichment_kwargs)
            except Exception as exc:
                _log.warning(f"[DocumentProcessor] page image enrichment skipped: {exc}")
        try:
            document = await self.enrich_metadata(document, **enrichment_kwargs)
        except Exception as exc:
            _log.warning(f"[DocumentProcessor] metadata enrichment skipped: {exc}")
        try:
            document = await self.enrich_custom_fields(document, **enrichment_kwargs)
        except Exception as exc:
            _log.warning(f"[DocumentProcessor] custom_fields enrichment skipped: {exc}")
        # doc_type 스탬프(예: card): 요청 kwargs 로 doc_type 이 오면 문서 메타에 저장 → compose_vectors 가
        # 모든 청크에 broadcast(+ context metadata 노출). (faq 는 xlsx tabular 경로에서 별도 처리)
        doc_type = normalize_doc_type(kwargs.get("doc_type"))
        if doc_type:
            try:
                store_metadata_in_document(document, {"doc_type": doc_type})
                ctx = enrichment_kwargs.get("_enrichment_context")
                if isinstance(ctx, dict):
                    ctx.setdefault("metadata", {})["doc_type"] = doc_type
            except Exception as exc:
                _log.warning(f"[DocumentProcessor] doc_type stamp skipped: {exc}")

        # 민감정보 분류(#315): 청킹 전, 문서 전체를 분류 워크플로우에 1회 호출 → sensitive_infos.
        sensitive_infos: list = []
        if gr.call_enabled(kwargs):
            sensitive_infos = gr.classify_document(
                gr.doc_text(document), self._guardrail_url, self._guardrail_workflow_id,
                self._guardrail_api_key, self._guardrail_timeout,
            )

        # Extract Chunk from DoclingDocument. PPT 는 페이지 기반 청킹(1 page 1 chunk).
        if is_ppt:
            chunks: List[DocChunk] = self.split_documents_by_page(document, **kwargs)
        else:
            chunks: List[DocChunk] = self.split_documents(document, **kwargs)

        if len(chunks) >= 1:
            vectors: list[dict] = await self.compose_vectors(
                document,
                chunks,
                file_path,
                request,
                _sensitive_infos=sensitive_infos,
                _guardrail_masking=(gr.call_enabled(kwargs) and self._guardrail_masking_enabled),
                **enrichment_kwargs,
            )
        else:
            raise GenosServiceException("1", f"chunk length is 0")

        return vectors

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        # #329: LLM 캐시 컨텍스트를 요청 스코프로 설정(최외곽이라 HWP PDF 폴백 재처리까지 커버).
        #   params.llm_cache + workflow_id + interim_root(or env INTERIM_ROOT) 가 모두 있을 때만
        #   캐시 동작. 미지정 시 기존과 완전히 동일(no-op). ThreadPool 워커엔 in_current_context 로 전파.
        _cache_token = _set_cache_context(_resolve_cache_context(kwargs))
        try:
            # HWP/HWPX: docling(SDK + 레거시 백엔드) 처리가 전체 실패하면 PDF 변환으로 최종 폴백한다.
            # attachment_processor.DocumentProcessor.__call__ 의 PDF 폴백과 동일 취지 —
            # convert_to_pdf 는 rhwp와 LibreOffice의 HWP-PDF 변환 체인이며, 변환된 PDF를 PDF 경로로 재처리한다.
            # (헌법.hwp 처럼 SDK 가 exit 3 으로 거부하거나 02.hwp 처럼 빈 결과를 내는 경우를 살린다.)
            # 비정상/암호화 파일 사전 감지(이슈 #278/#307): 지원 포맷 매직헤더에 하나도 안 맞고
            # 텍스트도 아니면(=DRM 암호화/손상 바이너리) 파싱/변환 단계의 garbage 처리를 유발하므로
            # 진입부에서 컷한다. 확장자와 무관하게 실제 헤더로 판정.
            bad_reason = _detect_unsupported_file(file_path)
            if bad_reason:
                _log.warning(f"[convert] 비정상 파일 감지({bad_reason}) — 처리 중단: {file_path}")
                raise GenosServiceException(
                    "1", f"{bad_reason} 입니다. 정상 문서로 다시 업로드하세요: {os.path.basename(file_path)}"
                )

            ext = Path(file_path).suffix.lower()
            # .hml(HWPML)은 hwp_sdk 260713+ 에서 지원 — 같은 SDK 경로로 라우팅 (이슈 #323)
            if ext in ('.hwp', '.hwpx', '.hml'):
                try:
                    return await self._process_request(request, file_path, **kwargs)
                except Exception as hwp_err:
                    _log.warning(f"[DocumentProcessor] HWP/HWPX 처리 실패, PDF 변환 폴백 시도: {hwp_err}")
                    converted = convert_to_pdf(file_path, use_pdf_sdk=kwargs.get('use_pdf_sdk', True))
                    if converted:
                        _log.info(f"[DocumentProcessor] PDF 변환 성공, PDF 경로로 재처리: {converted}")
                        return await self._process_request(request, converted, **kwargs)
                    raise hwp_err
            return await self._process_request(request, file_path, **kwargs)
        finally:
            _log_cache_summary()
            _reset_cache_context(_cache_token)


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
