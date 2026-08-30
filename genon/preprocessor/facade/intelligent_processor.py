# 적재용(지능형) 전처리기 v.2.2.4 (2026-07-30 Release)
from __future__ import annotations

import json
import os
import logging
import math, bisect
import yaml
from pathlib import Path

from collections import defaultdict
from datetime import datetime
from typing import Optional, Iterable, Any, List, Dict, Tuple

from fastapi import Request

_log = logging.getLogger(__name__)

# Genos 웹 UI 환경은 facade 코드를 단일 파일(preprocessor.py)로 처리하므로
# 다른 facade 파일에서 import 가 깨진다. 따라서 convert_to_pdf 는
# attachment_processor / convert_processor 와 동일하게 자체 정의한다.
import shutil
import subprocess
import tempfile

import httpx


def convert_to_pdf(file_path: str, use_pdf_sdk: bool = True) -> str | None:
    """
    PDF 변환을 시도한다. 실패해도 예외를 던지지 않고 None을 반환한다.

    chain (HWP/HWPX 입력):
      use_pdf_sdk=True  → pdf_sdk → rhwp → libreoffice
      use_pdf_sdk=False → rhwp → libreoffice
    chain (그 외 입력, 예: docx/pptx):
      use_pdf_sdk=True  → pdf_sdk → libreoffice
      use_pdf_sdk=False → libreoffice

    rhwp 는 HWP/HWPX 전용이라 비-HWP 입력에는 chain 에 들어가지 않는다. HWP/HWPX
    변환은 rhwp 를 libreoffice 보다 우선한다 (pdf_sdk 가 있으면 그 다음 순위).
    내부 구현은 `genon.preprocessor.converters.hwp_to_pdf` 모듈에 통합되어 있다.
    """
    from genon.preprocessor.converters.hwp_to_pdf import convert_hwp_to_pdf
    ext = os.path.splitext(file_path)[1].lower()
    is_hwp = ext in (".hwp", ".hwpx")
    if use_pdf_sdk:
        order = ["pdf_sdk", "rhwp", "libreoffice"] if is_hwp else ["pdf_sdk", "libreoffice"]
    else:
        order = ["rhwp", "libreoffice"] if is_hwp else ["libreoffice"]
    return convert_hwp_to_pdf(file_path, order=order)

# ── 공용 하위 모듈로 옮긴 헬퍼들의 별칭 ──────────────────────────────
# 구현은 facade/common/, facade/chunking/ 에 한 벌만 둔다. 여기서는 기존 이름을
# 그대로 유지해 호출부를 건드리지 않는다. 사이트별 조정 대상 상수(구분자, 최소
# 청크 크기, 토크나이저 경로)는 이 파일에 남아 있으므로 래퍼가 넘겨준다.
from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.chunking import smart_chunker as sc
from genon.preprocessor.facade.common import vector_meta as vm
from genon.preprocessor.facade.common import docling_ops as dops
from genon.preprocessor.facade.common import runtime as rt
from genon.preprocessor.facade.common import file_probe as fp
from genon.preprocessor.facade.chunking import header_path as hp

_as_dict = cp.as_dict
_as_int_flag = cp.as_int_flag
_copy_enrichment_options = cp.copy_enrichment_options
_detect_unsupported_file = fp.detect_unsupported_file
_filename_title_candidates = hp.filename_title_candidates
_is_encrypted_office = fp.is_encrypted_office
_is_encrypted_pdf = fp.is_encrypted_pdf
_is_pdf = fp.is_pdf
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



def _has_any_pdf_converter() -> bool:
    """PDF 변환 backend(pdf_sdk / rhwp / libreoffice) 가 하나라도 가용한지 확인 (이슈 #286).

    빌드 시 INSTALL_LIBREOFFICE / INSTALL_RHWP 를 끄거나 PDF SDK 미포함(standard)이면
    변환 backend 가 0개가 될 수 있다. 이때 비-PDF 입력을 변환 시도하면 무조건 실패하므로,
    호출부에서 "PDF 로 직접 입력" 안내를 주기 위한 판별 헬퍼.
    가용성 판단 자체가 불가하면(import 실패 등) True 를 반환해 기존 동작을 유지한다.
    """
    try:
        from genon.preprocessor.converters.hwp_to_pdf.availability import (
            libreoffice_available,
            pdf_sdk_available,
            rhwp_available,
        )
        return bool(pdf_sdk_available() or rhwp_available() or libreoffice_available())
    except ImportError:
        # facade 단일 파일 실행 등으로 모듈 import 가 안 되는 경우 → 기존 동작 유지(가용 가정)
        return True
    except Exception as exc:
        # 가용성 probe 자체가 예기치 못하게 실패하면 로그만 남기고 파이프라인은 막지 않는다
        _log.warning(f"[_has_any_pdf_converter] PDF 변환기 가용성 확인 실패: {exc}")
        return True














# docling imports

from docling.datamodel.base_models import InputFormat
from docling.pipeline.simple_pipeline import SimplePipeline
# from docling.datamodel.document import ConversionStatus
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    # OcrEngine,
    # PdfBackend,
    LayoutModelType,
    PdfPipelineOptions,
    TableFormerMode,
    TableStructureModelType,
    PipelineOptions,
    UpstageOcrOptions,
)

from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    FormatOption
)
from docling.datamodel.pipeline_options import DataEnrichmentOptions
from docling.prompts.prompt_manager import LLMApiError
from docling.utils.document_enrichment import enrich_document, check_document
from docling.utils.llm_cache import (
    classify_error as _classify_error,
    current_context as _cache_current_context,
    log_summary as _log_cache_summary,
    reset_context as _reset_cache_context,
    resolve_context as _resolve_cache_context,
    set_context as _set_cache_context,
)
from docling.datamodel.document import ConversionResult
from docling_core.transforms.chunker import (
    BaseChunk,
    BaseChunker,
    DocChunk,
    DocMeta,
)
from docling_core.transforms.serializer.markdown import (
    MarkdownDocSerializer,
    MarkdownParams,
)
from docling_core.types import DoclingDocument

from pandas import DataFrame
import asyncio
from docling_core.types import DoclingDocument as DLDocument
from docling_core.types.doc.document import (
    DocumentOrigin,
    LevelNumber,
    ListItem,
    CodeItem,
    ContentLayer,
)
from docling_core.types.doc.labels import DocItemLabel
from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    DocumentOrigin,
    DocItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    PageItem,
    ProvenanceItem
)
from docling.datamodel.settings import settings

from collections import Counter
import re
import json
import time
import warnings
from typing import Iterable, Iterator, Optional, Union

from pydantic import BaseModel, ConfigDict, PositiveInt, TypeAdapter, model_validator
from typing_extensions import Self

from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
    build_document_custom_fields_enrichers as _build_document_custom_fields_enrichers,
    normalize_doc_type,
)
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    build_tabular_custom_fields_mappers as _build_tabular_custom_fields_mappers,
    warn_tabular_llm_fields_unsupported as _warn_tabular_llm_fields_unsupported,
)
from genon.preprocessor.facade.enrichment.metadata_enricher import (
    MetadataEnricher as _MetadataEnricher,
)

from genon.preprocessor.facade.enrichment.enrichment_config import EnrichmentConfig
from genon.preprocessor.facade.enrichment.page_description import (
    PageDescriptionOptions,
    collect_page_texts,
    describe_pages,
)
from genon.preprocessor.facade.enrichment.image_description import (
    ImageDescriptionOptions,
    ImageDescriptionEnricher,
    resolve_runtime_image_options,
)
from genon.preprocessor.facade.enrichment.table_description import (
    TableDescriptionOptions,
    TableDescriptionEnricher,
    TableDescriptionExtractor,
    refined_html_to_format,
    resolve_runtime_table_options,
)
from genon.preprocessor.facade.enrichment.doc_summary import (
    DocSummaryOptions,
    DocSummaryEnricher,
    resolve_runtime_doc_summary_options,
)
from genon.preprocessor.facade.enrichment.field_transforms import (
    DEFAULT_METADATA_FIELD_TRANSFORMS,
    apply_field_transforms,
    extract_metadata_from_document,
    serialize_metadata_value_for_output,
    store_metadata_in_document,
)
from genon.preprocessor.facade.chunking.table_splitter import (
    leading_header_row_count,
    split_entries_preserving_tables,
    split_table_rows,
)
from genon.preprocessor.facade.chunking import text_norm as tn

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

# HWP/HWPX 품질 복구(선택적). 모듈 로드 실패 시 None → 복구 미적용(기존 동작 유지).
try:
    from genon.preprocessor.converters.hwp_recovery import HwpQualityRecovery
except ImportError:
    HwpQualityRecovery = None


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












# #329: LLM 캐시 / error_policy 컨텍스트 해석은 docling.utils.llm_cache.resolve_context
# (3개 facade 공용, cross-facade import 회피)를 _resolve_cache_context 로 재노출해 사용한다.


def _handle_stage_error(exc: Exception, stage: str) -> None:
    """enrichment 단계 실패 처리(#329).

    - lenient(기본): 기존처럼 warning 후 계속(soft-fail, 하위호환).
    - strict: stage/error_type 를 실어 GenosServiceException 으로 재-raise(Temporal 경로).
    error_policy 는 요청 스코프 CacheContext 에서 읽는다(단일 소스).
    """
    error_type = _classify_error(exc)
    if _cache_current_context().error_policy == "strict":
        raise GenosServiceException(
            "1", f"[{stage}] {exc}", stage=stage, error_type=error_type
        ) from exc
    _log.warning(f"[DocumentProcessor] {stage} enrichment skipped ({error_type}): {exc}")


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


def _resolve_default_intelligent_config_path() -> str:
    base_dir = Path(__file__).resolve().parent
    local_config = (base_dir / "../resource_dev/intelligent_processor_config.yaml").resolve()
    default_config = (base_dir / "../resource/intelligent_processor_config.yaml").resolve()

    if local_config.exists():
        return str(local_config)
    return str(default_config)


# 청킹용 토크나이저 기본 경로 (config 미지정 시 현행 동작 유지)
_DEFAULT_TOKENIZER_LOCAL_PATH = "/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2"
_DEFAULT_TOKENIZER_ID = "sentence-transformers/all-MiniLM-L6-v2"

# PDF 변환에서 제외(직접 처리)할 엑셀 계열 포맷(이슈 #288).
# PDF 변환 시 한 행이 페이지 경계로 쪼개지는 논리 오류가 생기므로 변환하지 않고 직접 처리한다.
_XLSX_DIRECT_EXTS = {".xlsx", ".xlsm", ".csv"}




# ============================================
#
# Copyright IBM Corp. 2024 - 2024
# SPDX-License-Identifier: MIT
#

"""Chunker implementation leveraging the document structure."""

class GenosSmartChunker(sc.SmartChunkerBase):
    """청킹 본체는 facade/chunking/smart_chunker.py 에 있다.

    여기에는 이 facade 가 고른 동작 옵션과 헤더 구분자만 둔다. 값을 바꾸면 청킹
    동작이 바로 달라지므로, 사이트에서 손댈 지점은 사실상 이 블록이다.
    """

    # 그림 annotation 텍스트를 청크 본문에 싣는다.
    PICTURE_ANNOTATION_TEXT = True
    # 표 설명 annotation 반영 범위: refine HTML + 검색 설명 접두 + 요약 접미.
    TABLE_DESCRIPTION_MODE = "full"
    # xlsx 유래 문서를 table_as_chunk 로 자동 전환한다 (요청 kwargs 로만 켠다).
    AUTO_TABLE_AS_CHUNK_FOR_SHEETS = False

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
    title: str = None
    created_date: int = None
    appendix: str = None ## !! appendix feature (2025-09-30, geonhee kim) !!
    file_path: Optional[str] = None
    guardrail_categories: Optional[list] = None  # #315 민감정보 분류 라벨(부동산/인사/민감 등). 미적용 시 None


class GenOSVectorMetaBuilder(vm.VectorMetaBuilderBase):
    """공통 세터(텍스트 통계·페이지·bbox·미디어·글로벌 메타데이터)는
    facade/common/vector_meta.py 에 있다. 여기에는 이 facade 고유 필드만 둔다."""

    def __init__(self):
        """빌더 초기화"""
        super().__init__()
        self.title: Optional[str] = None
        self.created_date: Optional[int] = None
        self.appendix: Optional[str] = None # !! appendix feature (2025-09-30, geonhee kim) !!
        self.file_path: Optional[str] = None

    def build(self) -> GenOSVectorMeta:
        """설정된 데이터를 사용해 최종적으로 GenOSVectorMeta 객체 생성"""
        payload = {
            **self.core_payload(),
            "title": self.title,
            "created_date": self.created_date,
            "appendix": self.appendix or "", # !! appendix feature (2025-09-30, geonhee kim) !!
            "file_path": self.file_path,
            **self.extra_metadata,
        }
        return GenOSVectorMeta.model_validate(payload)


class DocumentProcessor:

    def __init__(self, config_path: str | None = None):
        '''
        initialize Document Converter (config 기반)

        config_path 가 None 이면 resource_dev/intelligent_processor_config.yaml
        (없으면 resource/intelligent_processor_config.yaml) 을 사용한다.
        GenOS 는 DocumentProcessor() 무인자로 호출하므로 기본 경로 resolve 필수.
        '''
        if config_path is None:
            config_path = _resolve_default_intelligent_config_path()

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

        # 청크 텍스트 정규화(chunking.text_cleanup): "off"(기본) | "safe".
        # safe 면 청킹 입력에 문자 위생(tn.sanitize)을, 벡터 생성 직전에 표현 정리(tn.tidy)를 적용한다.
        # 우선순위: kwargs.text_cleanup > 아래 > "off".
        self._text_cleanup = tn.mode_from_cfg(chunking_cfg)

        # xlsx(엑셀) 직접 처리 설정(이슈 #288). formats.xlsx 아래에 둔다(포맷별 옵션 컨테이너).
        #   processing_mode: docling(기본)=MsExcel 백엔드로 DoclingDocument 후 기존 파이프라인 /
        #                    tabular=데이터 행마다 1벡터 + 컬럼 헤더→메타(병합셀 unmerge+ffill)
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
        table_format = str(output_cfg.get("table_format", "html")).strip().lower()
        if table_format not in {"html", "markdown"}:
            _log.warning(
                f"[DocumentProcessor] Unknown output.table_format '{table_format}', fallback to 'html'."
            )
            table_format = "html"
        self._table_format = table_format
        # markdown 표 compact(컬럼 정렬 패딩 제거) 여부. 기본 True. html 포맷엔 무관.
        self._compact_tables = bool(output_cfg.get("compact_tables", True))

        # OCR 엔드포인트는 ocr.paddle.ocr_endpoint 가 정식 위치.
        # 구버전 호환: ocr.ocr_endpoint(상위) / 최상위 ocr_endpoint 도 폴백으로 인식.
        paddle_cfg = _as_dict(ocr_cfg.get("paddle"))
        ocr_ep = (
            paddle_cfg.get("ocr_endpoint")
            or ocr_cfg.get("ocr_endpoint")
            or cfg.get("ocr_endpoint", "http://192.168.73.172:48080/ocr")
        )

        # OCR 수행 모드. "auto"(default)=휴리스틱 기반 재OCR / "force"=무조건 전체 OCR / "disable"=OCR 안 함
        raw_ocr_mode = str(ocr_cfg.get("ocr_mode", cfg.get("ocr_mode", "auto"))).lower().strip()
        if raw_ocr_mode not in {"auto", "force", "disable"}:
            _log.warning(f"[DocumentProcessor] Unknown ocr_mode '{raw_ocr_mode}', fallback to 'auto'")
            raw_ocr_mode = "auto"
        self.ocr_mode = raw_ocr_mode

        # 테이블 셀 재OCR HTTP timeout (ocr_all_table_cells). 잘못된 값은 60 으로 폴백.
        table_cell_ocr_timeout = _parse_optional_int(
            ocr_cfg.get("table_cell_ocr_timeout"), "ocr.table_cell_ocr_timeout"
        )
        self._table_cell_ocr_timeout = (
            table_cell_ocr_timeout if table_cell_ocr_timeout and table_cell_ocr_timeout > 0 else 60
        )

        # 글리프 기반 auto-OCR 재트리거 임계값.
        glyph_cfg = _as_dict(ocr_cfg.get("glyph_detection"))
        glyph_cell_th = _parse_optional_int(
            glyph_cfg.get("table_cell_threshold"), "ocr.glyph_detection.table_cell_threshold"
        )
        self._glyph_table_cell_threshold = glyph_cell_th if glyph_cell_th and glyph_cell_th > 0 else 1
        glyph_doc_th = _parse_optional_int(
            glyph_cfg.get("document_threshold"), "ocr.glyph_detection.document_threshold"
        )
        self._glyph_document_threshold = glyph_doc_th if glyph_doc_th and glyph_doc_th > 0 else 10

        ocr_options = self._build_ocr_options(ocr_cfg, paddle_endpoint=ocr_ep)
        if isinstance(ocr_options, UpstageOcrOptions):
            self.ocr_endpoint = ocr_options.api_endpoint
        else:
            self.ocr_endpoint = ocr_ep

        # 민감정보 분류/마스킹(#315): GenOS 분류 워크플로우 접속 정보.
        # 기능 on/off 는 요청별 kwargs(guardrail_call), 마스킹 치환은 masking_enabled(config/kwargs).
        gm_cfg = _as_dict(cfg.get("guardrail"))
        self._guardrail_url = str(gm_cfg.get("url") or "").strip()
        self._guardrail_workflow_id = _parse_optional_int(gm_cfg.get("workflow_id"), "guardrail.workflow_id")
        self._guardrail_api_key = str(gm_cfg.get("api_key") or "").strip()
        gm_timeout = _parse_optional_int(gm_cfg.get("timeout"), "guardrail.timeout")
        self._guardrail_timeout = gm_timeout if gm_timeout and gm_timeout > 0 else 60
        self._guardrail_masking_enabled = bool(_parse_optional_bool(gm_cfg.get("masking_enabled"), "guardrail.masking_enabled"))

        self.page_chunk_counts = defaultdict(int)

        device_str = str(pdf_cfg.get("device", "auto")).lower().strip()
        device = _ACCELERATOR_DEVICE_MAP.get(device_str)
        if device is None:
            _log.warning(f"[DocumentProcessor] Unknown pdf_pipeline.device '{device_str}', fallback to 'auto'")
            device = AcceleratorDevice.AUTO

        num_threads = _parse_optional_int(pdf_cfg.get("num_threads"), "pdf_pipeline.num_threads")
        if num_threads is None or num_threads <= 0:
            num_threads = 8
        accelerator_options = AcceleratorOptions(num_threads=num_threads, device=device)

        images_scale = _parse_optional_int(pdf_cfg.get("images_scale"), "pdf_pipeline.images_scale")
        if images_scale is None or images_scale <= 0:
            images_scale = 2

        generate_page_images = _parse_optional_bool(
            pdf_cfg.get("generate_page_images"), "pdf_pipeline.generate_page_images"
        )
        generate_picture_images = _parse_optional_bool(
            pdf_cfg.get("generate_picture_images"), "pdf_pipeline.generate_picture_images"
        )

        # 표 이미지(table_image) 옵션: 표를 picture 와 동일하게 이미지로 잘라 저장하고,
        # media_files 에 type='table_image' 로 기록한다(검색=청크 텍스트 / 답변=표 이미지).
        # 기본 False 라 미설정 시 기존 동작과 동일(하위 호환).
        table_image_cfg = _as_dict(cfg.get("table_image"))
        self.table_image_enabled = bool(
            _parse_optional_bool(table_image_cfg.get("enable"), "table_image.enable")
        )

        # PPT 페이지 단위 image description(page-level) 옵션. 기존 PictureItem 단위 설명과 별개로,
        # "페이지 자체"를 렌더링해 설명한 텍스트를 페이지별 TextItem 으로 주입한다(PPT 원본 전용).
        # config 위치: formats.ppt.page_description. 공통 모듈(enrichment/page_description)로 파싱.
        ppt_fmt_cfg = _as_dict(formats_cfg.get("ppt"))
        page_img_cfg = _as_dict(ppt_fmt_cfg.get("page_description"))
        self._page_desc_options = PageDescriptionOptions.from_config(page_img_cfg, self._config_dir)

        table_mode_str = str(pdf_cfg.get("table_structure_mode", "accurate")).lower().strip()
        table_structure_mode = _TABLE_FORMER_MODE_MAP.get(table_mode_str)
        if table_structure_mode is None:
            _log.warning(
                f"[DocumentProcessor] Unknown pdf_pipeline.table_structure_mode '{table_mode_str}', fallback to 'accurate'"
            )
            table_structure_mode = TableFormerMode.ACCURATE

        # PDF 파이프라인 옵션 설정
        self.pipe_line_options = PdfPipelineOptions()
        self.pipe_line_options.generate_page_images = (
            True if generate_page_images is None else generate_page_images
        )
        self.pipe_line_options.generate_picture_images = (
            True if generate_picture_images is None else generate_picture_images
        )
        # 표 이미지 크롭(TableItem.get_image)은 페이지 이미지를 소스로 하므로,
        # table_image 가 켜지면 generate_page_images 를 True 로 강제 보장한다.
        # 페이지 단위 image description 도 페이지 렌더 이미지를 소스로 하므로 동일하게 강제한다.
        if self.table_image_enabled or self._page_desc_options.enabled:
            self.pipe_line_options.generate_page_images = True
        self.pipe_line_options.do_ocr = False
        self.pipe_line_options.ocr_options = ocr_options
        self.pipe_line_options.images_scale = images_scale

        # layout 모델 선택. "genos_layout"(default) / "docling_layout". 잘못된 값은 경고 후 폴백.
        layout_model_type_str = str(
            layout_cfg.get("layout_model_type", cfg.get("layout_model_type", "genos_layout"))
        ).lower().strip()
        if layout_model_type_str == LayoutModelType.DOCLING_LAYOUT.value:
            layout_model_type = LayoutModelType.DOCLING_LAYOUT
        else:
            if layout_model_type_str != LayoutModelType.GENOS_LAYOUT.value:
                _log.warning(
                    f"[DocumentProcessor] Unknown layout_model_type '{layout_model_type_str}', "
                    f"fallback to '{LayoutModelType.GENOS_LAYOUT.value}'"
                )
            layout_model_type = LayoutModelType.GENOS_LAYOUT
        self.pipe_line_options.layout_options.layout_model_type = layout_model_type
        self.pipe_line_options.layout_options.genos_layout_options.endpoint = _as_dict(
            layout_cfg.get("genos_layout")
        ).get("endpoint", "http://192.168.75.174:26001/v1/chat/completions")
        self.pipe_line_options.layout_options.genos_layout_options.api_key = _as_dict(
            layout_cfg.get("genos_layout")
        ).get("api_key", "")

        # genos layout 모델은 batch size를 32로 설정
        page_batch_size = _parse_optional_int(
            _as_dict(layout_cfg.get("genos_layout")).get("page_batch_size"), "layout.genos_layout.page_batch_size"
        )
        if page_batch_size is None or page_batch_size <= 0:
            page_batch_size = 128
        settings.perf.page_batch_size = page_batch_size

        max_completion_tokens = _parse_optional_int(
            _as_dict(layout_cfg.get("genos_layout")).get("max_completion_tokens"),
            "layout.genos_layout.max_completion_tokens",
        )
        if max_completion_tokens is None or max_completion_tokens <= 0:
            max_completion_tokens = 16384
        self.pipe_line_options.layout_options.genos_layout_options.max_completion_tokens = max_completion_tokens

        # DotsOCR VLM 호출/생성 파라미터 (yaml 누락·무효 시 기본값 폴백)
        genos_layout_cfg = _as_dict(layout_cfg.get("genos_layout"))
        layout_model = genos_layout_cfg.get("model") or "dots-mocr"
        layout_timeout = _parse_optional_int(
            genos_layout_cfg.get("timeout"), "layout.genos_layout.timeout"
        )
        if layout_timeout is None or layout_timeout <= 0:
            layout_timeout = 1200
        layout_retry_count = _parse_optional_int(
            genos_layout_cfg.get("retry_count"), "layout.genos_layout.retry_count"
        )
        if layout_retry_count is None or layout_retry_count < 0:
            layout_retry_count = 2
        layout_temperature = _parse_optional_float(
            genos_layout_cfg.get("temperature"), "layout.genos_layout.temperature"
        )
        if layout_temperature is None or layout_temperature < 0:
            layout_temperature = 0.1
        layout_top_p = _parse_optional_float(
            genos_layout_cfg.get("top_p"), "layout.genos_layout.top_p"
        )
        if layout_top_p is None or not (0 < layout_top_p <= 1):
            layout_top_p = 0.9
        layout_repetition_penalty = _parse_optional_float(
            genos_layout_cfg.get("repetition_penalty"),
            "layout.genos_layout.repetition_penalty",
        )
        if layout_repetition_penalty is None or layout_repetition_penalty <= 0:
            layout_repetition_penalty = 1.15
        layout_length_fallback = _parse_optional_bool(
            genos_layout_cfg.get("length_fallback_enabled"),
            "layout.genos_layout.length_fallback_enabled",
        )
        if layout_length_fallback is None:
            layout_length_fallback = True
        layout_fallback_dpi = _parse_optional_int(
            genos_layout_cfg.get("fallback_dpi"), "layout.genos_layout.fallback_dpi"
        )
        if layout_fallback_dpi is None or layout_fallback_dpi <= 0:
            layout_fallback_dpi = 200
        layout_table_fallback = _parse_optional_bool(
            genos_layout_cfg.get("table_fallback_enabled"),
            "layout.genos_layout.table_fallback_enabled",
        )
        if layout_table_fallback is None:
            layout_table_fallback = True
        self.pipe_line_options.layout_options.genos_layout_options.model = layout_model
        self.pipe_line_options.layout_options.genos_layout_options.timeout = layout_timeout
        self.pipe_line_options.layout_options.genos_layout_options.retry_count = layout_retry_count
        self.pipe_line_options.layout_options.genos_layout_options.temperature = layout_temperature
        self.pipe_line_options.layout_options.genos_layout_options.top_p = layout_top_p
        self.pipe_line_options.layout_options.genos_layout_options.repetition_penalty = layout_repetition_penalty
        self.pipe_line_options.layout_options.genos_layout_options.length_fallback_enabled = layout_length_fallback
        self.pipe_line_options.layout_options.genos_layout_options.fallback_dpi = layout_fallback_dpi
        self.pipe_line_options.layout_options.genos_layout_options.table_fallback_enabled = layout_table_fallback

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
        # 모델(ds4sd--DocumentFigureClassifier)은 빌드 시 /models 에 포함(docling-tools models download).
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

        # HWP/HWPX 품질 복구(선택적). 모듈 미로드 시 None → 복구 미적용(기존 동작).
        self._hwp_recovery = (
            HwpQualityRecovery(reload_fn=self._load_document) if HwpQualityRecovery else None
        )

        self.image_description_enricher = ImageDescriptionEnricher(
            self.image_description_options
        )
        self.table_description_enricher = TableDescriptionEnricher(
            self.table_description_options
        )
        self.doc_summary_enricher = DocSummaryEnricher(self.doc_summary_options)
        self.custom_fields_enrichers: list = _build_document_custom_fields_enrichers(
            ec.custom_fields_cfgs
        )
        # enrichment.custom_fields 중 tabular_mapping handler(요청 doc_type=faq 등 xlsx 행별 매핑).
        # LLM enricher 와 달리 파싱 조기 분기(_process_xlsx)에서 소비한다.
        self._tabular_custom_fields_mappers: list = _build_tabular_custom_fields_mappers(
            ec.custom_fields_cfgs
        )
        _warn_tabular_llm_fields_unsupported(self._tabular_custom_fields_mappers, "intelligent")
        self.metadata_enricher = (
            _MetadataEnricher(
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
            # 커스텀 MetadataEnricher가 있으면 docling 내장 metadata 추출을 비활성화한다.
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
        # kwargs에서 save_images 값을 가져와서 옵션 업데이트
        save_images = kwargs.get('save_images', True)
        include_wmf = kwargs.get('include_wmf', False)

        # save_images 옵션이 현재 설정과 다르면 컨버터 재생성
        if (self.simple_pipeline_options.save_images != save_images or
            getattr(self.simple_pipeline_options, 'include_wmf', False) != include_wmf):
            self.simple_pipeline_options.save_images = save_images
            self.simple_pipeline_options.include_wmf = include_wmf
            self._create_converters()

        try:
            conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)
        except Exception as e:
            conv_result: ConversionResult = self.second_converter.convert(file_path, raises_on_error=True)
        return conv_result.document

    def load_documents_with_docling_ocr(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        # kwargs에서 save_images 값을 가져와서 옵션 업데이트
        save_images = kwargs.get('save_images', True)
        include_wmf = kwargs.get('include_wmf', False)

        # save_images 옵션이 현재 설정과 다르면 컨버터 재생성
        if (self.simple_pipeline_options.save_images != save_images or
            getattr(self.simple_pipeline_options, 'include_wmf', False) != include_wmf):
            self.simple_pipeline_options.save_images = save_images
            self.simple_pipeline_options.include_wmf = include_wmf
            self._create_converters()

        try:
            conv_result: ConversionResult = self.ocr_converter.convert(file_path, raises_on_error=True)
        except Exception as e:
            conv_result: ConversionResult = self.ocr_second_converter.convert(file_path, raises_on_error=True)
        return conv_result.document

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        return self.load_documents_with_docling(file_path, **kwargs)

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
        )

        # 표 직렬화 형식(html|markdown)을 청커로 전달(런타임 kwarg 가 있으면 우선).
        kwargs.setdefault("table_format", self._table_format)
        kwargs.setdefault("compact_tables", self._compact_tables)
        # 청크 텍스트 정규화(text_cleanup=safe): 문자 위생을 청킹 입력에 먼저 적용한다.
        # 출력에서만 정규화하면 청크 경계가 노이즈 문자를 센 채로 잡힌다.
        _cleanup = tn.prepare_document(documents, kwargs, self)
        chunks: List[DocChunk] = list(chunker.chunk(dl_doc=documents, **kwargs))
        if _cleanup:
            chunks = tn.drop_blank_chunks(chunks)
        for chunk in chunks:
            if chunk.meta.doc_items[0].prov:
                self.page_chunk_counts[chunk.meta.doc_items[0].prov[0].page_no] += 1
        return chunks

    def split_documents_by_page(self, documents: DoclingDocument, **kwargs: dict) -> List[DocChunk]:
        """PPT 전용 페이지 기반 청킹.

        기본 1 page = 1 chunk. chunk_size(kwargs > yaml) 가 주어지면 연속 페이지를 토큰 기준
        chunk_size 이하가 되도록 greedy 병합한다. 같은 페이지의 native text 와 주입된 page
        description TextItem 은 prov.page_no 로 동일 페이지 청크에 자연히 묶인다.
        """
        chunk_size = _parse_optional_int(kwargs.get('chunk_size'), 'chunk_size')
        if chunk_size is None:
            chunk_size = self._chunk_size
        chunk_size = _clamp_chunk_size(chunk_size)
        # chunk_mode(0/1 또는 'split_only'/'resize_all') > yaml > "split_only"
        chunk_mode = _resolve_chunk_mode(kwargs, self._chunk_mode)
        chunker: GenosSmartChunker = GenosSmartChunker(
            max_tokens=chunk_size if chunk_size is not None else 0,
            merge_peers=True,
            tokenizer=self._tokenizer,
            tokenizer_type=self._tokenizer_type,
            chunk_mode=chunk_mode,
            include_chunk_header=_resolve_include_chunk_header(kwargs, self._include_chunk_header),
        )
        kwargs.setdefault("table_format", self._table_format)
        kwargs.setdefault("compact_tables", self._compact_tables)

        # 청크 텍스트 정규화(text_cleanup=safe): 청킹 입력에 문자 위생을 먼저 적용.
        _cleanup = tn.prepare_document(documents, kwargs, self)

        # 전체 아이템 base chunk(정상 경로와 동일한 아이템 수집/헤더/누락표 복구 재사용)
        base = next(iter(chunker.preprocess(dl_doc=documents, **kwargs)), None)
        if base is None:
            return []
        items = base.meta.doc_items
        header_short = getattr(base, "_header_short_info_list", []) or []

        # prov page_no 로 그룹(아이템 순서 유지). prov 없으면 직전 페이지에 귀속.
        page_items: dict = {}
        page_headers: dict = {}
        last_page = 1
        for idx, it in enumerate(items):
            prov = getattr(it, "prov", None) or []
            pg = prov[0].page_no if prov and getattr(prov[0], "page_no", None) else last_page
            last_page = pg
            page_items.setdefault(pg, []).append(it)
            page_headers.setdefault(pg, []).append(
                header_short[idx] if idx < len(header_short) else {}
            )

        # 페이지별 1 청크 직렬화
        page_chunks: List[DocChunk] = []
        for pg in sorted(page_items.keys()):
            its = page_items[pg]
            text = chunker._generate_section_text_with_heading(
                its, page_headers[pg], documents, **kwargs
            )
            if text and text.strip() and text.strip() != ".":
                page_chunks.append(DocChunk(
                    text=text,
                    # headings 를 채워야 compose_vectors 가 `HEADER:` 를 붙인다(본문 접두는 제거됨).
                    meta=DocMeta(doc_items=its, headings=chunker._extract_header_paths(page_headers[pg]),
                                 captions=None, origin=documents.origin),
                ))

        # chunk_size>0 이면 연속 페이지 greedy 병합 (split_only 는 1 page = 1 chunk 유지)
        if chunk_mode == "resize_all" and chunk_size and chunk_size > 0 and page_chunks:
            merged: List[DocChunk] = [page_chunks[0]]
            for ch in page_chunks[1:]:
                cand_text = merged[-1].text + "\n" + ch.text
                # headings 를 None 으로 덮으면 위에서 채운 섹션 경로가 사라져 병합 청크에만
                # HEADER 가 안 붙는다. 경로를 합집합으로 승계하고, 크기 판정에도 그 헤더 라인을
                # 포함한다(경로가 길어지면 병합 여부가 달라져야 한다).
                cand_headings = _union_paths(merged[-1].meta.headings, ch.meta.headings)
                cand_size = chunker._count_tokens(
                    _build_header_line(cand_headings, chunker.include_chunk_header) + cand_text)
                if cand_size <= chunk_size:
                    merged[-1] = DocChunk(
                        text=cand_text,
                        meta=DocMeta(
                            doc_items=merged[-1].meta.doc_items + ch.meta.doc_items,
                            headings=cand_headings, captions=None, origin=documents.origin,
                        ),
                    )
                else:
                    merged.append(ch)
            page_chunks = merged

        if _cleanup:
            page_chunks = tn.drop_blank_chunks(page_chunks)
        for ch in page_chunks:
            if ch.meta.doc_items and ch.meta.doc_items[0].prov:
                self.page_chunk_counts[ch.meta.doc_items[0].prov[0].page_no] += 1
        _log.info(f"[ppt] page-based chunks: {len(page_chunks)} (chunk_size={chunk_size})")
        return page_chunks

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
                _log.warning("[intelligent] toc=1 요청이지만 TOC endpoint 미구성 → 무시")
            else:
                options = _copy_enrichment_options(options, do_toc_enrichment=want_toc)
                _log.info("[intelligent] runtime toc override → %s", want_toc)
        # PPT 는 페이지 기반 1chunk 라 목차 계층이 무의미 → TOC 만 비활성(다른 enrichment 는 유지).
        if is_ppt and getattr(options, "do_toc_enrichment", False):
            options = _copy_enrichment_options(options, do_toc_enrichment=False)
            _log.info("[intelligent] PPT — TOC enrichment skip")
        try:
            # 새로운 enriched result 받기
            document = enrich_document(document, options, **kwargs)
            return document
        except LLMApiError as e:
            # Preserve provider error payload as-is for load status error message.
            # #329: 기존 hard-fail 동작 유지 + stage/error_type 스탬프(4xx→permanent, 5xx→transient).
            raise GenosServiceException(
                "1", e.raw_error_message, stage="enrichment", error_type=_classify_error(e)
            ) from e

    def _normalize_runtime_kwargs(self, kwargs: dict) -> dict:
        """이미지/차트 description 런타임 토글을 정규화한다(전부 0/1 플래그).

        img_desc          : 이미지 description 사용유무          → image_description.enable
        chart_desc        : 차트 description 사용유무            → chart.enable (chart_convert alias)
        chart_detection   : 1=auto(docling 자동판별)/0=all       → chart.detection
        doc_summary       : 문서 본문요약 사용유무               → body_summary.enable
        미지정 kwarg 는 config(runtime 섹션 또는 base 옵션) 기본값을 따른다.
        """
        normalized = dict(kwargs or {})
        runtime = self._runtime_cfg
        base = getattr(self, "_base_image_description_options", None)

        img_default = _as_int_flag(
            runtime.get("img_desc"), 1 if (base and base.enabled) else 0
        )
        chart_default = _as_int_flag(
            runtime.get("chart_desc", runtime.get("chart_convert")),
            1 if (base and base.chart_enabled) else 0,
        )
        detection_default = _as_int_flag(
            runtime.get("chart_detection"),
            1 if (base and base.chart_detection == "auto") else 0,
        )
        dbase = getattr(self, "_base_doc_summary_options", None)
        summary_default = _as_int_flag(
            runtime.get("doc_summary"),
            1 if (dbase and dbase.enabled) else 0,
        )

        normalized["img_desc"] = _as_int_flag(normalized.get("img_desc"), img_default)
        normalized["chart_desc"] = _as_int_flag(
            normalized.get("chart_desc", normalized.get("chart_convert")), chart_default
        )
        normalized["chart_detection"] = _as_int_flag(
            normalized.get("chart_detection"), detection_default
        )
        normalized["doc_summary"] = _as_int_flag(
            normalized.get("doc_summary"), summary_default
        )

        # 표 description 런타임 토글(table_desc→enable, table_refine→refine.enable)
        tbase = getattr(self, "_base_table_description_options", None)
        table_default = _as_int_flag(
            runtime.get("table_desc"), 1 if (tbase and tbase.enabled) else 0
        )
        refine_default = _as_int_flag(
            runtime.get("table_refine"), 1 if (tbase and tbase.refine_enabled) else 0
        )
        normalized["table_desc"] = _as_int_flag(normalized.get("table_desc"), table_default)
        normalized["table_refine"] = _as_int_flag(normalized.get("table_refine"), refine_default)

        # TOC 런타임 토글(toc/toc_on alias) — 기본값은 config 의 do_toc_enrichment.
        toc_default = _as_int_flag(
            runtime.get("toc", runtime.get("toc_on")),
            1 if getattr(self.enrichment_options, "do_toc_enrichment", False) else 0,
        )
        normalized["toc"] = _as_int_flag(
            normalized.get("toc", normalized.get("toc_on")), toc_default
        )
        # merge_sections 별칭은 도입하지 않는다 — 기존 chunk_mode kwarg 가 동일 기능이며
        # split_documents 의 _resolve_chunk_mode() 가 chunk_mode 0/1/문자열을 직접 해석한다.
        return normalized

    def _configure_runtime_image_mode(self, kwargs: dict):
        """정규화된 kwargs 로 image_description_options/enricher 를 재구성한다.

        순수 override 계산은 enrichment.image_description.resolve_runtime_image_options 에 위임.
        """
        doc_summary = _as_int_flag(kwargs.get("doc_summary"), 0)

        # image description 런타임 재구성 (image base 옵션이 있을 때만)
        base = getattr(self, "_base_image_description_options", None)
        if base is not None:
            img_desc = _as_int_flag(kwargs.get("img_desc"), 0)
            chart_desc = _as_int_flag(kwargs.get("chart_desc"), 0)
            chart_detection = _as_int_flag(kwargs.get("chart_detection"), 0)
            self.image_description_options = resolve_runtime_image_options(
                base,
                img_desc=img_desc,
                chart_desc=chart_desc,
                chart_detection=chart_detection,
                classification_available=getattr(
                    self.pipe_line_options, "do_picture_classification", False
                ),
            )
            self.image_description_enricher = ImageDescriptionEnricher(
                self.image_description_options
            )
            _log.info(
                "[runtime_feature] image mode enabled=%s img_desc=%s chart_desc=%s detection=%s",
                self.image_description_options.enabled,
                img_desc,
                chart_desc,
                self.image_description_options.chart_detection,
            )

        # 표 description 런타임 재구성 (image base 유무와 무관하게 독립 실행)
        tbase = getattr(self, "_base_table_description_options", None)
        if tbase is not None:
            table_desc = _as_int_flag(kwargs.get("table_desc"), 0)
            table_refine = _as_int_flag(kwargs.get("table_refine"), 0)
            self.table_description_options = resolve_runtime_table_options(
                tbase,
                table_desc=table_desc,
                table_refine=table_refine,
            )
            self.table_description_enricher = TableDescriptionEnricher(
                self.table_description_options
            )
            _log.info(
                "[runtime_feature] table mode enabled=%s table_desc=%s table_refine=%s",
                self.table_description_options.enabled,
                table_desc,
                table_refine,
            )

        # doc_summary 런타임 재구성(image/table 공통 컨텍스트 제공)
        dbase = getattr(self, "_base_doc_summary_options", None)
        if dbase is not None:
            self.doc_summary_options = resolve_runtime_doc_summary_options(
                dbase, doc_summary=doc_summary
            )
            self.doc_summary_enricher = DocSummaryEnricher(self.doc_summary_options)
            _log.info(
                "[runtime_feature] doc_summary mode enabled=%s doc_summary=%s",
                self.doc_summary_options.enabled,
                doc_summary,
            )

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
        """페이지 단위 image description: 각 페이지를 렌더링해 설명한 텍스트를 페이지별
        TextItem 으로 주입한다(기존 PictureItem 단위 설명과 별개, 옵션 default False).
        """
        if not self._page_desc_options.enabled:
            return document

        # 페이지별 native text 수집(설명 주입 전) → 프롬프트({{page_text}})에 반영해 요청
        page_texts = collect_page_texts(document)
        page_descs = describe_pages(document, self._page_desc_options, page_texts=page_texts)
        if not page_descs:
            return document

        for page_no in sorted(page_descs.keys()):
            text = page_descs[page_no].strip()
            if not text:
                continue
            prov = ProvenanceItem(
                page_no=page_no,
                bbox=BoundingBox(l=0, t=0, r=1, b=1),
                charspan=(0, len(text)),
            )
            document.add_text(label=DocItemLabel.TEXT, text=text, prov=prov)
        _log.info(f"[page_image_description] 페이지 설명 주입: pages={len(page_descs)}")
        return document

    async def enrich_metadata(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = getattr(self, "metadata_enricher", None)
        if enricher is not None:
            document = await enricher.enrich(document, **kwargs)
        return document

    async def enrich_custom_fields(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        for enricher in self.custom_fields_enrichers:
            document = await enricher.enrich(document, **kwargs)
        return document

    async def compose_vectors(self, document: DoclingDocument, chunks: List[DocChunk], file_path: str, request: Request, converted_pdf_path: Optional[str] = None, **kwargs: dict) -> \
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

        # kwargs에서 부록 정보 추출 !! appendix feature (2025-09-30, geonhee kim) !!
        appendix_info = kwargs.get('appendix', '')
        appendix_list = []
        if isinstance(appendix_info, str):
            if appendix_info:
                try:
                    parsed = json.loads(appendix_info)
                    if isinstance(parsed, list):
                        appendix_list = [item.strip() for item in parsed if isinstance(item, str) and item.strip()]
                    elif isinstance(parsed, str):
                        appendix_list = [parsed.strip()] if parsed.strip() else []
                    else:
                        appendix_list = []
                except json.JSONDecodeError:
                    appendix_list = [appendix_info.strip()] if appendix_info.strip() else []
            else:
                appendix_list = []
        elif isinstance(appendix_info, list):
            appendix_list = appendix_info
        else:
            appendix_list = []

        passthrough_metadata = dict(merged_metadata)
        # GenOSVectorMeta 스키마 예약 필드 + transform 이 소비한 source/target 키는 passthrough 제외.
        reserved_keys = {
            "text", "n_char", "n_word", "n_line", "e_page", "i_page",
            "i_chunk_on_page", "n_chunk_of_page", "i_chunk_on_doc", "n_chunk_of_doc",
            "n_page", "reg_date", "chunk_bboxes", "media_files", "title",
            "created_date", "appendix", "file_path", "metadata", "guardrail_categories",
        } | consumed_keys
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
        # 비-PDF 입력이 변환된 경우 vector 의 file_path 를 변환 PDF 경로로 set.
        if converted_pdf_path:
            global_metadata['file_path'] = converted_pdf_path

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

            # appendix 추출 !! appendix feature (2025-09-30, geonhee kim) !!
            matched_appendices = self.check_appendix_keywords(content, appendix_list)
            # print(appendix_list, matched_appendices)
            chunk_global_metadata = global_metadata.copy()
            chunk_global_metadata['appendix'] = matched_appendices  # Only matched ones
            ###

            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0

            # #315 가드레일 분류 후처리: quote 매칭 → guardrail_categories 부착(항상) + 마스킹 치환(옵션)
            content, chunk_cats = gr.apply_to_text(content, _sensitive_infos, _gr_masking)
            if _cleanup_out:
                content = tn.tidy(content)

            vector = (GenOSVectorMetaBuilder()
                      .set_text(content)
                      .set_page_info(chunk_page, chunk_index_on_page, self.page_chunk_counts[chunk_page])
                      .set_chunk_index(chunk_idx)
                      .set_global_metadata(**chunk_global_metadata) #!! appendix feature (2025-09-30, geonhee kim) !!
                      .set_chunk_bboxes(chunk.meta.doc_items, document)
                      .set_media_files(chunk.meta.doc_items, include_tables=self.table_image_enabled)
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

    def check_empty_text(self, document: DoclingDocument) -> bool:
        """텍스트 클러스터(박스)는 있는데 그 텍스트가 전부 비어 있는 페이지가 있는지 확인.

        length 폴백(layout_only)이나 텍스트레이어 부재 등으로 박스만 있고 텍스트가
        안 채워진 페이지를 잡아 강제 OCR 로 보낸다(이슈 #278 B-2).
        """
        from collections import defaultdict
        page_item_count: dict = defaultdict(int)
        page_text_len: dict = defaultdict(int)
        for item, _level in document.iterate_items():
            if isinstance(item, TextItem) and hasattr(item, 'prov') and item.prov:
                page_no = item.prov[0].page_no
                page_item_count[page_no] += 1
                page_text_len[page_no] += len((item.text or "").strip())
        for page_no, n_items in page_item_count.items():
            # 텍스트 아이템이 있는데 그 페이지 텍스트 총량이 0 → 비어있는 페이지
            if n_items > 0 and page_text_len[page_no] == 0:
                _log.info(f"[intelligent] page {page_no} 텍스트가 비어있음 → 강제 OCR 필요")
                return True
        return False

    def check_appendix_keywords(self, content: str, appendix_list: list) -> str: # !! appendix feature (2025-09-30, geonhee kim) !!
        if not content or not appendix_list:
            return ""

        matched_appendices = []

        # 1. Find appendix patterns in content first
        found_patterns = []

        # Complex patterns: 별지/별표/장부 + numbers (with hyphens, Roman numerals)
        # Updated regex to capture full patterns like "별지 제 Ⅰ -1 호 서식" by matching until closing delimiters
        content = re.sub(r"\s+", "", content)
        complex_patterns = re.findall(r'(별지|별표|장부)(?:제)?([^<>()\[\]]+?)(?=(?:호|서식)|[<>\)\]]|$)', content)
        for pattern_type, number in complex_patterns:
            found_patterns.extend([
                f"{pattern_type} {number}",
                f"{pattern_type} 제{number}호",
                f"{pattern_type}{number}",
                f"{pattern_type}제{number}호"
            ])

        # Standalone patterns: (별표), (별지), (장부)
        standalone_patterns = re.findall(r'[\(\[]+(별지|별표|장부)[\)\]]+', content)
        for pattern_type in set(standalone_patterns):
            found_patterns.extend([
                pattern_type,
                f"{pattern_type}",
            ])

        # 2. Check if found patterns match any appendix in the list
        for appendix in appendix_list:
            if not appendix or not isinstance(appendix, str):
                continue

            appendix_clean = appendix.replace('.pdf', '').lower().strip()
            appendix_clean_no_space = re.sub(r"\s+", "", appendix_clean)

            # If any found pattern exists in appendix filename, it's a match
            for pattern in found_patterns:
                pattern_no_space = re.sub(r"\s+", "", pattern).lower()
                if pattern_no_space in appendix_clean_no_space:
                    matched_appendices.append(appendix)
                    break  # Prevent duplicates

        return ', '.join(matched_appendices) if matched_appendices else ""

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

    def _convert_to_pdf(self, file_path: str, **kwargs: dict) -> tuple[str, str]:
        """비-PDF 입력을 PDF SDK/LibreOffice 로 변환. (변환된 file_path, converted_pdf_path) 반환."""
        # 변환 backend(pdf_sdk/rhwp/libreoffice)가 전무하면(이슈 #286 — 빌드 시 OFF)
        # 변환 시도 자체가 무의미하므로, PDF 직접 입력을 안내하며 즉시 중단한다.
        if not _has_any_pdf_converter():
            raise GenosServiceException(
                1,
                f"이 전처리기 이미지에는 PDF 변환기(rhwp/LibreOffice/PDF SDK)가 설치되어 "
                f"있지 않아 '{os.path.basename(file_path)}' 를 PDF 로 변환할 수 없습니다. "
                f"PDF 로 변환한 파일을 입력하거나, 변환기를 포함해 전처리기 이미지를 다시 "
                f"빌드하세요 (genon/README.md 참고).",
            )
        _log.info(f"[intelligent] Non-PDF input — auto-converting to PDF: {file_path}")
        use_sdk = kwargs.get('use_pdf_sdk', True)
        converted = convert_to_pdf(file_path, use_pdf_sdk=use_sdk)
        if (not converted or not os.path.exists(converted)) and use_sdk:
            _log.warning(f"[intelligent] SDK conversion failed → fallback to LibreOffice")
            converted = convert_to_pdf(file_path, use_pdf_sdk=False)
        if not converted or not os.path.exists(converted):
            raise GenosServiceException(1, f"PDF 변환 실패: {file_path}")
        _log.info(f"[intelligent] Converted PDF: {converted}")
        return converted, converted

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
                1, f"동일 doc_type 에 tabular custom_fields 설정이 여러 개입니다: {runtime_doc_type}"
            )
        if matching_mappers:
            _log.info(f"[intelligent] xlsx tabular custom_fields 처리(doc_type={runtime_doc_type}): {file_path}")
            try:
                vectors = build_tabular_custom_fields_vectors(
                    file_path, matching_mappers[0], runtime_doc_type,
                    header_row=self._xlsx_cfg["header_row"],
                    multi_table=self._xlsx_cfg["multi_table"],
                )
            except (FileNotFoundError, TypeError, ValueError) as exc:
                raise GenosServiceException(1, str(exc)) from exc
            if not vectors:
                raise GenosServiceException(1, "chunk length is 0")
            return vectors

        if self._xlsx_cfg["processing_mode"] == "tabular":
            _log.info(f"[intelligent] xlsx tabular 직접 처리: {file_path}")
            vectors = build_tabular_vectors(
                file_path,
                header_row=self._xlsx_cfg["header_row"],
                multi_table=self._xlsx_cfg["multi_table"],
            )
            if not vectors:
                raise GenosServiceException(1, f"chunk length is 0")
            return vectors

        _log.info(f"[intelligent] xlsx docling 직접 처리(PDF 변환 생략): {file_path}")
        try:
            document = build_docling_document(
                file_path, save_images=kwargs.get('save_images', False)
            )
        except Exception as e:
            raise GenosServiceException(
                1, f"xlsx 처리 실패: {os.path.basename(file_path)} ({e})"
            )
        # openpyxl 텍스트라 글리프 깨짐이 없고 렌더 PDF 도 없으므로 테이블셀 재OCR 은 생략.
        # table_as_chunk=True: 시트/표마다 별도 청크로 분리(엑셀은 표 단위가 논리 단위).
        return await self._document_to_vectors(
            document, file_path, request,
            converted_pdf_path=None, ocr_table_cells=False, table_as_chunk=True, **kwargs
        )

    async def _process_pdf(self, request: Request, file_path: str,
                           converted_pdf_path: Optional[str], is_ppt: bool = False,
                           source_file_path: Optional[str] = None, **kwargs: dict):
        """PDF(또는 PDF 로 변환된) 입력을 docling 으로 로딩 후 공유 파이프라인으로 처리."""
        document = self._load_document(file_path, **kwargs)

        # HWP/HWPX 품질 복구(선택적): PDF 변환이 내용을 잃으면(text score 낮음) rhwp 재변환
        # 재시도 → 개선되면 교체. HWPX 는 네이티브 XML 추출로 추가 폴백. hwp_recovery 모듈이
        # 로드된 경우에만 적용하고, 없으면 로딩된 document 를 그대로 통과(기존 동작).
        # source_file_path 는 변환 전 원본(.hwp/.hwpx). 정상 문서는 score≥임계값 이라 미진입.
        if source_file_path is None:
            source_file_path = file_path
        if self._hwp_recovery is not None:
            file_path, converted_pdf_path, document = self._hwp_recovery.recover(
                document, source_file_path, file_path, converted_pdf_path, **kwargs
            )

        return await self._document_to_vectors(
            document, file_path, request,
            converted_pdf_path=converted_pdf_path, ocr_table_cells=True, is_ppt=is_ppt, **kwargs
        )

    def _load_document(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        """ocr_mode 에 따라 docling 문서를 로딩한다.
        "force"=무조건 전체 OCR / "auto"=휴리스틱 기반 재OCR / "disable"=OCR 안 함
        """
        if self.ocr_mode == "force":
            return self.load_documents_with_docling_ocr(file_path, **kwargs)
        document: DoclingDocument = self.load_documents(file_path, **kwargs)
        if self.ocr_mode == "auto":
            if not check_document(document, self.enrichment_options) or self.check_glyphs(document) or self.check_empty_text(document):
                # OCR이 필요하다고 판단되면 OCR 수행
                document = self.load_documents_with_docling_ocr(file_path, **kwargs)
        return document

    async def _document_to_vectors(self, document: DoclingDocument, file_path: str,
                                   request: Request, *, converted_pdf_path: Optional[str],
                                   ocr_table_cells: bool, is_ppt: bool = False, **kwargs: dict) -> list:
        """DoclingDocument → enrichment → 청킹 → 벡터 생성(공유 파이프라인).

        ocr_table_cells: 글리프 깨진 테이블 셀 재OCR 수행 여부(xlsx 직접 처리는 False).
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

        enrichment_context = kwargs.get("_enrichment_context", {})
        if not isinstance(enrichment_context, dict):
            enrichment_context = {}
        enrichment_kwargs = dict(kwargs)
        enrichment_kwargs["_enrichment_context"] = enrichment_context
        # #329: error_policy=strict 이면 _handle_stage_error 가 GenosServiceException 으로
        # 재-raise(삼키지 않음). lenient(기본)은 기존처럼 warning 후 계속.
        try:
            document = self.enrich_doc_summary(document, **enrichment_kwargs)
        except Exception as exc:
            _handle_stage_error(exc, "doc_summary")
        try:
            document = self.enrich_image_descriptions(document, **enrichment_kwargs)
        except Exception as exc:
            _handle_stage_error(exc, "image_description")
        text_table_enricher = next((
            enricher for enricher in self.custom_fields_enrichers
            if enricher.wants_table_descriptions(**enrichment_kwargs)
        ), None)
        if text_table_enricher is None:
            try:
                document = self.enrich_table_descriptions(document, **enrichment_kwargs)
            except Exception as exc:
                _handle_stage_error(exc, "table_description")
        elif (
            text_table_enricher.table_description_conflict_policy == "error"
            and enrichment_kwargs.get("table_desc")
        ):
            _handle_stage_error(
                ValueError("텍스트 표 설명과 이미지 표 설명이 동시에 활성화되었습니다."),
                "table_description",
            )
        # 페이지 단위 image description 은 PPT 원본에만 적용(formats.ppt.page_description).
        if is_ppt:
            try:
                document = self.enrich_page_descriptions(document, **enrichment_kwargs)
            except Exception as exc:
                _handle_stage_error(exc, "page_description")
        try:
            document = await self.enrich_metadata(document, **enrichment_kwargs)
        except Exception as exc:
            _handle_stage_error(exc, "metadata")
        try:
            document = await self.enrich_custom_fields(document, **enrichment_kwargs)
        except Exception as exc:
            _handle_stage_error(exc, "custom_fields")
        # doc_type 스탬프(예: card): 요청 kwargs 로 doc_type 이 오면 문서 메타에 저장 → compose_vectors 가
        # 모든 청크에 broadcast(+ context metadata 노출). (faq 는 xlsx tabular 경로에서 별도 처리)
        doc_type = normalize_doc_type(kwargs.get("doc_type"))
        if doc_type:
            try:
                store_metadata_in_document(document, {"doc_type": doc_type})
                if isinstance(enrichment_context, dict):
                    enrichment_context.setdefault("metadata", {})["doc_type"] = doc_type
            except Exception as exc:
                _handle_stage_error(exc, "doc_type_stamp")

        # 민감정보 분류(#315): 청킹 전, 문서 전체를 분류 워크플로우에 1회 호출 → sensitive_infos.
        # 실제 라벨 부착/마스킹 치환은 청킹 후 compose 에서 quote 매칭으로 수행.
        sensitive_infos: list = []
        if gr.call_enabled(kwargs):
            sensitive_infos = gr.classify_document(
                gr.doc_text(document), self._guardrail_url, self._guardrail_workflow_id,
                self._guardrail_api_key, self._guardrail_timeout,
            )

        has_text_items = False
        for item, _ in document.iterate_items():
            if (isinstance(item, (TextItem, ListItem, CodeItem, SectionHeaderItem)) and item.text and item.text.strip()) or (isinstance(item, TableItem) and item.data and len(item.data.table_cells) == 0):
                has_text_items = True
                break

        if has_text_items:
            # Extract Chunk from DoclingDocument.
            # PPT 는 페이지 기반 청킹(기본 1 page 1 chunk, chunk_size 지정 시 페이지 결합).
            if is_ppt:
                chunks: List[DocChunk] = self.split_documents_by_page(document, **kwargs)
            else:
                chunks: List[DocChunk] = self.split_documents(document, **kwargs)
        else:
            # text가 있는 item이 없을 때 document에 임의의 text item 추가
            # 첫 번째 페이지의 기본 정보 사용 (1-based indexing)
            page_no = 1

            # ProvenanceItem 생성
            prov = ProvenanceItem(
                page_no=page_no,
                bbox=BoundingBox(l=0, t=0, r=1, b=1),  # 최소 bbox
                charspan=(0, 1)
            )

            # document에 temp text item 추가
            document.add_text(
                label=DocItemLabel.TEXT,
                text=".",
                prov=prov
            )

            # split_documents 호출
            if is_ppt:
                chunks: List[DocChunk] = self.split_documents_by_page(document, **kwargs)
            else:
                chunks: List[DocChunk] = self.split_documents(document, **kwargs)
        # await assert_cancelled(request)

        vectors = []
        if len(chunks) >= 1:
            vectors: list[dict] = await self.compose_vectors(
                document, chunks, file_path, request,
                converted_pdf_path=converted_pdf_path,
                _sensitive_infos=sensitive_infos,
                _guardrail_masking=(gr.call_enabled(kwargs) and self._guardrail_masking_enabled),
                **enrichment_kwargs,
            )
        else:
            raise GenosServiceException(1, f"chunk length is 0")

        # 변환된 PDF 를 minio 에 업로드. object key 는 원본 파일명의 stem + ".pdf".
        # (예: 원본 file_name='sample.hwp' → minio key='<doc_id>/sample.pdf')
        # upload_files 가 finally 에서 org_path 를 os.remove 하는데, 변환 PDF 의
        # NFS 원본은 GenOS UI 의 PDF preview 가 직접 참조하므로 보존 필요.
        # → 임시 사본을 만들어 그것만 업로드시키고 NFS 원본은 그대로 둔다.
        if converted_pdf_path and upload_files:
            original_name = kwargs.get('file_name') or os.path.basename(converted_pdf_path)
            pdf_object_name = os.path.splitext(original_name)[0] + '.pdf'
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as _tmp:
                shutil.copy(converted_pdf_path, _tmp.name)
                _tmp_upload_path = _tmp.name
            await upload_files(
                [{'path': _tmp_upload_path, 'name': pdf_object_name}],
                request=request,
            )

        """
        # 미디어 파일 업로드 방법
        media_files = [
            { 'path': '/tmp/graph.jpg', 'name': 'graph.jpg', 'type': 'image' },
            { 'path': '/result/1/graph.jpg', 'name': '1/graph.jpg', 'type': 'image' },
        ]

        # 업로드 요청 시에는 path, name 필요
        file_list = [{k: v for k, v in file.items() if k != 'type'} for file in media_files]
        await upload_files(file_list, request=request)

        # 메타에 저장시에는 name, type 필요
        meta = [{k: v for k, v in file.items() if k != 'path'} for file in media_files]
        vectors[0].media_files = meta
        """

        return vectors

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        runtime_level = kwargs.get('log_level')
        self.setup_logging(runtime_level if runtime_level is not None else self._log_level)

        # 런타임 토글(img_desc/chart_desc/chart_detection/doc_summary)로 이미지·차트 description 재구성
        kwargs = self._normalize_runtime_kwargs(kwargs)
        self._configure_runtime_image_mode(kwargs)

        # #329: LLM 캐시 / error_policy 컨텍스트를 요청 스코프로 설정.
        # ThreadPool 워커 스레드로는 in_current_context 로 전파된다(docling/utils/llm_cache).
        _cache_token = _set_cache_context(_resolve_cache_context(kwargs))
        try:
            _log.info(f"file_path: {file_path}")
            _log.info(f"kwargs: {kwargs}")

            # 비정상 파일 사전 감지(이슈 #278): 지원 포맷 매직헤더에 하나도 안 맞고 텍스트도
            # 아니면(=DRM 암호화/손상 바이너리) 변환 시 garbage PDF → VLM 무한 출력/행을
            # 유발하므로 변환 전에 컷한다. 확장자와 무관하게 실제 헤더로 판정.
            bad_reason = _detect_unsupported_file(file_path)
            if bad_reason:
                _log.warning(
                    f"[intelligent] 비정상 파일 감지({bad_reason}) — 처리 중단: {file_path}"
                )
                raise GenosServiceException(
                    "1", f"{bad_reason} 입니다. 정상 문서로 다시 업로드하세요: {os.path.basename(file_path)}"
                )

            ext = os.path.splitext(file_path)[1].lower()

            # 직접 처리(PDF 변환 없이) 가능한 포맷(이슈 #288): 엑셀 계열(xlsx/xlsm) + csv.
            # csv 는 본질적으로 tabular 이므로 항상 직접 처리한다(PDF 변환 시 행 분할 문제 방지).
            # (.xls/.xlsb 는 openpyxl/docling 미지원 → 아래 PDF 변환 경로로 처리)
            # 이 집합을 변환 가드와 디스패치 양쪽에서 동일하게 써서 "직접 처리 포맷 == 변환 제외 포맷"
            # 불변식을 유지한다.

            # 직접 처리 포맷이 아니고 PDF 도 아니면 PDF 로 변환한다.
            # - auto_convert_to_pdf=True (default): PDF SDK/LibreOffice 로 자동 변환 후 진입
            # - auto_convert_to_pdf=False: 변환 없이 그대로 진행 (변경 전 동작; PDF 가정)
            converted_pdf_path: Optional[str] = None
            # HWP/HWPX 품질 복구가 참조할 변환 전 원본 경로(rhwp 재변환/네이티브 추출용).
            source_file_path = file_path
            if ext not in _XLSX_DIRECT_EXTS and kwargs.get('auto_convert_to_pdf', True) and not _is_pdf(file_path):
                file_path, converted_pdf_path = self._convert_to_pdf(file_path, **kwargs)

            # 포맷별 처리: 직접 처리 가능 포맷은 xlsx 핸들러, 그 외는 PDF(docling) 처리.
            if ext in _XLSX_DIRECT_EXTS:
                return await self._process_xlsx(request, file_path, **kwargs)
            # 원본이 PPT 였는지(변환 전 ext)를 명시 전달 — 페이지 기반 청킹/page description 게이팅용.
            is_ppt = ext in ('.ppt', '.pptx')
            return await self._process_pdf(
                request, file_path, converted_pdf_path, is_ppt=is_ppt,
                source_file_path=source_file_path, **kwargs
            )
        finally:
            _log_cache_summary()
            _reset_cache_context(_cache_token)


class GenosServiceException(Exception):
    # GenOS 와의 의존성 부분 제거를 위해 추가
    def __init__(self, error_code: str, error_msg: Optional[str] = None, msg_params: Optional[dict] = None,
                 *, stage: Optional[str] = None, error_type: Optional[str] = None) -> None:
        self.code = 1
        self.error_code = error_code
        self.error_msg = error_msg or "GenOS Service Exception"
        self.msg_params = msg_params or {}
        # #329: 실패 단계(stage)와 성격(error_type: transient/permanent/timeout).
        self.stage = stage
        self.error_type = error_type

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        return f"{class_name}(code={self.code!r}, errMsg={self.error_msg!r})"


# GenOS 와의 의존성 제거를 위해 추가
async def assert_cancelled(request: Request):
    if await request.is_disconnected():
        raise GenosServiceException(1, f"Cancelled")
