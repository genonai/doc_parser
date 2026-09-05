# 파싱용 전처리기 v.2.2.0 (2026-06-02 Release)
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
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

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    PdfPipelineOptions,
    TableFormerMode,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
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
    ProvenanceItem,
)

from genon.preprocessor.facade.enrichment.custom_fields_enricher import normalize_doc_type
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    build_tabular_custom_fields_mappers,
    claimed_row_pages,
    merge_parse_formats,
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

from genon.preprocessor.facade import guardrail as gr
from genon.preprocessor.facade.enrichment.page_description import (
    PageDescriptionOptions,
    collect_page_texts,
    describe_pages,
)
from genon.preprocessor.facade.enrichment.table_text_description import (
    apply_table_description_stage,
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
from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.common import loaders as ld
from genon.preprocessor.facade.common import docling_ops as dops
from genon.preprocessor.facade.common import parser_config as pcfg
from genon.preprocessor.facade.serialize import parse_format as pf
from genon.preprocessor.facade.common import runtime as rt
from genon.preprocessor.facade.common import file_probe as fp
from genon.preprocessor.facade.common import pdf_convert as pc
from genon.preprocessor.facade.common import format_alias as fa
from genon.preprocessor.facade.common.docling_runtime import DoclingRuntimeBase
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


def _config_error(message: str) -> "GenosServiceException":
    """기동 시 설정 오류용 예외 팩토리. 공용 모듈이 이 facade 의 예외 클래스를 쓰게 하는 통로다."""
    return GenosServiceException("1", message, stage="custom_fields")


def _routing_error(message: str) -> "GenosServiceException":
    """요청 처리 중 doc_type 매칭 오류. 기동 시 설정 오류가 아니므로 stage 를 붙이지 않는다."""
    return GenosServiceException("1", message)


def _guard(label: str, fn, *args, **kwargs):
    """설정 빌더 호출을 감싸 어느 설정이 문제인지 드러낸다(구현은 common/parser_config.py)."""
    return pcfg.guard_config(label, _config_error, fn, *args, **kwargs)


def _build_json_records_mappers(custom_fields_cfgs: list) -> list:
    """json_mapping/json_records/json_semantic 설정을 모두 매퍼로 만들어 합친다.

    두 빌더에 전체 목록을 그대로 넘긴다 — 각 빌더가 자기 extractor 집합에 속하는 설정만
    스스로 고른다(custom_fields_enricher.py 의 집합 분리 참고).
    """
    mappers: list = list(build_json_records_mappers(custom_fields_cfgs))
    mappers.extend(build_semantic_json_mappers(custom_fields_cfgs))
    return mappers


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


class TextLoader(ld.TextLoaderBase):
    pass



# ============================================================
# AudioLoader (from attachment_processor.py)
# ============================================================

class AudioLoader(ld.AudioLoaderBase):
    pass


# ============================================================
# IntelligentDocumentProcessor — parser 가 합성으로 쓰는 docling 런타임
# 배관은 facade/common/docling_runtime.py 한 벌이다. 여기 남는 것은 enrichment()
# 뿐이다 — 세 facade 의 본문이 로그 태그·PPT 처리·예외 스탬프에서 갈린다.
# 이름을 남기는 이유: tests/unit 두 파일이 모듈 최상위에서 이 이름을 import 한다.
# ============================================================

class IntelligentDocumentProcessor(DoclingRuntimeBase):

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


# ============================================================
# 포맷 전용 로더 — 컨버터 조립은 facade/common/docling_ops.py 에 한 벌 있다.
# 여기 남는 것은 이름뿐이다. 확장자를 어느 로더로 보낼지(라우팅)는 아래
# GenericDocumentLoader.get_loader 와 DocumentProcessor 의 _parse_* 가 정한다.
# ============================================================

class HwpDocumentLoader(dops.HwpLoaderBase):
    pass


class DocxDocumentLoader(dops.DocxLoaderBase):
    pass


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

        # xlsx/csv · md · 확장자 별칭 설정은 intel 프로세서가 동일 config에서 이미 파싱함 → 재사용.
        # _ext_aliases 를 빠뜨리면 formats.extension_aliases 가 설정에 있어도 라우팅이 그것을
        # 못 읽어(빈 dict) *.parsed 같은 입력이 구조 없는 텍스트로 떨어진다.
        self._xlsx_cfg = self._intel._xlsx_cfg
        self._md_cfg = self._intel._md_cfg
        self._ext_aliases = self._intel._ext_aliases
        self._config_dir = self._intel._config_dir
        # extractor=tabular_mapping = xlsx 행별 매핑. 파싱 조기 분기(_parse_tabular_records)가 쓴다.
        self._tabular_custom_fields_mappers = _guard(
            "tabular_mapping", build_tabular_custom_fields_mappers,
            self._intel.custom_fields_cfgs)

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

        # custom_fields 설정에서 파싱 라우팅이 참조할 spec/mapper 를 시작 시 1회 만든다.
        # _guard 로 감싸는 이유: 설정 오류가 raw 예외로 __init__ 을 뚫고 나가면 서비스
        # import 자체가 죽고 어느 yaml 이 문제인지도 드러나지 않는다.
        cf_cfgs = self._intel.custom_fields_cfgs
        # `json:` 블록 = .json 입력에서 본문 텍스트를 꺼낼 key 목록.
        self._json_text_specs = _guard("json", pcfg.build_json_text_specs, cf_cfgs)
        # markdown.front_matter = 원천 YAML 머리말의 metadata 승격 필드와 청크 텍스트 제외 필드.
        self._markdown_front_matter_specs = _guard(
            "markdown.front_matter", build_markdown_front_matter_specs, cf_cfgs)
        # markdown.text_fence = PDF 레이아웃 보존용 ```text 펜스를 논리 단락으로 되돌린다
        # (안 하면 펜스 본문 전체가 CodeItem 하나가 되어 chunk_size 가 무의미해진다).
        self._markdown_text_fence_specs = _guard(
            "markdown.text_fence", build_markdown_text_fence_specs, cf_cfgs)
        # html.marker_headings = h태그 없이 도형 마커로만 계층을 표현하는 원천에서 그 마커
        # 줄을 섹션 헤더로 승격한다. 대상 doc_type 이 아니면 사유 계산 자체를 켜지 않는다.
        self._html_marker_heading_doc_types = _guard(
            "html.marker_headings", build_html_marker_heading_doc_types, cf_cfgs)
        # 같은 원문이 md 로도 온다. 판정 규칙은 converters 쪽에서 공유하므로 스위치만 나란히 둔다.
        self._markdown_marker_heading_doc_types = _guard(
            "markdown.marker_headings", build_markdown_marker_heading_doc_types, cf_cfgs)
        # extractor=json_mapping / json_semantic = JSON 레코드 → 목표필드 매핑.
        # 문서 모드(json:)보다 우선하며, 레코드마다 청크 메타데이터를 따로 싣는다.
        # 빌더 둘에 전체 목록을 그대로 넘긴다 — 각자 자기 extractor 집합만 골라 간다.
        self._json_records_mappers = _guard(
            "json_mapping/json_semantic", _build_json_records_mappers, cf_cfgs)
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

    def _json_records_mappers_for(self, runtime_doc_type: Any) -> list:
        return pcfg.json_records_mappers_for(
            self._json_records_mappers, runtime_doc_type, _routing_error)

    def _json_text_spec_for(self, runtime_doc_type: Any):
        """런타임 doc_type 에 매칭되는 json 설정. 없으면 None(기존 경로 폴백)."""
        return self._single_json_match(self._json_text_specs, runtime_doc_type, "json")

    def _markdown_front_matter_spec_for(self, runtime_doc_type: Any):
        return self._single_json_match(
            [s for s in self._markdown_front_matter_specs if s.matches(runtime_doc_type)],
            runtime_doc_type, "markdown.front_matter", filtered=True)

    def _markdown_text_fence_spec_for(self, runtime_doc_type: Any):
        """런타임 doc_type 에 매칭되는 text_fence 설정. 없으면 None(전처리 없이 파싱)."""
        return self._single_json_match(
            self._markdown_text_fence_specs, runtime_doc_type, "markdown.text_fence")

    def _html_marker_headings_enabled(self, runtime_doc_type: Any) -> bool:
        """런타임 doc_type 이 마커 승격 대상인지."""
        return normalize_doc_type(runtime_doc_type) in self._html_marker_heading_doc_types

    def _markdown_marker_headings_enabled(self, runtime_doc_type: Any) -> bool:
        """런타임 doc_type 이 md 마커 승격 대상인지."""
        return normalize_doc_type(runtime_doc_type) in self._markdown_marker_heading_doc_types

    @staticmethod
    def _single_json_match(specs, runtime_doc_type: Any, label: str, filtered: bool = False):
        """doc_type 으로 고르고, 매칭이 1개 이하인지 확인한다(중복 설정은 즉시 실패)."""
        matching = specs if filtered else pcfg.specs_for_doc_type(specs, runtime_doc_type)
        return pcfg.single_match(matching, runtime_doc_type, label, _routing_error)

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
        # 확장자가 둘 이상인 입력(X.html.parsed)은 마지막 확장자만 떼면 형제 파일(X.html)과
        # 이름이 겹친다. _with_pictures_refs 는 그림 유무와 무관하게 이 경로를 mkdir 하므로
        # 형제가 파일로 있으면 FileExistsError 로 파싱이 죽고, 반대로 먼저 만들면 나중에
        # 그 형제 파일을 같은 폴더에 내려받을 수 없다. 겹칠 수 없는 이름을 쓴다.
        if os.path.splitext(filename)[1]:
            filename = output_file + ".artifacts"
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

    async def _parse_json_records(self, file_path: str, mappers: list, **kwargs) -> dict:
        """JSON 레코드 배열 → 레코드별 목표필드 element(parse-format).

        docling 을 거치지 않는다 — 필요한 본문은 지정 필드에서 직접 오고, 청커의 행 기반
        경로가 레코드마다 청크를 만들며 metadata 를 청크 property 로 승격한다.
        """
        payload = self._load_json_payload(file_path)
        doc_type = kwargs.get("doc_type")
        results = []
        for mapper in mappers:
            try:
                # custom_fields 의 html_text/text 변환 표 모양을 docling 경로와 같은 설정으로 맞춘다
                # (output.table_format: html=<table> / markdown=파이프 표).
                fields_list = mapper.build_fields(
                    payload, doc_type,
                    table_format=getattr(self, "_table_format", "html"),
                    compact_tables=bool(getattr(self, "_compact_tables", True)),
                )
            except ValueError as exc:
                raise GenosServiceException("1", str(exc), stage="custom_fields") from exc

            fields_list = await self._apply_llm_fields(mapper, fields_list)
            _log.info(
                f"[parser] json 레코드 {len(fields_list)}건 → element "
                f"(records={getattr(mapper, 'records_key', None)})"
            )
            # json 매퍼의 to_parse_format 은 이미 만들어진 fields 목록을 받는다
            # (tabular 는 같은 이름이 원시 data_dict 를 받아 이름이 어긋나 있다).
            results.append(mapper.to_parse_format(fields_list, doc_type))
        return merge_parse_formats(results)

    async def _parse_tabular_records(
        self, file_path: str, mappers: list, runtime_doc_type: Any
    ) -> dict:
        """엑셀/CSV 를 매퍼 목록으로 매핑해 합친다(매퍼가 하나면 종전과 동일).

        매퍼가 여럿이면 각자 맡을 수 있는 표만 처리한다 — 스키마가 다른 표가 한 파일에
        섞여 오는 원천 대응이다. 어느 매퍼도 안 맡은 표가 있으면 경고로 드러낸다.
        조용히 사라지면 "몇 건이 왜 없지"를 나중에 데이터에서 발견하게 된다.
        """
        data_dict = self._parse_tabular(file_path)
        multi = len(mappers) > 1
        results: list = []
        # 표 단위로 맡았는지 본다. 건수 총합만 세면 매퍼 A 가 시트1을 맡은 순간 시트2가
        # 아무에게도 안 맡겨져도 "맡았다"가 되어, 표 하나가 통째로 조용히 사라진다.
        claimed_pages: set = set()
        for mapper in mappers:
            try:
                fields_list = mapper.build_fields(
                    data_dict, runtime_doc_type, skip_unmapped=multi,
                    table_format=getattr(self, "_table_format", "html"),
                    compact_tables=bool(getattr(self, "_compact_tables", True)),
                )
            except (FileNotFoundError, TypeError, ValueError) as exc:
                raise GenosServiceException("1", str(exc), stage="custom_fields") from exc
            claimed_pages.update(claimed_row_pages(fields_list))
            fields_list = await self._apply_llm_fields(mapper, fields_list)
            _log.info(f"[parser] tabular_mapping 행 {len(fields_list)}건 → element")
            results.append(mapper.to_parse_format_from_fields(fields_list, runtime_doc_type))

        if multi:
            sheets = data_dict.get("data", []) or []
            unclaimed = [
                str((sheets[page - 1] or {}).get("sheet_name") or f"sheet_{page}")
                for page in range(1, len(sheets) + 1)
                if page not in claimed_pages
            ]
            if unclaimed:
                _log.warning(
                    f"[parser] tabular_mapping 매퍼 {len(mappers)}개 중 어느 것도 맡지 않은 "
                    f"표가 있습니다(doc_type={runtime_doc_type}): {unclaimed} — 그 표는 청크가 "
                    f"되지 않습니다. 각 설정의 require.fields 와 alias 를 확인하세요."
                )
        return merge_parse_formats(results)

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
    # 직렬화 — 구현은 facade/serialize/parse_format.py 에 한 벌 있다.
    # 여기 남는 래퍼는 단위 테스트가 클래스 경유로 부르고(patch.object 로 갈아끼운다)
    # 응답 조립(_build_docling_response)이 self 를 통해 부르기 때문이다.
    # ------------------------------------------------------------------

    _get_normalized_coords = staticmethod(pf.get_normalized_coords)
    _export_table_content = staticmethod(pf.export_table_content)
    _docling_sheet_prefix = staticmethod(pf.docling_sheet_prefix)
    _docling_to_parse_format = staticmethod(pf.docling_to_parse_format)
    _serialize_docling_document = staticmethod(pf.serialize_docling_document)
    _replace_markdown_tables_with_html = staticmethod(pf.replace_markdown_tables_with_html)
    _docling_page_count = staticmethod(pf.docling_page_count)
    _normalize_response = staticmethod(pf.normalize_response)
    _content_response = staticmethod(pf.content_response)
    _audio_to_parse_format = staticmethod(pf.audio_to_parse_format)
    _tabular_to_parse_format = staticmethod(pf.tabular_to_parse_format)
    _langchain_to_parse_format = staticmethod(pf.langchain_to_parse_format)

    def _docling_to_content(self, doc: DoclingDocument) -> str:
        """DoclingDocument를 output.format에 따라 content 문자열로 변환."""
        return pf.docling_to_content(
            doc,
            getattr(self, "_output_format", "json"),
            getattr(self, "_table_format", "html"),
        )

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
                if matching_mappers:
                    result = await self._parse_tabular_records(
                        file_path, matching_mappers, runtime_doc_type
                    )
                    result = await self._describe_record_tables(result, **kwargs)
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
                records_mappers = self._json_records_mappers_for(kwargs.get("doc_type"))
                if records_mappers:
                    result = await self._parse_json_records(
                        file_path, records_mappers, **kwargs
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
