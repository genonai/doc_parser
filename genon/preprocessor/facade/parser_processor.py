# 파싱용 전처리기 v.2.2.0 (2026-06-02 Release)
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from fastapi import Request

from langchain_community.document_loaders import (
    PyMuPDFLoader,
    UnstructuredFileLoader,
    UnstructuredImageLoader,
    UnstructuredPowerPointLoader,
    UnstructuredWordDocumentLoader,
)
from langchain_core.documents import Document

from docling.backend.genos_hwp_backend import GenosHwpDocumentBackend
from docling.backend.genos_msword_backend import GenosMsWordDocumentBackend
from docling.backend.hwp_backend import HwpDocumentBackend
from docling.backend.xml.hwpx_backend import HwpxDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    DataEnrichmentOptions,
    PdfPipelineOptions,
    PipelineOptions,
    TableFormerMode,
    UpstageOcrOptions,
)
from docling.datamodel.settings import settings as docling_settings
from docling.document_converter import (
    DocumentConverter,
    HwpxFormatOption,
    PdfFormatOption,
    WordFormatOption,
)
from docling.pipeline.simple_pipeline import SimplePipeline
from docling.prompts.prompt_manager import LLMApiError
from docling.utils.document_enrichment import check_document, enrich_document
from docling.utils.llm_cache import (
    classify_error as _classify_error,
    current_context as _cache_current_context,
    log_summary as _log_cache_summary,
    reset_context as _reset_cache_context,
    resolve_context as _resolve_cache_context,
    set_context as _set_cache_context,
)
from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    PictureItem,
    ProvenanceItem,
    TableItem,
)
from docling_core.types.doc.base import CoordOrigin
from docling_core.types.doc.document import ContentLayer
from genon.preprocessor.facade.common.markdown_export import export_markdown

from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
    build_document_custom_fields_enrichers,
    normalize_doc_type,
    normalize_doc_types,
)
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    build_tabular_custom_fields_mappers,
)
from genon.preprocessor.facade.enrichment.json_records import (
    build_json_records_mappers,
)
from genon.preprocessor.facade.enrichment.json_semantic import (
    build_semantic_json_mappers,
)
from genon.preprocessor.facade.enrichment.markdown_front_matter import (
    build_markdown_front_matter_specs,
    build_markdown_text_fence_specs,
    build_html_marker_heading_doc_types,
    build_markdown_marker_heading_doc_types,
)
from genon.preprocessor.facade.enrichment.metadata_enricher import MetadataEnricher

from genon.preprocessor.facade import guardrail as gr
from genon.preprocessor.facade.enrichment.enrichment_config import EnrichmentConfig
from genon.preprocessor.facade.enrichment.page_description import (
    PageDescriptionOptions,
    collect_page_texts,
    describe_pages,
)
from genon.preprocessor.facade.enrichment.image_description import (
    ImageDescriptionOptions,
    ImageDescriptionEnricher,
    PictureDescriptionExtractor,
)
from genon.preprocessor.facade.enrichment.table_text_description import (
    TableTextDescriptionEnricher,
    apply_table_description_stage,
)
from genon.preprocessor.facade.enrichment.table_description import (
    TableDescriptionOptions,
    TableDescriptionEnricher,
    TableDescriptionExtractor,
    refined_html_to_format,
)
from genon.preprocessor.facade.enrichment.doc_summary import (
    DocSummaryOptions,
    DocSummaryEnricher,
)

try:
    import chardet
except ImportError:
    raise RuntimeError("Module 'chardet' not imported. Run `pip install chardet`.")

try:
    from weasyprint import HTML
except (ImportError, OSError):
    print("Warning: WeasyPrint could not be imported. PDF conversion features will be disabled.")
    HTML = None

try:
    from genos_utils import upload_files
except ImportError:
    upload_files = None

_log = logging.getLogger(__name__)


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


# ── 공용 하위 모듈로 옮긴 헬퍼들의 별칭 ──────────────────────────────
# 구현은 facade/common/, facade/chunking/ 에 한 벌만 둔다. 여기서는 기존 이름을
# 그대로 유지해 호출부를 건드리지 않는다. 사이트별 조정 대상 상수(구분자, 최소
# 청크 크기, 토크나이저 경로)는 이 파일에 남아 있으므로 래퍼가 넘겨준다.
from genon.preprocessor.facade.chunking import table_shape as ts
from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.common import loaders as ld
from genon.preprocessor.facade.common import pipeline_setup as ps
from genon.preprocessor.facade.common import runtime_kwargs as rk
from genon.preprocessor.facade.common import docling_ops as dops
from genon.preprocessor.facade.common import runtime as rt
from genon.preprocessor.facade.common import file_probe as fp
from genon.preprocessor.facade.common import pdf_convert as pc
from genon.preprocessor.facade.common import format_alias as fa
from genon.preprocessor.facade.common.doc_meta import strip_enricher_meta

_as_dict = cp.as_dict
_materialize_alias_copy = fa.materialize_alias_copy
_parse_extension_aliases = fa.parse_extension_aliases
_resolve_ext = fa.resolve_ext
_as_int_flag = cp.as_int_flag
_copy_enrichment_options = cp.copy_enrichment_options
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
    return cp.load_config(config_path, strict=True)


# fontTools 로그 억제
for _n in ("fontTools", "fontTools.ttLib", "fontTools.ttLib.ttFont"):
    _lg = logging.getLogger(_n)
    _lg.setLevel(logging.CRITICAL)
    _lg.propagate = False
    logging.getLogger().setLevel(logging.WARNING)

# PDF 변환 대상 확장자
CONVERTIBLE_EXTENSIONS = ['.hwp', '.txt', '.json', '.md', '.ppt', '.pptx', '.docx']


# ============================================================
# 설정 로딩
# ============================================================


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


def _resolve_default_parser_config_path() -> str:
    base_dir = Path(__file__).resolve().parent
    local_config = (base_dir / "../resource_dev/parser_processor_config.yaml").resolve()
    default_config = (base_dir / "../resource/parser_processor_config.yaml").resolve()

    if local_config.exists():
        return str(local_config)
    return str(default_config)


# ============================================================
# 헬퍼 함수 (from attachment_processor.py)
# ============================================================

_is_libreoffice_available = fp.is_libreoffice_available


def convert_to_pdf(file_path: str) -> str | None:
    """LibreOffice 로 PDF 변환을 시도한다. 실패해도 예외를 던지지 않고 None 을 반환한다.

    이 facade 는 backend chain 을 LibreOffice 하나로 고정한다(rhwp/pdf_sdk 미사용).
    구현은 facade/common/pdf_convert.py 에 있다.
    """
    return pc.convert_to_pdf(file_path, libreoffice_only=True)


def _get_pdf_path(file_path: str) -> str:
    """변환 가능한 확장자면 PDF 경로로 바꾼다(구현은 facade/common/file_probe.py)."""
    return fp.get_pdf_path(file_path, CONVERTIBLE_EXTENSIONS)

install_packages = ld.install_packages


# 민감정보 분류(#315): parser 는 청크가 없어 직접 라벨/마스킹은 못 하지만, guardrail_call 시
# 문서 전체를 워크플로우로 1회 분류해 sensitive_infos 를 파스 출력에 실어 chunking API 로 넘긴다.
# 청크별 quote 매칭·라벨·마스킹은 chunking(및 intelligent/attachment/convert)이 수행한다.


# ============================================================
# TextLoader (from attachment_processor.py)
# ============================================================

def _doc_is_html_origin(doc) -> bool:
    """원본이 HTML 계열인가. 표 헤더 행 수 판정 규칙이 여기서 갈린다."""
    origin = getattr(doc, "origin", None)
    mimetype = str(getattr(origin, "mimetype", "") or "").lower()
    filename = str(getattr(origin, "filename", "") or "").lower()
    return mimetype in {"text/html", "application/xhtml+xml"} or filename.endswith(
        (".html", ".htm", ".xhtml"))



class TextLoader(ld.TextLoaderBase):
    pass



# ============================================================
# AudioLoader (from attachment_processor.py)
# ============================================================

class AudioLoader(ld.AudioLoaderBase):
    pass


# ============================================================
# IntelligentDocumentProcessor — PDF 전용 (from intelligent_processor.py)
# 파싱에 필요한 메서드만 포함 (청킹/벡터 메서드 제외)
# ============================================================

class IntelligentDocumentProcessor:

    def __init__(self, config: dict | None = None, config_path: str | None = None):
        cfg = _as_dict(config)
        self._config_dir = Path(config_path).resolve().parent if config_path else Path.cwd()
        # 런타임 kwargs 기본값(img_desc/chart_desc/chart_detection/doc_summary) 용도
        self._runtime_cfg = _as_dict(cfg.get("runtime"))
        ocr_cfg = _as_dict(cfg.get("ocr"))
        layout_cfg = _as_dict(cfg.get("layout"))
        pdf_cfg = _as_dict(cfg.get("pdf_pipeline"))
        ec = EnrichmentConfig.from_raw(cfg.get("enrichment"), self._config_dir, parent_cfg=cfg)

        # OCR 엔드포인트는 ocr.paddle.ocr_endpoint 가 정식 위치.
        # 구버전 호환: ocr.ocr_endpoint(상위) / 최상위 ocr_endpoint 도 폴백으로 인식.
        # 해석은 facade/common/pipeline_setup.py 로 모았다(조정 지점은 yaml 의 ocr 섹션).
        _ocr_rt = ps.resolve_ocr_runtime(cfg, ocr_cfg)
        ocr_ep = _ocr_rt.endpoint
        self.ocr_mode = _ocr_rt.mode
        self._table_cell_ocr_timeout = _ocr_rt.table_cell_ocr_timeout
        self._glyph_table_cell_threshold = _ocr_rt.glyph_table_cell_threshold
        self._glyph_document_threshold = _ocr_rt.glyph_document_threshold

        # 해석·적용은 facade/common/pipeline_setup.py 로 모았다(조정 지점은 yaml 의 layout 섹션).
        _layout = ps.resolve_layout_settings(cfg, layout_cfg)

        ocr_options = self._build_ocr_options(ocr_cfg, paddle_endpoint=ocr_ep)
        if isinstance(ocr_options, UpstageOcrOptions):
            self.ocr_endpoint = ocr_options.api_endpoint
        else:
            self.ocr_endpoint = ocr_ep

        self.page_chunk_counts = defaultdict(int)

        # pdf_pipeline 섹션 해석은 facade/common/pipeline_setup.py 로 모았다.
        _pdf = ps.resolve_pdf_basics(pdf_cfg)
        accelerator_options = _pdf.accelerator_options
        images_scale = _pdf.images_scale
        generate_page_images = _pdf.generate_page_images
        generate_picture_images = _pdf.generate_picture_images
        table_structure_mode = _pdf.table_structure_mode

        self.pipe_line_options = PdfPipelineOptions()
        self.pipe_line_options.generate_page_images = (
            True if generate_page_images is None else generate_page_images
        )
        self.pipe_line_options.generate_picture_images = (
            True if generate_picture_images is None else generate_picture_images
        )
        self.pipe_line_options.do_ocr = False
        self.pipe_line_options.ocr_options = ocr_options
        self.pipe_line_options.images_scale = images_scale

        ps.apply_layout_settings(self.pipe_line_options, _layout)
        docling_settings.perf.page_batch_size = _layout.page_batch_size

        self.pipe_line_options.do_table_structure = True
        self.pipe_line_options.table_structure_options.do_cell_matching = True
        self.pipe_line_options.table_structure_options.mode = table_structure_mode
        self.pipe_line_options.accelerator_options = accelerator_options

        # docling 모델(TableFormer 등) 로컬 경로. config 에 값이 있을 때만 설정하고,
        # 비어있으면 설정하지 않아 docling 기본 캐시 동작을 그대로 유지(backward compat).
        models_cfg = _as_dict(cfg.get("models"))
        artifacts_path = models_cfg.get("artifacts_path")
        if artifacts_path:
            self.pipe_line_options.artifacts_path = Path(artifacts_path)

        # xlsx(엑셀) 처리 설정(이슈 #288). formats.xlsx 아래에 둔다.
        #   tabular(기본): openpyxl 로 병합셀 unmerge+forward-fill 후 데이터 행마다 parse element 생성.
        #   docling: docling MsExcel 백엔드로 DoclingDocument 생성 후 parse-JSON 직렬화.
        #   tabular.{header_row, multi_table}: tabular 모드 전용 세부 옵션
        formats_cfg = _as_dict(cfg.get("formats"))
        xlsx_cfg = _as_dict(formats_cfg.get("xlsx"))
        tabular_cfg = _as_dict(xlsx_cfg.get("tabular"))
        xlsx_mode = str(xlsx_cfg.get("processing_mode", "tabular")).strip().lower()
        if xlsx_mode not in {"docling", "tabular"}:
            _log.warning(
                f"[DocumentProcessor] Unknown formats.xlsx.processing_mode '{xlsx_mode}', fallback to 'tabular'."
            )
            xlsx_mode = "tabular"
        self._xlsx_cfg = {
            "processing_mode": xlsx_mode,
            "header_row": _parse_optional_int(tabular_cfg.get("header_row"), "formats.xlsx.tabular.header_row") or 0,
            "multi_table": bool(_parse_optional_bool(tabular_cfg.get("multi_table"), "formats.xlsx.tabular.multi_table")),
        }

        # md(마크다운) 처리 설정. formats.md 아래에 둔다.
        #   docling(기본): MarkdownDocumentBackend 로 파싱 → 헤딩/표 구조 유지 + enrichment(custom_fields) 적용.
        #   text: 레거시 TextLoader 경로. 구조가 없고 enrichment 가 걸리지 않는다.
        # 기본을 docling 으로 두는 이유: 상품설명서(md) 같은 doc_type 이 custom_fields 를 쓰려면
        # DoclingDocument 가 있어야 한다(TextLoader 경로에는 후처리 enrichment 훅이 없다).
        md_cfg = _as_dict(formats_cfg.get("md"))
        md_mode = str(md_cfg.get("processing_mode", "docling")).strip().lower()
        if md_mode not in {"docling", "text"}:
            _log.warning(
                f"[DocumentProcessor] Unknown formats.md.processing_mode '{md_mode}', fallback to 'docling'."
            )
            md_mode = "docling"
        self._md_cfg = {"processing_mode": md_mode}

        # 비표준 확장자 별칭. formats.extension_aliases 아래에 둔다.
        #   예) {".parsed": ".md"} — *.parsed 를 마크다운으로 보고 md 분기로 라우팅한다.
        # 확장자별 분기를 늘리지 않고 설정 한 줄로 새 원천을 받기 위한 장치다.
        self._ext_aliases = _parse_extension_aliases(formats_cfg)
        if self._ext_aliases:
            _log.info(f"[DocumentProcessor] 확장자 별칭: {self._ext_aliases}")

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
                    f"[IntelligentDocumentProcessor] do_picture_classification 설정 실패: {exc}"
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
            try:
                self.pipe_line_options.generate_page_images = True
            except Exception as exc:
                _log.warning(
                    f"[IntelligentDocumentProcessor] generate_page_images 설정 실패: {exc}"
                )

        # 문서 본문요약(doc_summary) 옵션. image/table 이 공유하는 {{doc_summary}} 를 1회 계산.
        self.doc_summary_options = DocSummaryOptions.from_config(
            doc_summary_cfg=ec.doc_summary_cfg,
            fallback_api_url=ec.api_url,
            fallback_api_key=ec.api_key,
            fallback_model=ec.model,
            config_dir=self._config_dir,
        )
        self._base_doc_summary_options = self.doc_summary_options

        # pipe_line_options 의 layout 설정이 deep copy 에 포함되므로 별도 재설정 불필요
        self.ocr_pipe_line_options = self.pipe_line_options.model_copy(deep=True)
        self.ocr_pipe_line_options.do_ocr = True
        self.ocr_pipe_line_options.ocr_options = ocr_options.model_copy(deep=True)
        self.ocr_pipe_line_options.ocr_options.force_full_page_ocr = True

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
        self.custom_fields_cfgs = list(ec.custom_fields_cfgs)
        self.custom_fields_enrichers: list = (
            build_document_custom_fields_enrichers(self.custom_fields_cfgs)
        )

        # 사용자가 커스텀 metadata 신호(prompt/파일/output_fields/parser)를 하나라도 지정한 경우
        # 커스텀 MetadataEnricher를 사용한다. 지정되지 않으면 docling 내장 enricher가 동작한다
        # (하위 호환). built-in default system prompt 가 이 게이트를 흔들지 않도록
        # system_prompt 유무가 아닌 has_custom_metadata 로 판단한다.
        self.metadata_enricher: "Optional[MetadataEnricher]" = (
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

        self.enrichment_options = DataEnrichmentOptions(
            do_toc_enrichment=ec.toc.do_toc,
            toc_doc_type=ec.toc.doc_type,
            # 커스텀 MetadataEnricher가 있으면 docling 내장 비활성화
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
        save_images = kwargs.get('save_images', True)
        include_wmf = kwargs.get('include_wmf', False)

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
        save_images = kwargs.get('save_images', True)
        include_wmf = kwargs.get('include_wmf', False)

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

    def enrichment(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        options = self.enrichment_options
        # 런타임 toc(0/1) — config 기본값(do_toc_enrichment)을 요청별로 켜고/끈다.
        # 활성화(0→1)는 TOC endpoint 가 config 에 구성된 경우에만 유효(미구성 시 무시).
        cur_toc = bool(getattr(options, "do_toc_enrichment", False))
        want_toc = bool(_as_int_flag(kwargs.get("toc"), 1 if cur_toc else 0))
        if want_toc != cur_toc:
            if want_toc and not str(getattr(options, "toc_api_base_url", "") or ""):
                _log.warning("[parser] toc=1 요청이지만 TOC endpoint 미구성 → 무시")
            else:
                options = _copy_enrichment_options(options, do_toc_enrichment=want_toc)
                _log.info("[parser] runtime toc override → %s", want_toc)
        try:
            document = enrich_document(document, options, **kwargs)
            return document
        except LLMApiError as e:
            # Preserve provider error payload as-is for load status error message.
            raise GenosServiceException("1", e.raw_error_message) from e

    def _normalize_runtime_kwargs(self, kwargs: dict) -> dict:
        return rk.normalize_runtime_kwargs(self, kwargs)

    def _configure_runtime_image_mode(self, kwargs: dict):
        rk.configure_runtime_image_mode(self, kwargs)

    def _get_or_create_image_description_enricher(self) -> ImageDescriptionEnricher:
        enricher = getattr(self, "image_description_enricher", None)
        if enricher is None:
            # 테스트 등에서 __init__ 우회 시 legacy attribute 기반으로 재구성
            legacy_options = ImageDescriptionOptions.from_legacy_processor(self)
            enricher = ImageDescriptionEnricher(legacy_options)
            self.image_description_enricher = enricher
        return enricher

    def enrich_image_descriptions(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = self._get_or_create_image_description_enricher()
        return enricher.enrich(document, **kwargs)

    def _get_or_create_doc_summary_enricher(self) -> DocSummaryEnricher:
        enricher = getattr(self, "doc_summary_enricher", None)
        if enricher is None:
            base = getattr(self, "_base_doc_summary_options", None)
            enricher = DocSummaryEnricher(base or DocSummaryOptions())
            self.doc_summary_enricher = enricher
        return enricher

    def enrich_doc_summary(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = self._get_or_create_doc_summary_enricher()
        return enricher.enrich(document, **kwargs)

    def _get_or_create_table_description_enricher(self) -> TableDescriptionEnricher:
        enricher = getattr(self, "table_description_enricher", None)
        if enricher is None:
            base = getattr(self, "_base_table_description_options", None)
            enricher = TableDescriptionEnricher(base or TableDescriptionOptions())
            self.table_description_enricher = enricher
        return enricher

    def enrich_table_descriptions(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = self._get_or_create_table_description_enricher()
        return enricher.enrich(document, **kwargs)

    async def enrich_metadata(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = getattr(self, "metadata_enricher", None)
        if enricher is not None:
            document = await enricher.enrich(document, **kwargs)
        return document

    async def enrich_custom_fields(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        for enricher in self.custom_fields_enrichers:
            document = await enricher.enrich(document, **kwargs)
        return document

    def check_glyph_text(self, text: str, threshold: int = 1) -> bool:
        return dops.check_glyph_text(text, threshold)

    def check_glyphs(self, document: DoclingDocument) -> bool:
        return dops.check_glyphs(document, self._glyph_document_threshold)

    def check_empty_text(self, document: DoclingDocument) -> bool:
        return dops.check_empty_text(document)

    def ocr_all_table_cells(self, document: DoclingDocument, pdf_path) -> DoclingDocument:
        """글리프 깨진 텍스트가 있는 표에 대해서만 셀 단위 재OCR 을 수행한다."""
        return dops.ocr_all_table_cells(
            document,
            ocr_endpoint=self.ocr_endpoint,
            cell_threshold=self._glyph_table_cell_threshold,
            timeout=self._table_cell_ocr_timeout,
        )


# ============================================================
# HwpDocumentLoader — HWP/HWPX 전용 (from attachment_processor.py)
# load_documents() 메서드만 포함
# ============================================================

class HwpDocumentLoader:

    def __init__(self):
        pass

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
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


# ============================================================
# DocxDocumentLoader — DOCX 전용 (from attachment_processor.py)
# load_documents() 메서드만 포함
# ============================================================

class DocxDocumentLoader:

    def __init__(self):
        self.pipeline_options = PipelineOptions()
        self.converter = DocumentConverter(
            format_options={
                InputFormat.DOCX: WordFormatOption(
                    pipeline_cls=SimplePipeline, backend=GenosMsWordDocumentBackend
                ),
            }
        )

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)
        return conv_result.document


# ============================================================
# GenericDocumentLoader — 기타 포맷 (from attachment_processor.py)
# load_documents() 메서드만 포함
# ============================================================

def _file_looks_like_text(file_path: str) -> bool:
    """파일 앞부분이 텍스트로 보이는지. 읽을 수 없으면 False(=텍스트로 단정하지 않음)."""
    try:
        with open(file_path, "rb") as f:
            return _looks_like_text(f.read(512))
    except OSError:
        return False


class GenericDocumentLoader:

    def __init__(self):
        pass

    def get_real_file_type(self, file_path: str) -> str:
        with open(file_path, 'rb') as f:
            header = f.read(8)
        if header.startswith(b'%PDF-'):
            return 'pdf'
        elif header.startswith(b'\x89PNG'):
            return 'png'
        elif header.startswith(b'\xff\xd8\xff'):
            return 'jpg'
        return os.path.splitext(file_path)[-1].lower()

    def get_loader(self, file_path: str):
        ext = os.path.splitext(file_path)[-1].lower()
        real_type = self.get_real_file_type(file_path)

        if ext != real_type and real_type == 'pdf':
            return PyMuPDFLoader(file_path)
        elif ext != real_type and real_type in ['txt', 'json', 'md']:
            return TextLoader(file_path)
        elif ext == '.pdf':
            return PyMuPDFLoader(file_path)
        elif ext == '.doc':
            convert_to_pdf(file_path)
            return UnstructuredWordDocumentLoader(file_path)
        elif ext in ['.ppt', '.pptx']:
            convert_to_pdf(file_path)
            return UnstructuredPowerPointLoader(file_path)
        elif ext in ['.jpg', '.jpeg', '.png']:
            convert_to_pdf(file_path)
            return UnstructuredImageLoader(file_path, languages=["kor", "eng"])
        elif ext in ['.txt', '.json', '.md']:
            # .md 는 기본적으로 docling 분기에서 처리된다. 여기로 오는 건
            # formats.md.processing_mode=text 인 레거시 경로뿐이다.
            return TextLoader(file_path)
        elif _file_looks_like_text(file_path):
            # 모르는 확장자라도 내용이 텍스트면 TextLoader 로 읽는다. Unstructured 는 무거운
            # 선택 의존이라 오프라인 배포본에는 없을 수 있는데, 그때 ImportError 로 죽는 대신
            # 최소한 본문은 살린다. 구조(헤딩/표)까지 살리려면 아래 안내대로 별칭을 지정한다.
            _log.warning(
                f"[GenericDocumentLoader] 모르는 확장자 '{ext}' — 구조 없는 텍스트로 읽습니다. "
                "표준 포맷으로 처리하려면 파서 설정의 formats.extension_aliases 에 "
                f"'{ext}' 별칭을 지정하세요(예: \"{ext}\": \".md\")."
            )
            return TextLoader(file_path)
        else:
            return UnstructuredFileLoader(file_path)

    def _load_image_documents_fallback(self, file_path: str) -> list[Document]:
        """UnstructuredImageLoader의 __str__ NoneType 오류를 우회해 이미지 요소를 안전하게 적재."""
        from unstructured.partition.image import partition_image

        elements = partition_image(filename=file_path, languages=["kor", "eng"])
        documents: list[Document] = []

        for element in elements:
            text = getattr(element, "text", "")
            if text is None:
                text = ""
            elif not isinstance(text, str):
                text = str(text)

            metadata: dict[str, Any] = {"source": file_path}
            if hasattr(element, "metadata") and element.metadata is not None:
                try:
                    metadata.update(element.metadata.to_dict())
                except Exception:
                    pass

            if hasattr(element, "category"):
                metadata["category"] = element.category

            if hasattr(element, "to_dict"):
                element_id = element.to_dict().get("element_id")
                if element_id:
                    metadata["element_id"] = element_id

            documents.append(Document(page_content=text, metadata=metadata))

        return documents

    def load_documents(self, file_path: str, **kwargs: dict) -> list:
        try:
            loader = self.get_loader(file_path)
        except ImportError as exc:
            # unstructured 는 선택 의존이라 오프라인 배포본에는 없을 수 있다. 원인과 조치를
            # 알 수 있게 바꿔 던진다(텍스트 파일은 위에서 TextLoader 로 빠지므로 여기 안 온다).
            raise GenosServiceException(
                "1",
                f"이 형식은 unstructured 패키지가 있어야 처리됩니다({exc}). "
                f"표준 포맷으로 변환해 올리거나 파서 설정의 formats.extension_aliases 로 "
                f"처리할 포맷을 지정하세요: {os.path.basename(file_path)}",
            ) from exc
        ext = os.path.splitext(file_path)[-1].lower()
        try:
            documents = loader.load()
        except TypeError as exc:
            if ext in ['.jpg', '.jpeg', '.png'] and "__str__ returned non-string" in str(exc):
                _log.warning(f"[GenericDocumentLoader] Image loader fallback: {file_path} ({exc})")
                documents = self._load_image_documents_fallback(file_path)
            else:
                raise

        if ext in ['.jpg', '.jpeg', '.png']:
            if not documents or not any((doc.page_content or "").strip() for doc in documents):
                documents = [Document(page_content=".", metadata={'source': file_path, 'page': 0})]

        return documents


# ============================================================
# GenosServiceException
# ============================================================

class GenosServiceException(Exception):
    def __init__(self, error_code: str, error_msg: Optional[str] = None,
                 msg_params: Optional[dict] = None,
                 *, stage: Optional[str] = None, error_type: Optional[str] = None) -> None:
        self.code = 1
        self.error_code = error_code
        self.error_msg = error_msg or "GenOS Service Exception"
        self.msg_params = msg_params or {}
        # #329: 실패 단계(stage)와 성격(error_type: transient/permanent/timeout).
        self.stage = stage
        self.error_type = error_type

    def __repr__(self) -> str:
        return f"GenosServiceException(code={self.code!r}, errMsg={self.error_msg!r})"


# ============================================================
# DocumentProcessor — 메인 클래스
# ============================================================

class DocumentProcessor:
    """
    파싱 단계만 수행하고 결과를 JSON으로 반환하는 파사드.
    청킹/벡터 조합은 수행하지 않음.

    IS_PARSER: main.py 가 이 프로세서가 /parser API 전용임을 식별하는 데 사용.
    """

    IS_PARSER: bool = True

    def __init__(self, config_path: str | None = None):
        if config_path is None:
            config_path = _resolve_default_parser_config_path()

        cfg = _load_config(config_path)
        self._intel = IntelligentDocumentProcessor(cfg, config_path=config_path)

        # xlsx/csv · md 처리 설정은 intel 프로세서가 동일 config에서 이미 파싱함 → 재사용
        self._xlsx_cfg = self._intel._xlsx_cfg
        self._md_cfg = self._intel._md_cfg
        # enrichment.custom_fields 중 tabular_mapping handler를 시작 시 1회 로드한다.
        self._config_dir = self._intel._config_dir
        self._tabular_custom_fields_mappers = self._build_tabular_custom_fields_mappers(
            self._intel.custom_fields_cfgs
        )

        defaults_cfg = _as_dict(cfg.get("defaults"))
        log_level = _parse_optional_int(defaults_cfg.get("log_level"), "defaults.log_level")
        if log_level is None:
            log_level = 4
        self._log_level = log_level

        self._hwp = HwpDocumentLoader()
        self._docx = DocxDocumentLoader()
        self._generic = GenericDocumentLoader()

        # 신/구 설정 스키마 동시 지원
        whisper_cfg = _as_dict(cfg.get("whisper"))
        attach_cfg = _as_dict(cfg.get("attachment"))

        self._whisper_url = whisper_cfg.get("url", attach_cfg.get("whisper_url", ""))
        self._whisper_req_data = {
            "model": whisper_cfg.get("model", attach_cfg.get("whisper_model", "model")),
            "language": whisper_cfg.get("language", attach_cfg.get("whisper_language", "ko")),
            "response_format": whisper_cfg.get(
                "response_format", attach_cfg.get("whisper_response_format", "json")
            ),
            "temperature": whisper_cfg.get("temperature", attach_cfg.get("whisper_temperature", "0")),
            "stream": whisper_cfg.get("stream", attach_cfg.get("whisper_stream", "false")),
            "timestamp_granularities[]": whisper_cfg.get(
                "timestamp_granularities", attach_cfg.get("whisper_timestamp_granularities", "word")
            ),
        }
        try:
            self._whisper_chunk_sec = int(
                whisper_cfg.get("chunk_sec", attach_cfg.get("whisper_chunk_sec", 29))
            )
        except (TypeError, ValueError):
            _log.warning("[DocumentProcessor] Invalid whisper.chunk_sec value, fallback to 29")
            self._whisper_chunk_sec = 29

        try:
            self._whisper_chunk_overlap_ms = int(
                whisper_cfg.get("chunk_overlap_ms", attach_cfg.get("whisper_chunk_overlap_ms", 300))
            )
        except (TypeError, ValueError):
            _log.warning("[DocumentProcessor] Invalid whisper.chunk_overlap_ms value, fallback to 300")
            self._whisper_chunk_overlap_ms = 300

        output_cfg = _as_dict(cfg.get("output"))
        self._output_format = self._normalize_output_format(output_cfg.get("format", "json"))
        self._table_format = self._normalize_table_format(output_cfg.get("table_format", "html"))
        # markdown 표 compact(컬럼 정렬 패딩 제거) 여부. 기본 True. html 포맷엔 무관.
        # 공용 헬퍼를 쓴다 - 다른 facade 와 같은 규칙으로 문자열 "false" 도 off 로 읽는다.
        self._compact_tables = cp.resolve_compact_tables(output_cfg)

        # 민감정보 분류(#315): parser 가 호출 주체. guardrail_call 요청 시 문서 전체를 1회 분류해
        # sensitive_infos 를 파스 출력에 실어 chunking 으로 전달(chunking 은 청크에 적용만 = 병합).
        self._gr_cfg = gr.GuardrailConfig.from_cfg(cfg)

        # PPT 페이지 단위 image description(page-level). config: formats.ppt.page_description.
        # 파서는 PPT 를 (레거시 langchain 대신) PDF→docling 으로 재라우팅해 페이지 설명을 주입한다.
        formats_cfg = _as_dict(cfg.get("formats"))
        ppt_pd_cfg = _as_dict(_as_dict(formats_cfg.get("ppt")).get("page_description"))
        self._page_desc_options = PageDescriptionOptions.from_config(ppt_pd_cfg, self._intel._config_dir)
        self._ppt_pdf_converter = None

        # HTML flatten 전처리 모드. docling 은 <iframe srcdoc="..."> 속성 안의 본문,
        # 접힌 아코디언, <li>와 중첩 목록 사이에 div가 낀 구조를 누락할 수 있다.
        # auto: 원문 스캔/DOM 사전검사로 그런 구조적 결함이 감지될 때만 flatten(기본)
        # always: 항상 flatten  |  off: 전처리 없음(기존 동작)
        html_cfg = _as_dict(formats_cfg.get("html"))
        self._html_flatten_mode = self._normalize_flatten_mode(html_cfg.get("flatten", "auto"))

        # enrichment.custom_fields 중 json 블록을 가진 설정(= .json 입력에서 본문 텍스트를
        # 꺼낼 key 목록)을 시작 시 1회 로드한다. tabular_mapping 과 같은 패턴.
        self._json_text_specs = self._build_json_text_specs(self._intel.custom_fields_cfgs)

        # 문서 단위 custom_fields의 markdown.front_matter 설정. doc_type별로 원천 YAML의
        # metadata 승격 필드와 청크 텍스트 제외 필드를 독립 선택한다.
        self._markdown_front_matter_specs = self._build_markdown_front_matter_specs(
            self._intel.custom_fields_cfgs
        )

        # 문서 단위 custom_fields의 markdown.text_fence 설정. PDF 레이아웃 보존용 ```text
        # 펜스를 docling 변환 전에 논리 단위 단락으로 되돌린다(안 하면 펜스 본문 전체가
        # CodeItem 하나가 되어 chunk_size 가 무의미해진다).
        self._markdown_text_fence_specs = self._build_markdown_text_fence_specs(
            self._intel.custom_fields_cfgs
        )

        # 문서 단위 custom_fields 의 html.marker_headings 설정. h태그 없이 도형 마커로만 계층을
        # 표현하는 원천(고객센터 카드 HTML)에서 그 마커 줄을 섹션 헤더로 승격한다. 대상 doc_type
        # 이 아니면 사유 계산 자체를 켜지 않아 다른 원천의 flatten auto 동작을 건드리지 않는다.
        self._html_marker_heading_doc_types = self._build_html_marker_heading_doc_types(
            self._intel.custom_fields_cfgs
        )
        # 같은 원문이 md 로도 온다. 판정 규칙은 converters 쪽에서 공유하므로 스위치만 나란히 둔다.
        self._markdown_marker_heading_doc_types = self._build_marker_heading_doc_types(
            build_markdown_marker_heading_doc_types, self._intel.custom_fields_cfgs, "markdown"
        )

        # enrichment.custom_fields 중 extractor=json_mapping 설정(= JSON 레코드 → 목표필드
        # 매핑). 문서 모드(json:)보다 우선하며, 레코드마다 청크 메타데이터를 따로 싣는다.
        self._json_records_mappers = self._build_json_records_mappers(self._intel.custom_fields_cfgs)
        # json_mapping/tabular_mapping 이 선언한 LLM 생성 필드용 enricher(설정 파일당 1개).
        # 설정/프롬프트 파일 오류가 첫 요청이 아니라 기동 시 드러나도록 여기서 미리 만든다.
        self._llm_field_enrichers: dict = {}
        for mapper in (*self._json_records_mappers, *self._tabular_custom_fields_mappers):
            for llm_spec in getattr(mapper, "llm_field_specs", ()):
                self._llm_field_enricher(llm_spec, mapper)

    @staticmethod
    def _normalize_output_format(value: Any) -> str:
        fmt = str(value).strip().lower()
        if fmt not in {"json", "html", "markdown", "docling"}:
            _log.warning(f"[DocumentProcessor] Invalid output.format '{value}', fallback to 'json'")
            return "json"
        return fmt

    @staticmethod
    def _normalize_table_format(value: Any) -> str:
        """output.table_format 설정을 읽는다. auto 는 표마다 구조를 보고 정해진다."""
        return cp.resolve_table_format_setting({"table_format": value})

    @staticmethod
    def _normalize_flatten_mode(value: Any) -> str:
        mode = str(value).strip().lower()
        if mode not in {"auto", "always", "off"}:
            _log.warning(
                f"[DocumentProcessor] Invalid formats.html.flatten '{value}', fallback to 'auto'"
            )
            return "auto"
        return mode

    @staticmethod
    def _build_json_text_specs(custom_fields_cfgs: list) -> list:
        """custom_fields 설정 중 `json:` 블록을 가진 것만 JsonTextSpec 으로 만든다."""
        from genon.preprocessor.converters.json_text import JsonTextSpec

        specs = []
        for config in custom_fields_cfgs or []:
            json_cfg = _as_dict(config.get("json"))
            if not json_cfg:
                continue
            doc_types = normalize_doc_types(config.get("doc_type"))
            try:
                specs.append(JsonTextSpec(json_cfg, doc_types))
            except ValueError as exc:
                raise GenosServiceException(
                    "1", f"custom_fields.json 설정 오류: {exc}", stage="custom_fields"
                ) from exc
        return specs

    @staticmethod
    def _build_markdown_front_matter_specs(custom_fields_cfgs: list) -> list:
        try:
            return build_markdown_front_matter_specs(custom_fields_cfgs)
        except (ValueError, TypeError) as exc:
            raise GenosServiceException(
                "1", f"custom_fields markdown.front_matter 설정 오류: {exc}",
                stage="custom_fields",
            ) from exc

    @staticmethod
    def _build_markdown_text_fence_specs(custom_fields_cfgs: list) -> list:
        try:
            return build_markdown_text_fence_specs(custom_fields_cfgs)
        except (ValueError, TypeError) as exc:
            raise GenosServiceException(
                "1", f"custom_fields markdown.text_fence 설정 오류: {exc}",
                stage="custom_fields",
            ) from exc

    @staticmethod
    def _build_marker_heading_doc_types(builder, custom_fields_cfgs: list, fmt: str) -> frozenset:
        try:
            return builder(custom_fields_cfgs)
        except (ValueError, TypeError, FileNotFoundError) as exc:
            raise GenosServiceException(
                "1", f"custom_fields {fmt} 설정 오류: {exc}", stage="custom_fields"
            ) from exc

    @classmethod
    def _build_html_marker_heading_doc_types(cls, custom_fields_cfgs: list) -> frozenset:
        return cls._build_marker_heading_doc_types(
            build_html_marker_heading_doc_types, custom_fields_cfgs, "html"
        )

    @staticmethod
    def _build_tabular_custom_fields_mappers(custom_fields_cfgs: list) -> list:
        """custom_fields 설정 중 extractor=tabular_mapping 만 매퍼로 만든다.

        json 쪽과 같이 감싸는 이유: 감싸지 않으면 설정 오류가 raw 로 __init__ 을 뚫고 나가
        **서비스 import 자체가 죽는다**(어느 설정이 문제인지도 드러나지 않는다).
        TypeError 도 잡는다 — `constants: 5` 처럼 dict() 강제 변환이 TypeError 를 내는 경우가 있다.
        """
        try:
            return build_tabular_custom_fields_mappers(custom_fields_cfgs)
        except (ValueError, TypeError, FileNotFoundError) as exc:
            raise GenosServiceException(
                "1", f"custom_fields tabular_mapping 설정 오류: {exc}", stage="custom_fields"
            ) from exc

    @staticmethod
    def _build_json_records_mappers(custom_fields_cfgs: list) -> list:
        """custom_fields 설정 중 JSON 레코드/의미 계열(json_mapping/json_records/json_semantic)을
        모두 매퍼로 만들어 합친다. tabular 와 같은 패턴이되, 두 빌더에 전체 목록을 그대로
        넘긴다 — 각 빌더가 자기 extractor 집합(json_records.JSON_RECORD_EXTRACTORS /
        json_semantic.JSON_SEMANTIC_EXTRACTORS)에 속하는 설정만 스스로 고른다
        (custom_fields_enricher.py 의 집합 분리 참고). 여기서 미리 걸러줄 필요가 없다.
        """
        try:
            mappers: list = list(build_json_records_mappers(custom_fields_cfgs))
            mappers.extend(build_semantic_json_mappers(custom_fields_cfgs))
            return mappers
        except (ValueError, TypeError, FileNotFoundError) as exc:
            raise GenosServiceException(
                "1", f"custom_fields json_mapping/json_semantic 설정 오류: {exc}", stage="custom_fields"
            ) from exc

    def _json_records_mapper_for(self, runtime_doc_type: Any):
        """런타임 doc_type 에 매칭되는 json_mapping 매퍼. 없으면 None(다음 경로로 폴백)."""
        return self._single_json_match(
            [m for m in self._json_records_mappers if m.matches(runtime_doc_type)],
            runtime_doc_type,
            "json_mapping",
        )

    def _json_text_spec_for(self, runtime_doc_type: Any):
        """런타임 doc_type 에 매칭되는 json 설정. 없으면 None(기존 경로 폴백)."""
        return self._single_json_match(
            [
                spec for spec in self._json_text_specs
                if not spec.doc_types or normalize_doc_type(runtime_doc_type) in spec.doc_types
            ],
            runtime_doc_type,
            "json",
        )

    def _markdown_front_matter_spec_for(self, runtime_doc_type: Any):
        return self._single_json_match(
            [
                spec for spec in self._markdown_front_matter_specs
                if spec.matches(runtime_doc_type)
            ],
            runtime_doc_type,
            "markdown.front_matter",
        )

    def _markdown_text_fence_spec_for(self, runtime_doc_type: Any):
        """런타임 doc_type 에 매칭되는 text_fence 설정. 없으면 None(전처리 없이 파싱)."""
        return self._single_json_match(
            [
                spec for spec in self._markdown_text_fence_specs
                if not spec.doc_types or normalize_doc_type(runtime_doc_type) in spec.doc_types
            ],
            runtime_doc_type,
            "markdown.text_fence",
        )

    def _html_marker_headings_enabled(self, runtime_doc_type: Any) -> bool:
        """런타임 doc_type 이 마커 승격 대상인지."""
        return normalize_doc_type(runtime_doc_type) in self._html_marker_heading_doc_types

    def _markdown_marker_headings_enabled(self, runtime_doc_type: Any) -> bool:
        """런타임 doc_type 이 md 마커 승격 대상인지."""
        return normalize_doc_type(runtime_doc_type) in self._markdown_marker_heading_doc_types

    @staticmethod
    def _single_json_match(matching: list, runtime_doc_type: Any, label: str):
        """doc_type 매칭 결과가 1개 이하인지 확인하고 반환한다(중복 설정은 즉시 실패)."""
        if len(matching) > 1:
            raise GenosServiceException(
                "1",
                f"동일 doc_type에 {label} custom_fields 설정이 여러 개입니다: {runtime_doc_type}",
            )
        return matching[0] if matching else None

    # ------------------------------------------------------------------
    # 포맷별 파싱 메서드
    # ------------------------------------------------------------------

    def _parse_docling(
        self, file_path: str, artifacts_from: str | None = None, **kwargs
    ) -> DoclingDocument:
        """
        intelligent_processor.__call__ 흐름 중 enrichment 까지만 실행.
        load → OCR 검사 → ocr_all_table_cells → enrichment

        artifacts_from: 이미지 artifacts 경로 계산에 쓸 '원본' 파일 경로. html flatten /
            json 병합처럼 파싱 대상이 파생 임시 파일일 때, media_files 경로가 원본
            기준으로 유지되도록 원본 경로를 넘긴다. 미지정 시 file_path 를 쓴다.
        """
        ocr_mode = getattr(self._intel, "ocr_mode", "auto")

        if ocr_mode == "force":
            document = self._intel.load_documents_with_docling_ocr(file_path, **kwargs)
        else:
            document = self._intel.load_documents(file_path, **kwargs)
            if ocr_mode == "auto":
                # #329(task#1): /run(_load_document)과 동일한 auto 재OCR 휴리스틱으로 정합.
                # 기존엔 check_empty_text 조건이 빠져 있어, /run 은 재OCR 하는 '텍스트 없는'
                # 문서를 /parse 는 재OCR 하지 않아 다운스트림 청크가 달라졌다.
                if (not check_document(document, self._intel.enrichment_options)
                        or self._intel.check_glyphs(document)
                        or self._intel.check_empty_text(document)):
                    document = self._intel.load_documents_with_docling_ocr(file_path, **kwargs)

        if ocr_mode != "disable" and self._intel.ocr_endpoint:
            document = self._intel.ocr_all_table_cells(document, file_path)

        # #329(task#1): /run(_document_to_vectors)과 동일하게 picture/table 이미지 참조를
        # 설정한다. chunking_processor.compose_vectors 의 set_media_files/get_media_files 는
        # item.image.uri 를 읽어 media_files 를 구성하는데, 그 uri 는 파싱 단계에서 설정돼야
        # 한다(청커는 설정하지 않음). 이게 빠져 있으면 /parse→/chunk 의 media_files 가 비어
        # /run 과 달라진다. PNG 는 공유 NFS(artifacts_dir=파일 경로 기준)에 저장돼 /chunk 가
        # 같은 경로로 minio 업로드한다.
        output_path, output_file = os.path.split(artifacts_from or file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(output_path) / filename  # 빈 output_path 가 절대경로(/filename)로 바뀌는 것 방지
        reference_path = None if artifacts_dir.is_absolute() else artifacts_dir.parent

        document = document._with_pictures_refs(
            image_dir=artifacts_dir, page_no=None, reference_path=reference_path
        )
        # 표 이미지 저장: config on 이고 임베디드 intel 이 해당 기능을 지원할 때만.
        # (parser 임베디드 IntelligentDocumentProcessor 는 경량 사본이라 이 기능이 없을 수 있음 —
        #  없으면 조용히 skip. 파스는 원래 표 이미지 미생성이므로 현행 동작 보존.)
        if getattr(self._intel, "table_image_enabled", False) and hasattr(self._intel, "_save_table_images"):
            self._intel._save_table_images(
                document, image_dir=artifacts_dir, reference_path=reference_path
            )

        document = self._intel.enrichment(document, **kwargs)

        return document

    def _parse_hwp_hwpx(self, file_path: str, **kwargs) -> DoclingDocument:
        """HwpDocumentLoader.load_documents() 만 실행. 실패 시 폴백 적용.

        .hml 은 레거시 백엔드가 없어 SDK 실패 시 폴백 없이 그대로 예외를 올린다 (이슈 #323).
        """
        ext = os.path.splitext(file_path)[-1].lower()
        try:
            return self._hwp.load_documents(file_path, **kwargs)
        except Exception as sdk_err:
            _log.warning(f"[DocumentProcessor] HWP SDK 실패: {sdk_err}")
            if ext in (".hwp", ".hwpx"):
                try:
                    return self._hwp.load_documents(
                        file_path, **dict(kwargs, use_hwp_sdk=False)
                    )
                except Exception:
                    # 모든 백엔드 실패 시 LibreOffice → PDF → intelligent 경로
                    converted = convert_to_pdf(file_path)
                    if converted:
                        return self._parse_docling(converted, **kwargs)
                    # 이슈 #286 — HWP SDK 도 실패하고 LibreOffice(이 경로의 유일한 변환기)마저
                    # 없으면, 원인을 명확히 안내한다 (혼란스러운 SDK 에러 대신 PDF 직접 입력/재빌드).
                    if not _is_libreoffice_available():
                        raise GenosServiceException(
                            1,
                            f"이 전처리기 이미지에는 PDF 변환기(LibreOffice)가 설치되어 "
                            f"있지 않아 '{os.path.basename(file_path)}' 처리에 실패했습니다. "
                            f"PDF 로 변환한 파일을 입력하거나, 변환기를 포함해 전처리기 이미지를 다시 "
                            f"빌드하세요 (genon/README.md 참고).",
                        ) from sdk_err
                    raise sdk_err
            raise

    def _parse_docx(self, file_path: str, **kwargs) -> DoclingDocument:
        return self._docx.load_documents(file_path, **kwargs)

    def _parse_audio(self, file_path: str, **kwargs) -> str:
        tmp_path = f"./tmp_audios_{os.path.basename(file_path).split('.')[0]}"
        if not os.path.exists(tmp_path):
            os.makedirs(tmp_path)
        try:
            loader = AudioLoader(
                file_path=file_path,
                req_url=self._whisper_url,
                req_data=self._whisper_req_data,
                chunk_sec=self._whisper_chunk_sec,
                chunk_overlap_ms=self._whisper_chunk_overlap_ms,
                tmp_path=tmp_path,
            )
            audio_chunks = loader.split_file_as_chunks()
            return loader.transcribe_audio(audio_chunks)
        finally:
            try:
                subprocess.run(["rm", "-r", tmp_path], check=True)
            except Exception:
                pass

    def _parse_tabular(self, file_path: str) -> dict:
        """xlsx/csv → {"data":[{"sheet_name","title","data_rows":[{col:val}]}]} (이슈 #288).

        표 감지(멀티헤더 자동 + 1시트 복수표)는 xlsx_processor.load_tables 에 위임한다.
        - 제목행은 title 로(컨텍스트), 계층 헤더는 `상위_하위` flatten, 그 아래 컬럼명행이 leaf.
        - multi_table=True 면 빈 행 기준 복수 표를 표별로 분리.
        헤더명(원본, 한글 가능)을 그대로 key 로 쓴다(HTML 셀 내용 — Weaviate 키 제약 무관).
        """
        from genon.preprocessor.converters.xlsx_processor import build_tabular_data_dict

        return build_tabular_data_dict(
            file_path,
            header_row=self._xlsx_cfg["header_row"],
            multi_table=self._xlsx_cfg["multi_table"],
        )

    def _parse_other(self, file_path: str, **kwargs) -> list:
        return self._generic.load_documents(file_path, **kwargs)

    # ------------------------------------------------------------------
    # HTML flatten 전처리 / JSON 본문 추출
    # ------------------------------------------------------------------

    @staticmethod
    def _prepare_markdown(
        file_path: str, work_dir: str, fm_spec, fence_spec, marker_headings: bool = False
    ) -> tuple[str, dict]:
        """Front matter / text_fence 전처리를 적용한 파싱 경로와 enrichment context를 반환.

        둘 중 하나라도 텍스트를 바꿨을 때만 파생 파일을 쓴다. 바뀐 것이 없으면 원본 경로를
        그대로 돌려주므로 docling 입력은 물론 artifacts 경로도 기존과 동일하다.
        """
        context: dict = {}
        text: str | None = None   # 전처리로 실제로 바뀐 텍스트만 담는다(None = 원본 그대로)

        if fm_spec is not None:
            try:
                parsed = fm_spec.parse(file_path)
            except ValueError as exc:
                raise GenosServiceException(
                    "1", str(exc), stage="custom_fields"
                ) from exc
            if parsed.found:
                context = {
                    "metadata": dict(parsed.metadata),
                    "prompt_prefix": parsed.prompt_prefix,
                    "source_fields": list(parsed.source_fields),
                }
                text = parsed.filtered_text

        if fence_spec is not None:
            source = text
            if source is None:
                try:
                    source = Path(file_path).read_text(encoding="utf-8-sig")
                except (OSError, UnicodeError) as exc:
                    # 읽을 수 없으면 전처리를 건너뛰고 원본 경로로 파싱한다(기존 동작).
                    _log.warning(f"[DocumentProcessor] markdown.text_fence 입력 읽기 실패: {exc}")
                    return file_path, context
            fenced, converted = fence_spec.apply(source)
            if converted:
                _log.info(
                    f"[DocumentProcessor] markdown.text_fence: {converted}개 펜스 블록을 "
                    f"단락으로 복원 ({Path(file_path).name})"
                )
                text = fenced

        if marker_headings:
            # text_fence 뒤에 돈다 — 펜스를 단락으로 되돌린 뒤라야 그 안의 마커 줄도 후보가 된다.
            from genon.preprocessor.converters.md_marker_headings import (
                promote_markdown_marker_headings,
            )

            source = text
            if source is None:
                try:
                    source = Path(file_path).read_text(encoding="utf-8-sig")
                except (OSError, UnicodeError) as exc:
                    _log.warning(
                        f"[DocumentProcessor] markdown.marker_headings 입력 읽기 실패: {exc}"
                    )
                    return file_path, context
            promoted, count = promote_markdown_marker_headings(source)
            if count:
                _log.info(
                    f"[DocumentProcessor] markdown.marker_headings: {count}개 마커 줄을 "
                    f"heading 으로 승격 ({Path(file_path).name})"
                )
                text = promoted

        if text is None:
            return file_path, context

        # 원본과 같은 basename을 유지해 Docling origin.filename이 임시 이름으로 바뀌지 않게 한다.
        out_path = Path(work_dir) / Path(file_path).name
        try:
            out_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise GenosServiceException(
                "1", f"Markdown 전처리 파일 생성 실패: {exc}",
                stage="custom_fields",
            ) from exc
        return str(out_path), context

    def _prepare_html(self, file_path: str, work_dir: str, marker_headings: bool = False) -> str:
        """필요 시 HTML 을 flatten 해 새 경로를 돌려준다. 불필요하면 원본 경로 그대로.

        docling 의 HTML 백엔드가 읽지 못하는 iframe srcdoc/escape 본문과, 접힌
        아코디언·wrapper 안의 중첩 목록을 사전검사한다. 결함이 감지된 문서만 정리해
        정상 HTML 의 기존 파싱 경로는 유지한다.

        ``marker_headings`` 는 대상 doc_type 일 때만 True 다 — 그때만 도형 마커 소제목을
        섹션 헤더로 승격하는 사유를 계산·적용한다.
        """
        if self._html_flatten_mode == "off":
            return file_path

        from genon.preprocessor.converters import html_flatten

        try:
            raw = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            _log.warning(f"[parser] html flatten 사전검사 실패(원본으로 진행): {exc}")
            return file_path

        reasons = html_flatten.precheck_html(raw, detect_marker_headings=marker_headings)
        if self._html_flatten_mode == "auto" and not reasons:
            return file_path

        stem = Path(file_path).stem
        try:
            flattened = html_flatten.flatten_html(
                raw, html_flatten.document_title(raw, stem), reasons, marker_headings=marker_headings
            )
        except Exception as exc:
            # 전처리 실패가 파싱 자체를 막지 않도록 원본으로 폴백한다.
            _log.warning(f"[parser] html flatten 실패(원본으로 진행): {exc}")
            return file_path

        out_path = os.path.join(work_dir, f"{stem}.html")
        with open(out_path, "w", encoding="utf-8") as fp:
            fp.write(flattened)
        _log.info(
            f"[parser] html flatten 적용(mode={self._html_flatten_mode}, "
            f"사유={reasons or 'always'}): {len(raw):,} → {len(flattened):,} bytes"
        )
        return out_path

    def _warn_if_thin_html(self, file_path: str, doc: DoclingDocument) -> None:
        """원문은 큰데 추출 텍스트가 거의 없으면 경고만 남긴다(재파싱하지 않음).

        사전검사는 flatten 으로 복구 가능한 결함만 잡는다. SPA 가 본문을 하이드레이션
        JSON 에만 담은 경우는 flatten 으로도 복구되지 않으므로, 재파싱 대신 운영자가
        알아챌 수 있게 로그만 남긴다.
        """
        from genon.preprocessor.converters import html_flatten

        try:
            raw_size = os.path.getsize(file_path)
            # export_to_text() 는 큰 문서에서 비싸다 — 판정 하한을 못 넘는 문서는 애초에
            # 대상이 아니므로 먼저 걸러 텍스트 export 자체를 건너뛴다.
            if raw_size < html_flatten.THIN_MIN_RAW_SIZE:
                return
            text_len = len(doc.export_to_text() or "")
        except Exception:
            return
        if html_flatten.looks_thin(raw_size, text_len):
            _log.warning(
                f"[parser] HTML 추출 텍스트가 비정상적으로 적습니다 "
                f"({raw_size:,} bytes → {text_len:,}자). 본문이 스크립트/동적 렌더링에만 "
                f"있을 수 있습니다: {os.path.basename(file_path)}"
            )

    @staticmethod
    def _load_json_payload(file_path: str) -> Any:
        """`.json` 입력을 읽는다. 읽기/파싱 실패는 입력 오류로 즉시 종료."""
        try:
            with open(file_path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except (OSError, ValueError) as exc:
            raise GenosServiceException(
                "1", f"JSON 파일을 읽을 수 없습니다: {os.path.basename(file_path)} ({exc})"
            ) from exc

    def _llm_field_enricher(self, spec, mapper):
        """llm_fields 항목용 CustomFieldsEnricher(항목당 1개, 생성 후 캐시).

        LLM 설정은 항목에 인라인돼 있을 수도, config_file 로 분리돼 있을 수도 있다. 어느 쪽이든
        spec.enricher_kwargs 로 통일돼 오므로 여기서는 구분하지 않는다. 캐시 키는 스펙 객체 자체다
        — 스펙은 기동 시 1회 생성되어 매퍼가 붙들고 있으므로 identity 가 안정적이다.

        json_mapping(JSON 레코드)과 tabular_mapping(Excel 행) 양쪽이 같은 스펙 타입을 쓰므로
        이 경로를 공유한다.
        """
        enricher = self._llm_field_enrichers.get(spec)
        if enricher is None:
            from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
                CustomFieldsEnricher,
            )
            try:
                enricher = CustomFieldsEnricher(
                    resource_path=mapper.resource_path, **spec.enricher_kwargs
                )
            except (ValueError, FileNotFoundError, TypeError) as exc:
                raise GenosServiceException(
                    "1", f"llm_fields 설정 오류({spec.label}): {exc}", stage="custom_fields"
                ) from exc
            self._llm_field_enrichers[spec] = enricher
        return enricher

    async def _apply_llm_fields(self, mapper, fields_list: list) -> list:
        """`llm_fields` 선언대로 LLM 을 호출해 목표필드를 채운다.

        기본(record 스코프)은 행/레코드마다 호출한다 — 건수만큼 나가므로 spec.concurrency 로
        동시 실행을 제한하고, 실패는 on_error 정책(null 채움 / 건별 skip)으로 흡수한다.

        `mapper.llm_fields_scope == "document"`(json_semantic)면 문서 1건당 1회만 호출하고
        결과를 전 섹션에 복사한다 — 섹션(청크) 수만큼 부르면 카드 1장에 10회 넘게 호출되기
        때문이다(json_semantic 모듈 docstring 참고).
        """
        if getattr(mapper, "llm_fields_scope", "record") == "document":
            return await self._apply_llm_fields_document_scope(mapper, fields_list)

        for spec in getattr(mapper, "llm_field_specs", ()):
            if not fields_list:
                break
            enricher = self._llm_field_enricher(spec, mapper)
            if not enricher.is_configured:
                _log.warning(
                    f"[llm_fields] 비활성({spec.label}): url/model 설정이 "
                    f"비어있어 {spec.output_fields} 를 null 로 둡니다."
                )
                for fields in fields_list:
                    for name in spec.output_fields:
                        fields.setdefault(name, None)
                continue

            semaphore = asyncio.Semaphore(spec.concurrency)

            async def _extract(record_fields: dict) -> dict:
                async with semaphore:
                    return await enricher.extract_fields_from_text(
                        spec.build_input_text(record_fields)
                    )

            results = await asyncio.gather(
                *(_extract(fields) for fields in fields_list), return_exceptions=True
            )

            kept: list = []
            failed = 0
            for fields, result in zip(fields_list, results):
                if isinstance(result, BaseException):
                    failed += 1
                    _log.warning(
                        f"[llm_fields] LLM 필드 추출 실패({spec.label}): {result}"
                    )
                    if spec.on_error == "skip_record":
                        continue
                    result = {name: None for name in spec.output_fields}
                fields.update(result)
                kept.append(fields)
            if failed:
                # silent 축소 방지 — 몇 건이 실패했고 어떻게 처리했는지 요약으로 드러낸다.
                _log.warning(
                    f"[llm_fields] 실패 {failed}/{len(fields_list)}건 "
                    f"(on_error={spec.on_error})"
                )
            fields_list = kept
        return fields_list

    async def _apply_llm_fields_document_scope(self, mapper, fields_list: list) -> list:
        """llm_fields_scope == "document" 인 매퍼용 — spec 당 LLM 을 문서 1회만 호출해
        결과를 전 섹션(fields_list 전체)에 그대로 복사한다.

        record 스코프처럼 건별 skip 은 의미가 없다(스킵할 "건"이 없다) — 실패 시 on_error 와
        같은 톤으로 output_fields 를 null 채움해 나머지 필드는 계속 살아남게 한다.
        """
        for spec in getattr(mapper, "llm_field_specs", ()):
            if not fields_list:
                break
            enricher = self._llm_field_enricher(spec, mapper)
            if not enricher.is_configured:
                _log.warning(
                    f"[llm_fields] 비활성({spec.label}): url/model 설정이 "
                    f"비어있어 {spec.output_fields} 를 null 로 둡니다."
                )
                for fields in fields_list:
                    for name in spec.output_fields:
                        fields.setdefault(name, None)
                continue

            document_fields = mapper.document_input_fields(fields_list, spec.input_fields)
            try:
                result = await enricher.extract_fields_from_text(
                    spec.build_input_text(document_fields)
                )
            except Exception as exc:
                _log.warning(f"[llm_fields] 문서 단위 LLM 필드 추출 실패({spec.label}): {exc}")
                result = {name: None for name in spec.output_fields}
            for fields in fields_list:
                fields.update(result)
        return fields_list

    async def _parse_json_records(self, file_path: str, mapper, **kwargs) -> dict:
        """JSON 레코드 배열 → 레코드별 목표필드 element(parse-format).

        docling 을 거치지 않는다 — 필요한 본문은 지정 필드에서 직접 오고, 청커의 행 기반
        경로가 레코드마다 청크를 만들며 metadata 를 청크 property 로 승격한다.
        """
        payload = self._load_json_payload(file_path)
        doc_type = kwargs.get("doc_type")
        try:
            # html_text_fields 파생 필드의 표 모양을 docling 경로와 같은 설정으로 맞춘다
            # (output.table_format: html=<table> / markdown=파이프 표).
            fields_list = mapper.build_fields(
                payload, doc_type,
                table_format=getattr(self, "_table_format", "html"),
                compact_tables=bool(getattr(self, "_compact_tables", True)),
            )
        except ValueError as exc:
            raise GenosServiceException("1", str(exc), stage="custom_fields") from exc

        fields_list = await self._apply_llm_fields(mapper, fields_list)
        _log.info(f"[parser] json_mapping 레코드 {len(fields_list)}건 → element")
        return mapper.to_parse_format(fields_list, doc_type)

    def _parse_json(self, file_path: str, spec, work_dir: str, **kwargs) -> DoclingDocument:
        """JSON 의 지정 key 에서 본문 텍스트(markdown/html)를 꺼내 docling 으로 파싱한다.

        항목별 <h2> 섹션을 가진 단일 HTML 로 병합해 docling 을 1회만 호출한다. 파싱
        본체는 기존 `_parse_docling` 을 그대로 재사용하고, artifacts 경로는 원본 json
        기준으로 유지해 media_files 가 어긋나지 않게 한다.
        """
        from genon.preprocessor.converters.json_text import json_payload_to_html

        payload = self._load_json_payload(file_path)
        stem = Path(file_path).stem
        try:
            merged_html = json_payload_to_html(payload, spec, stem)
        except ValueError as exc:
            raise GenosServiceException("1", str(exc), stage="custom_fields") from exc

        html_path = os.path.join(work_dir, f"{stem}.html")
        with open(html_path, "w", encoding="utf-8") as fp:
            fp.write(merged_html)

        return self._parse_docling(html_path, artifacts_from=file_path, **kwargs)

    def _get_ppt_pdf_converter(self) -> DocumentConverter:
        """PPT(→PDF) 파싱용 경량 docling 컨버터(lazy, 캐시). dotsocr 미수행 + do_ocr=False.
        page_description 이 켜지면 generate_page_images=True 로 페이지 렌더 이미지를 만든다.
        """
        if self._ppt_pdf_converter is not None:
            return self._ppt_pdf_converter
        opts = PdfPipelineOptions()
        opts.do_ocr = False
        opts.do_table_structure = False
        opts.generate_page_images = bool(self._page_desc_options.enabled)
        opts.generate_picture_images = False
        opts.images_scale = self._page_desc_options.images_scale
        self._ppt_pdf_converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
        return self._ppt_pdf_converter

    def _parse_ppt_docling(self, file_path: str, **kwargs) -> "Optional[DoclingDocument]":
        """PPT/PPTX → PDF 변환 후 경량 docling 파싱 + 페이지 단위 image description 주입.

        페이지 설명은 페이지별 TextItem 으로 주입되어 parse 출력(elements)에 그대로 포함된다.
        PDF 변환이 불가하면 None 을 반환해 호출부가 레거시 langchain 경로로 폴백하도록 한다.
        """
        pdf_path = convert_to_pdf(file_path)
        if not pdf_path or not os.path.exists(pdf_path):
            candidate = _get_pdf_path(file_path)
            pdf_path = candidate if os.path.exists(candidate) else None
        if not pdf_path:
            _log.warning(f"[ppt] PDF 변환 실패 — 레거시 경로로 폴백: {os.path.basename(file_path)}")
            return None

        document: DoclingDocument = self._get_ppt_pdf_converter().convert(
            pdf_path, raises_on_error=True
        ).document

        # 페이지별 native text 수집 → 프롬프트({{page_text}})에 반영해 페이지 설명 요청
        page_texts = collect_page_texts(document)
        page_descs = describe_pages(document, self._page_desc_options, page_texts=page_texts)
        for page_no in sorted(page_descs.keys()):
            desc = page_descs[page_no].strip()
            if not desc:
                continue
            text = f"[페이지 이미지 설명]\n{desc}"
            prov = ProvenanceItem(
                page_no=page_no,
                bbox=BoundingBox(l=0, t=0, r=1, b=1),
                charspan=(0, len(text)),
            )
            document.add_text(label=DocItemLabel.TEXT, text=text, prov=prov)
        _log.info(
            f"[ppt] parse page documents: pages={document.num_pages()}, "
            f"described={len(page_descs)}, description_enabled={self._page_desc_options.enabled}"
        )
        return document

    async def _describe_record_tables(self, result: dict, **kwargs) -> dict:
        """레코드 경로(json/tabular 매핑) 산출물의 표에 설명 블록을 넣는다.

        이 경로들은 docling 문서를 만들지 않고 조기 반환하므로 `_apply_docling_post_enrichment`
        의 표 설명 스테이지를 타지 않는다. `table_text_description` 에 자체 LLM 연결이 있으면
        여기서 element 본문의 표를 직접 설명해, custom_fields 의 extractor 종류와 무관하게
        모든 문서유형이 같은 표 설명을 갖게 한다.
        """
        enricher = getattr(self._intel, "table_text_description_enricher", None)
        if enricher is None or not enricher.wants(**kwargs):
            return result
        elements = result.get("elements")
        if not isinstance(elements, list) or not elements:
            return result
        contents = [str(element.get("content") or "") for element in elements]
        try:
            described = await enricher.describe_texts(contents, **kwargs)
        except Exception as exc:
            _handle_stage_error(exc, "table_text_description")
            return result
        for element, content in zip(elements, described):
            element["content"] = content
        return result

    async def _apply_docling_post_enrichment(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        """Facade 후처리 enrichment 훅."""
        # #329: error_policy=strict 이면 _handle_stage_error 가 GenosServiceException 으로
        # 재-raise(삼키지 않음). lenient(기본)은 기존처럼 warning 후 계속.
        try:
            document = self._intel.enrich_doc_summary(document, **kwargs)
        except Exception as exc:
            _handle_stage_error(exc, "doc_summary")
        try:
            document = self._intel.enrich_image_descriptions(document, **kwargs)
        except Exception as exc:
            _handle_stage_error(exc, "image_description")
        # 표 설명(독립 → 융합 → 이미지). 판정은 공용 모듈 한 곳에 있다.
        document = await apply_table_description_stage(
            document,
            custom_fields_enrichers=self._intel.custom_fields_enrichers,
            standalone=getattr(self._intel, "table_text_description_enricher", None),
            run_image_stage=self._intel.enrich_table_descriptions,
            handle_error=_handle_stage_error,
            kwargs=kwargs,
        )
        try:
            document = await self._intel.enrich_metadata(document, **kwargs)
        except Exception as exc:
            _handle_stage_error(exc, "metadata")
        try:
            document = await self._intel.enrich_custom_fields(document, **kwargs)
        except Exception as exc:
            _handle_stage_error(exc, "custom_fields")
        # doc_type 스탬프(예: card): 요청 kwargs 로 doc_type 이 오면 문서 메타에 저장 → compose_vectors 가
        # 모든 청크에 broadcast + result["metadata"] 에도 노출. (faq 는 tabular 경로에서 별도 처리)
        doc_type = normalize_doc_type(kwargs.get("doc_type"))
        if doc_type:
            try:
                from genon.preprocessor.facade.enrichment.field_transforms import (
                    store_metadata_in_document,
                )
                store_metadata_in_document(document, {"doc_type": doc_type})
                ctx = kwargs.get("_enrichment_context")
                if isinstance(ctx, dict):
                    ctx.setdefault("metadata", {})["doc_type"] = doc_type
            except Exception as exc:
                _handle_stage_error(exc, "doc_type_stamp")
        return document

    # ------------------------------------------------------------------
    # 직렬화 헬퍼
    # ------------------------------------------------------------------

    @staticmethod
    def _get_normalized_coords(
        bbox, page_w: float, page_h: float
    ) -> list:
        """BoundingBox → 정규화된 4-코너 좌표 ([top-left, top-right, bottom-right, bottom-left])."""
        if bbox.coord_origin != CoordOrigin.TOPLEFT:
            bbox = bbox.to_top_left_origin(page_h)
        l = round(bbox.l / page_w, 4)
        t = round(bbox.t / page_h, 4)
        r = round(bbox.r / page_w, 4)
        b = round(bbox.b / page_h, 4)
        return [
            {"x": l, "y": t},
            {"x": r, "y": t},
            {"x": r, "y": b},
            {"x": l, "y": b},
        ]

    @staticmethod
    def _item_to_html(item, element_id: int, doc: DoclingDocument) -> str:
        """DocItem → element 수준 HTML 문자열."""
        label_value = item.label.value if hasattr(item.label, "value") else str(item.label)

        if isinstance(item, TableItem):
            return item.export_to_html(doc=doc) or f"<table id='{element_id}'></table>"

        if isinstance(item, PictureItem):
            return f"<figure id='{element_id}'></figure>"

        text = (getattr(item, "text", "") or "").replace("\n", "<br>")

        if label_value == "title":
            return f"<h1 id='{element_id}'>{text}</h1>"

        if label_value == "section_header":
            level = max(1, min(getattr(item, "level", 1), 6))
            return f"<h{level} id='{element_id}'>{text}</h{level}>"

        if label_value == "list_item":
            return f"<p id='{element_id}' data-category='list'>{text}</p>"

        return f"<p id='{element_id}' data-category='{label_value}'>{text}</p>"

    @staticmethod
    def _export_table_content(
        item: TableItem, doc: DoclingDocument, table_format: str = "html",
        compact_tables: bool = True,
    ) -> str:
        """TableItem을 지정한 포맷으로 변환. auto 면 그 표의 구조를 보고 고른다.

        청커도 같은 analyze_grid/resolve_table_format 을 쓰므로, 같은 표가 파서 출력과
        청크에서 다른 형식으로 나가지 않는다.
        """
        table_format = ts.resolve_table_format(
            table_format, ts.analyze_grid(
                getattr(getattr(item, "data", None), "grid", None),
                getattr(getattr(item, "data", None), "num_cols", 0),
                is_html_origin=_doc_is_html_origin(doc),
            ),
        )
        try:
            if table_format == "markdown":
                # compact_tables 는 컬럼 정렬 패딩을 없애 대형 표 markdown 크기를 줄인다.
                text = export_markdown(doc, item=item, compact_tables=compact_tables)
            else:
                text = item.export_to_html(doc=doc)
            if text and text.strip():
                return text
        except Exception:
            pass

        try:
            if item.data and item.data.table_cells:
                parts = []
                for cell in item.data.table_cells:
                    value = getattr(cell, "text", "")
                    if value and str(value).strip():
                        parts.append(str(value).strip())
                if parts:
                    return " ".join(parts)
        except Exception:
            pass

        return getattr(item, "text", "") or ""

    @staticmethod
    def _docling_sheet_prefix(item, doc) -> str:
        """xlsx docling 표의 부모 그룹(name='sheet: X')에서 시트명을 뽑아 '시트명: X\\n' 접두 생성.
        시트 그룹이 없으면 '' 반환(비-xlsx 문서엔 실질 미적용)."""
        try:
            parent = item.parent.resolve(doc) if getattr(item, "parent", None) else None
            name = getattr(parent, "name", None)
        except Exception:
            name = None
        if not name:
            return ""
        if name.startswith("sheet: "):
            name = name[len("sheet: "):]
        name = name.strip()
        return f"시트명: {name}\n" if name else ""

    @staticmethod
    def _docling_to_parse_format(doc: DoclingDocument, table_format: str = "html",
                                 compact_tables: bool = True) -> dict:
        """DoclingDocument → sample_result.json 호환 출력 포맷."""
        elements = []
        element_id = 0
        default_page_no = 1
        try:
            if getattr(doc, "pages", None):
                default_page_no = min(doc.pages.keys())
        except Exception:
            default_page_no = 1

        for item, _ in doc.iterate_items(
            included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}
        ):
            prov_list = getattr(item, "prov", None) or []
            prov = prov_list[0] if len(prov_list) > 0 else None

            page_no = getattr(prov, "page_no", None)
            if not isinstance(page_no, int) or page_no <= 0:
                page_no = default_page_no

            coordinates = []
            if prov is not None:
                try:
                    page_info = doc.pages.get(page_no)
                    if page_info is None or page_info.size is None:
                        raise ValueError("no page size")
                    page_w = page_info.size.width
                    page_h = page_info.size.height
                    coordinates = DocumentProcessor._get_normalized_coords(prov.bbox, page_w, page_h)
                except Exception:
                    coordinates = []

            label_value = item.label.value if hasattr(item.label, "value") else str(item.label)
            # if label_value == "section_header":
            #     level = max(1, min(getattr(item, "level", 1), 6))
            #     category = f"heading{level}"

            # html = DocumentProcessor._item_to_html(item, element_id, doc)
            if isinstance(item, TableItem):
                text = DocumentProcessor._export_table_content(
                    item=item,
                    doc=doc,
                    table_format=table_format,
                    compact_tables=compact_tables,
                )
                sheet_prefix = DocumentProcessor._docling_sheet_prefix(item, doc)
                # refine ON 이면 재구성 HTML 로 표 본체 교체, 요약이 있으면 항상 병기.
                refined_html = TableDescriptionExtractor.extract_refined_html(item)
                table_summary = TableDescriptionExtractor.extract_summary(item)
                if refined_html:
                    # refine 은 항상 HTML 로 재구성 → output table_format 에 맞춰 변환(markdown 등).
                    text = sheet_prefix + refined_html_to_format(refined_html, table_format, compact_tables)
                else:
                    # xlsx docling 표면 시트명 접두 추가(비-xlsx 는 "" 라 영향 없음).
                    text = sheet_prefix + text
                if table_summary:
                    text = text + "\n---\n[표 설명]\n" + table_summary
            else:
                text = getattr(item, "text", "") or ""

            element = {
                "category": label_value,
                # "content": {"html": html, "markdown": "", "text": text},
                "content": text,
                "coordinates": coordinates,
                "id": element_id,
                "page": page_no,
            }
            if isinstance(item, PictureItem):
                image_description = PictureDescriptionExtractor.extract(item)
                if image_description:
                    # 최종 소비계층에서 별도 필드 매핑 없이 바로 활용할 수 있도록
                    # picture 의 content 를 이미지 설명 텍스트로 채운다.
                    element["content"] = image_description

            elements.append(element)
            element_id += 1

        # full_html = "\n".join(e["content"]["html"] for e in elements)

        return {
            # "content": {"html": full_html, "markdown": "", "text": ""},
            "elements": elements,
            # "model": "genonai-parser",
            "usage": {"pages": DocumentProcessor._docling_page_count(doc)},
        }

    @staticmethod
    def _serialize_docling_document(doc: DoclingDocument) -> dict:
        """DoclingDocument를 JSON 직렬화 가능한 dict로 변환."""
        try:
            # pydantic v2 호환 방식 (enum/datetime 등 JSON-safe 변환 포함)
            return doc.model_dump(mode="json")
            # return doc.export_to_dict()
        except Exception:
            try:
                # model_dump가 호환되지 않을 때 문자열 JSON을 다시 dict로 복원
                return json.loads(doc.model_dump_json())
            except Exception:
                # 최후 폴백: docling 기본 export
                return doc.export_to_dict()

    @staticmethod
    def _replace_markdown_tables_with_html(doc: DoclingDocument, markdown_text: str) -> str:
        """Markdown 문자열의 테이블 블록을 순차적으로 HTML 테이블로 치환."""
        if not markdown_text:
            return markdown_text

        out = markdown_text
        for item, _ in doc.iterate_items(
            included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}
        ):
            if not isinstance(item, TableItem):
                continue

            try:
                md_table_raw = export_markdown(doc, item=item)
                html_table = item.export_to_html(doc=doc)
            except Exception:
                continue

            if not md_table_raw or not html_table:
                continue

            md_table = md_table_raw.strip()
            if not md_table:
                continue

            idx = out.find(md_table)
            if idx >= 0:
                out = out[:idx] + html_table + out[idx + len(md_table):]
            else:
                idx_raw = out.find(md_table_raw)
                if idx_raw >= 0:
                    out = out[:idx_raw] + html_table + out[idx_raw + len(md_table_raw):]

        return out

    def _docling_to_content(self, doc: DoclingDocument) -> str:
        """DoclingDocument를 output.format에 따라 content 문자열로 변환."""
        output_format = getattr(self, "_output_format", "json")
        table_format = getattr(self, "_table_format", "html")
        layers = {ContentLayer.BODY, ContentLayer.FURNITURE}

        if output_format == "html":
            return doc.export_to_html(included_content_layers=layers)

        if output_format == "markdown":
            markdown_text = export_markdown(doc, included_content_layers=layers)
            if table_format == "html":
                return self._replace_markdown_tables_with_html(doc, markdown_text)
            return markdown_text

        return ""

    @staticmethod
    def _docling_page_count(doc: DoclingDocument) -> int:
        """DoclingDocument 의 페이지 수. 페이지 개념이 없는 백엔드는 1 로 센다.

        docling HTML 백엔드는 브라우저 렌더링을 켠 경우에만 doc.pages 를 채우므로 평소엔 0 이다.
        raw HTML 블록이 섞인 md 도 md_backend 가 HTML 백엔드로 위임하면서(page 1 스텁이 버려진다)
        같은 상태가 된다. 내용이 있는 문서를 0페이지로 내보내면 소비계층의 페이지 기반 계산이
        전부 무너지므로 1 로 올린다. 진짜 빈 문서는 0 을 유지한다.
        """
        try:
            pages = int(doc.num_pages())
        except Exception:
            pages = 0
        if pages >= 1:
            return pages
        try:
            has_content = next(doc.iterate_items(), None) is not None
        except Exception:
            has_content = False
        return 1 if has_content else 0

    @staticmethod
    def _normalize_response(result: dict) -> dict:
        """응답에 content / elements / usage 키가 항상 존재하도록 보장."""
        result.setdefault("content", "")
        result.setdefault("elements", [])
        result.setdefault("usage", {"pages": 0})
        return result

    @staticmethod
    def _content_response(content: str, pages: int = 0) -> dict:
        """content 전용 출력 포맷."""
        return {
            "elements": [],
            "usage": {"pages": pages},
            "content": content,
        }

    def _build_docling_response(self, doc: DoclingDocument, clear_coordinates: bool = False, **kwargs) -> dict:
        """Docling 경로의 최종 응답 생성.
        민감정보 분류(#315): 요청 guardrail_call 시 문서 전체를 1회 분류해 sensitive_infos 를 응답에
        실어 chunking 으로 넘긴다(청크 단위 quote 매칭·적용은 chunking 담당)."""
        output_format = getattr(self, "_output_format", "json")
        table_format = getattr(self, "_table_format", "html")
        compact_tables = bool(getattr(self, "_compact_tables", True))

        if output_format == "docling":
            # 복원 가능한 DoclingDocument 원본 JSON(model_dump)을 그대로 반환.
            # DoclingDocument.model_validate(data["document"]) 로 무손실 복원 가능 → Chunk API 입력.
            # clear_coordinates / table_format 은 원본 보존을 위해 docling 포맷에서는 무시한다.
            resp = {
                "document": self._serialize_docling_document(doc),
                "usage": {"pages": self._docling_page_count(doc)},
            }
        elif output_format == "json":
            # 표 설명은 아래에서 `[표 설명]` 로 따로 싣는다. docling_core 가 meta 로 이관해 둔
            # 사본까지 본문에 딸려 나가면 같은 문장이 두 번 실리고 내부 구조체가 노출된다.
            strip_enricher_meta(doc)
            result = self._docling_to_parse_format(doc, table_format=table_format,
                                                   compact_tables=compact_tables)
            if clear_coordinates:
                for element in result.get("elements", []):
                    element["coordinates"] = []
            resp = result
        else:
            pages = self._docling_page_count(doc)
            strip_enricher_meta(doc)
            content = self._docling_to_content(doc)
            resp = self._content_response(content, pages=pages)

        # 민감정보 분류(#315): guardrail_call(0/1) 이면 문서 전체 1회 분류 → sensitive_infos 를 응답에 부착.
        # chunking 이 이 값을 받아 청크별 quote 매칭·라벨·마스킹을 수행한다(#315 parser→chunking 구조).
        if gr.call_enabled(kwargs) and self._gr_cfg.configured:
            infos = gr.classify_document(
                gr.doc_text(doc), self._gr_cfg.url, self._gr_cfg.workflow_id,
                self._gr_cfg.api_key, self._gr_cfg.timeout,
            )
            if infos:
                resp["sensitive_infos"] = infos
        return resp

    @staticmethod
    def _audio_to_parse_format(text: str) -> dict:
        """전사 텍스트 → parse format."""
        return {
            "elements": [
                {
                    "category": "paragraph",
                    "content": text,
                    "coordinates": [],
                    "id": 0,
                    "page": 1,
                }
            ],
            "usage": {"pages": 1},
        }

    @staticmethod
    def _tabular_to_parse_format(data_dict: dict) -> dict:
        """tabular data_dict(converters.xlsx_processor 산출) → 행별 parse format."""
        from genon.preprocessor.converters.xlsx_processor import tabular_data_to_parse_format

        return tabular_data_to_parse_format(data_dict)

    @staticmethod
    def _langchain_to_parse_format(docs: list) -> dict:
        """LangChain Document 목록 → parse format."""
        elements = []
        for idx, doc in enumerate(docs):
            page = doc.metadata.get("page", idx)
            if isinstance(page, int):
                page = page + 1  # 0-based → 1-based
            elements.append({
                "category": "paragraph",
                "content": doc.page_content,
                "coordinates": [],
                "id": idx,
                "page": page,
            })
        num_pages = max((e["page"] for e in elements), default=0)
        return {
            "elements": elements,
            "usage": {"pages": num_pages},
        }

    def setup_logging(self, level_num: int):
        rt.setup_logging(level_num)

    # ------------------------------------------------------------------
    # 메인 진입점
    # ------------------------------------------------------------------

    async def __call__(self, request: Request, file_path: str, **kwargs) -> dict:
        runtime_level = kwargs.get('log_level')
        self.setup_logging(runtime_level if runtime_level is not None else self._log_level)

        # 런타임 토글(img_desc/chart_desc/chart_detection/doc_summary)로 이미지·차트 description 재구성
        # (실제 enrichment 는 self._intel 경유이므로 embed 프로세서에 반영한다)
        kwargs = self._intel._normalize_runtime_kwargs(kwargs)
        self._intel._configure_runtime_image_mode(kwargs)

        # #329: LLM 캐시 / error_policy 컨텍스트를 요청 스코프로 설정(/parse 는 body 의
        # workflow_id/run_id 로 스코프 유도). ThreadPool 워커엔 in_current_context 로 전파.
        _cache_token = _set_cache_context(_resolve_cache_context(kwargs))
        # 확장자 별칭이 적용되면 표준 확장자 이름의 사본으로 파싱한다. 그 임시 디렉터리는
        # 요청이 끝날 때 정리한다(finally).
        alias_tmp: tempfile.TemporaryDirectory | None = None
        # 별칭 사본으로 파싱할 때 artifacts(이미지) 경로 기준이 되는 원본 경로.
        artifacts_source: str | None = None
        try:
            raw_ext = os.path.splitext(file_path)[-1].lower()
            # __init__ 을 우회해 만든 인스턴스(단위 테스트)도 견디도록 getattr 로 읽는다.
            ext = _resolve_ext(raw_ext, getattr(self, "_ext_aliases", {}))
            if ext != raw_ext:
                _log.info(
                    f"[DocumentProcessor] file_path={file_path}, ext={raw_ext} -> {ext} (확장자 별칭)"
                )
            else:
                _log.info(f"[DocumentProcessor] file_path={file_path}, ext={ext}")

            # 비정상/암호화 파일 사전 감지(이슈 #278/#307): 지원 포맷 매직헤더에 하나도 안 맞고
            # 텍스트도 아니면(=DRM 암호화/손상 바이너리) 파싱/변환 단계의 garbage 처리를 유발하므로
            # 진입부에서 컷한다. 확장자와 무관하게 실제 헤더로 판정.
            bad_reason = _detect_unsupported_file(file_path)
            if bad_reason:
                _log.warning(f"[parser] 비정상 파일 감지({bad_reason}) — 처리 중단: {file_path}")
                raise GenosServiceException(
                    "1", f"{bad_reason} 입니다. 정상 문서로 다시 업로드하세요: {os.path.basename(file_path)}"
                )

            if ext != raw_ext:
                # docling 은 파일명 확장자로 포맷을 판정하므로 이름을 바꾼 사본을 넘긴다.
                # 원본 경로는 artifacts_source 로 남겨 media_files 경로를 원본 기준으로 유지한다.
                try:
                    alias_tmp = tempfile.TemporaryDirectory(prefix="parser_alias_")
                    artifacts_source = file_path
                    file_path = _materialize_alias_copy(file_path, ext, alias_tmp.name)
                except OSError as exc:
                    raise GenosServiceException(
                        "1", f"확장자 별칭 사본 생성 실패: {exc}"
                    ) from exc

            enrichment_context: dict = {}

            if ext in (".wav", ".mp3", ".m4a"):
                # TODO(#315): PII 마스킹 미적용(보류) — 오디오 전사 텍스트는 별도 논의 후 적용.
                text = self._parse_audio(file_path, **kwargs)
                return self._normalize_response(self._audio_to_parse_format(text))

            if ext in (".csv", ".xlsx", ".xlsm"):
                # doc_type 은 "행을 어떻게 나눌지"가 아니라 "행 컬럼을 어떤 목표필드로 매핑할지"에만
                # 쓴다. 행 분할 여부는 formats.xlsx.processing_mode 가 결정한다.
                # 단, enrichment.custom_fields 의 tabular_mapping 이 doc_type 과 매칭되면 행별 매핑이
                # 목적이므로 processing_mode 와 무관하게 우선한다(intelligent._process_xlsx 와 동일).
                runtime_doc_type = normalize_doc_type(kwargs.get("doc_type"))
                matching_mappers = [
                    mapper for mapper in self._tabular_custom_fields_mappers
                    if mapper.matches(runtime_doc_type)
                ]
                if len(matching_mappers) > 1:
                    raise GenosServiceException(
                        "1",
                        f"동일 doc_type에 tabular custom_fields 설정이 여러 개입니다: {runtime_doc_type}",
                    )
                if matching_mappers:
                    mapper = matching_mappers[0]
                    data_dict = self._parse_tabular(file_path)
                    try:
                        fields_list = mapper.build_fields(data_dict, runtime_doc_type)
                    except (FileNotFoundError, TypeError, ValueError) as exc:
                        raise GenosServiceException("1", str(exc), stage="custom_fields") from exc
                    # 원천에 없는 필드(요약본문·키워드 등)를 행마다 LLM 으로 채운다.
                    # json_mapping 과 같은 build_fields → LLM → to_parse_format 3단 구성.
                    fields_list = await self._apply_llm_fields(mapper, fields_list)
                    _log.info(f"[parser] tabular_mapping 행 {len(fields_list)}건 → element")
                    result = await self._describe_record_tables(
                        mapper.to_parse_format_from_fields(fields_list, runtime_doc_type),
                        **kwargs,
                    )
                    return self._normalize_response(result)
                # docling 모드: MsExcel/Csv 백엔드로 DoclingDocument 생성 후 parse-JSON 직렬화.
                # 다른 문서 포맷과 같은 후처리 훅을 태운다 — 이 경로를 건너뛰면 xlsx 만
                # 문서 단위 custom_fields(extractor: llm)·metadata·doc_type 스탬프를
                # 설정으로 켤 수 없게 된다.
                if self._xlsx_cfg["processing_mode"] == "docling":
                    from genon.preprocessor.converters.xlsx_processor import build_docling_document
                    doc = build_docling_document(file_path)
                    doc = await self._apply_docling_post_enrichment(
                        doc, _enrichment_context=enrichment_context, **kwargs
                    )
                    result = self._build_docling_response(doc, **kwargs)
                    if enrichment_context.get("metadata"):
                        result["metadata"] = enrichment_context["metadata"]
                    return self._normalize_response(result)
                # tabular 모드(기본): openpyxl 병합셀 처리 → 데이터 행마다 element 하나.
                # docling 문서를 만들지 않으므로 표 설명만 레코드 경로와 같은 훅으로 넣는다
                # (custom_fields 매핑 경로와 동일하게 맞춘다).
                # TODO(#315): PII 마스킹 미적용(보류) — tabular 산출은 별도 논의 후 적용.
                result = await self._describe_record_tables(
                    self._tabular_to_parse_format(self._parse_tabular(file_path)),
                    **kwargs,
                )
                return self._normalize_response(result)

            # .hml(HWPML)은 hwp_sdk 260713+ 에서 지원 — 같은 SDK 경로로 라우팅 (이슈 #323)
            if ext in (".hwp", ".hwpx", ".hml"):
                doc = self._parse_hwp_hwpx(file_path, **kwargs)
                doc = await self._apply_docling_post_enrichment(doc, _enrichment_context=enrichment_context, **kwargs)
                result = self._build_docling_response(doc, **kwargs)
                if enrichment_context.get("metadata"):
                    result["metadata"] = enrichment_context["metadata"]
                return self._normalize_response(result)

            if ext == ".docx":
                doc = self._parse_docx(file_path, **kwargs)
                doc = await self._apply_docling_post_enrichment(doc, _enrichment_context=enrichment_context, **kwargs)
                result = self._build_docling_response(doc, clear_coordinates=True, **kwargs)
                if enrichment_context.get("metadata"):
                    result["metadata"] = enrichment_context["metadata"]
                return self._normalize_response(result)

            # .md 는 formats.md.processing_mode=docling(기본)일 때만 이 분기로 온다.
            # text 모드면 아래 캐치올(TextLoader)로 빠져 레거시 동작을 그대로 유지한다.
            if ext in (".pdf", ".html", ".htm") or (
                ext == ".md" and self._md_cfg["processing_mode"] == "docling"
            ):
                if ext in (".html", ".htm"):
                    # html 은 flatten 전처리를 거칠 수 있다(srcdoc 등). 파생 임시 파일은
                    # 파싱 후 정리하고, artifacts 경로는 원본 기준으로 유지한다.
                    with tempfile.TemporaryDirectory(prefix="parser_html_") as work_dir:
                        parse_path = self._prepare_html(
                            file_path, work_dir,
                            marker_headings=self._html_marker_headings_enabled(kwargs.get("doc_type")),
                        )
                        doc = self._parse_docling(
                            parse_path,
                            artifacts_from=artifacts_source or (
                                file_path if parse_path != file_path else None
                            ),
                            _enrichment_context=enrichment_context,
                            **kwargs,
                        )
                    self._warn_if_thin_html(file_path, doc)
                elif ext == ".md":
                    fm_spec = self._markdown_front_matter_spec_for(kwargs.get("doc_type"))
                    fence_spec = self._markdown_text_fence_spec_for(kwargs.get("doc_type"))
                    marker_on = self._markdown_marker_headings_enabled(kwargs.get("doc_type"))
                    if fm_spec is None and fence_spec is None and not marker_on:
                        doc = self._parse_docling(
                            file_path,
                            artifacts_from=artifacts_source,
                            _enrichment_context=enrichment_context,
                            **kwargs,
                        )
                    else:
                        # front matter를 제외하거나 ```text 펜스를 단락으로 되돌린 파생
                        # Markdown은 임시 파일로만 사용한다. 선택 metadata와 제외된 원문은
                        # custom-fields 후처리에 별도 전달한다.
                        with tempfile.TemporaryDirectory(prefix="parser_md_") as work_dir:
                            parse_path, front_matter_context = self._prepare_markdown(
                                file_path, work_dir, fm_spec, fence_spec, marker_on
                            )
                            markdown_kwargs = dict(kwargs)
                            markdown_kwargs["_markdown_front_matter"] = front_matter_context
                            doc = self._parse_docling(
                                parse_path,
                                artifacts_from=artifacts_source or (
                                    file_path if parse_path != file_path else None
                                ),
                                _enrichment_context=enrichment_context,
                                **markdown_kwargs,
                            )
                        kwargs = dict(kwargs)
                        kwargs["_markdown_front_matter"] = front_matter_context
                else:
                    doc = self._parse_docling(
                        file_path,
                        artifacts_from=artifacts_source,
                        _enrichment_context=enrichment_context,
                        **kwargs,
                    )
                doc = await self._apply_docling_post_enrichment(doc, _enrichment_context=enrichment_context, **kwargs)
                result = self._build_docling_response(doc, **kwargs)
                if enrichment_context.get("metadata"):
                    result["metadata"] = enrichment_context["metadata"]
                return self._normalize_response(result)

            # JSON: enrichment.custom_fields 설정으로 두 모드가 갈린다.
            #   레코드 모드(extractor: json_mapping) — 레코드별 목표필드 element
            #   문서 모드(json: text_fields)        — 본문 텍스트를 합쳐 docling 파싱
            # 매칭 설정이 없으면 기존 캐치올 경로로 폴백해 기존 .json 동작을 보존한다
            # (xlsx 분기와 같은 게이팅 패턴).
            if ext == ".json":
                # 1순위: 레코드 매핑(json_mapping) — 레코드마다 청크/메타데이터를 따로 만든다.
                #        docling 을 거치지 않으므로 xlsx 의 tabular 조기 분기와 같은 성격이다.
                records_mapper = self._json_records_mapper_for(kwargs.get("doc_type"))
                if records_mapper is not None:
                    result = await self._parse_json_records(
                        file_path, records_mapper, **kwargs
                    )
                    result = await self._describe_record_tables(result, **kwargs)
                    return self._normalize_response(result)

                # 2순위: 문서 모드(json: text_fields) — 본문 텍스트를 합쳐 docling 으로 파싱.
                json_spec = self._json_text_spec_for(kwargs.get("doc_type"))
                if json_spec is not None:
                    with tempfile.TemporaryDirectory(prefix="parser_json_") as work_dir:
                        doc = self._parse_json(
                            file_path, json_spec, work_dir,
                            _enrichment_context=enrichment_context, **kwargs,
                        )
                    doc = await self._apply_docling_post_enrichment(
                        doc, _enrichment_context=enrichment_context, **kwargs
                    )
                    result = self._build_docling_response(doc, **kwargs)
                    if enrichment_context.get("metadata"):
                        result["metadata"] = enrichment_context["metadata"]
                    return self._normalize_response(result)
                _log.info(
                    "[parser] custom_fields json 매칭 설정 없음 — 기존 텍스트 경로로 처리: "
                    f"{os.path.basename(file_path)}"
                )

            # PPT: PDF 변환 → 경량 docling 파싱 + 페이지 단위 image description(옵션).
            # 변환 실패 시에만 레거시 langchain 경로로 폴백한다. (파스 전용 — 청킹 없음)
            if ext in (".ppt", ".pptx"):
                doc = self._parse_ppt_docling(file_path, **kwargs)
                if doc is not None:
                    doc = await self._apply_docling_post_enrichment(
                        doc, _enrichment_context=enrichment_context, **kwargs
                    )
                    result = self._build_docling_response(doc, **kwargs)
                    if enrichment_context.get("metadata"):
                        result["metadata"] = enrichment_context["metadata"]
                    return self._normalize_response(result)
                # PDF 변환 실패 폴백
                # TODO(#315): PII 마스킹 미적용(보류) — langchain 폴백 경로. docling 아닌 파서 산출은 별도 논의.
                docs = self._parse_other(file_path, **kwargs)
                return self._normalize_response(self._langchain_to_parse_format(docs))

            # 기타 포맷: doc, txt, json, md, jpg, jpeg, png 등
            # TODO(#315): PII 마스킹 미적용(보류) — langchain 경로(doc/txt/md/이미지 등)는 별도 논의 후 적용.
            docs = self._parse_other(file_path, **kwargs)
            return self._normalize_response(self._langchain_to_parse_format(docs))
        finally:
            if alias_tmp is not None:
                alias_tmp.cleanup()
            _log_cache_summary()
            _reset_cache_context(_cache_token)
