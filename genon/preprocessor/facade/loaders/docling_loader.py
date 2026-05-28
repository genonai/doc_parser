import os
import re
import logging
import requests
from pathlib import Path

import yaml
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    HwpxFormatOption,
    WordFormatOption,
    MarkdownFormatOption,
    HTMLFormatOption,
)
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    EasyOcrOptions,
    PaddleOcrOptions,
    TesseractOcrOptions,
    TesseractCliOcrOptions,
    RapidOcrOptions,
    UpstageOcrOptions,
    PictureDescriptionApiOptions,
    PictureDescriptionVlmOptions,
    LayoutModelType,
)
from docling.datamodel.settings import settings as _docling_settings
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.backend.pymupdf_backend import PyMuPDFDocumentBackend
from docling.backend.genos_msword_backend import GenosMsWordDocumentBackend
from docling.backend.html_backend import HTMLDocumentBackend
from docling.backend.md_backend import MarkdownDocumentBackend
from docling.backend.hwp_backend import HwpDocumentBackend
from docling.backend.xml.hwpx_backend import HwpxDocumentBackend
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.pipeline.simple_pipeline import SimplePipeline
from docling_core.types import DoclingDocument
from docling_core.types.doc import TextItem

from .base_loader import BaseLoader
from .tabular_loader import TabularLoader
from .audio_loader import AudioLoader
from .langchain_loaders import LangChainPdfLoader, LangChainDocxLoader, LangChainPptxLoader, LangChainMdLoader

_log = logging.getLogger(__name__)

_OCR_YAML_DIR = Path(__file__).parent.parent / "configs" / "ocr"

OCR_ENGINE_MAP = {
    "easy":       EasyOcrOptions,
    "paddle":     PaddleOcrOptions,
    "tesseract":  TesseractOcrOptions,
    "tesseractcli": TesseractCliOcrOptions,
    "rapid":      RapidOcrOptions,
    "upstage":    UpstageOcrOptions,
}


def _is_non_meaningful_char(c: str) -> bool:
    if c.isspace():
        return False
    if '가' <= c <= '힣' or 'ㄱ' <= c <= 'ㅎ' or 'ㅏ' <= c <= 'ㅣ':
        return False
    if '一' <= c <= '鿿':
        return False
    if '぀' <= c <= 'ヿ':
        return False
    if c.isascii():
        return False
    return True


def _has_good_text(
    document: DoclingDocument,
    glyph_threshold: int = 10,
    non_meaningful_ratio_threshold: float = 0.3,
) -> bool:
    """
    문서 텍스트 품질 검사. True = OCR 불필요, False = OCR 권장.

    - 텍스트가 없으면 False
    - GLYPH 패턴이 glyph_threshold 초과하면 False
    - non-meaningful 문자 비율이 non_meaningful_ratio_threshold 초과하면 False
    """
    texts = []
    glyph_count = 0
    for item, _ in document.iterate_items():
        if isinstance(item, TextItem) and item.text:
            texts.append(item.text)
            glyph_count += len(re.findall(r'GLYPH\w*', item.text))

    text = " ".join(texts)
    if not text.strip():
        return False
    if glyph_count > glyph_threshold:
        return False

    text_len = len(text)
    non_meaningful = sum(1 for c in text if _is_non_meaningful_char(c))
    return (non_meaningful / text_len) < non_meaningful_ratio_threshold


_OCR_CHECK_SYSTEM_PROMPT = "당신은 PDF에서 추출된 텍스트의 품질을 분석하여 OCR 수행이 필요한지를 판단하는 전문가입니다."
_OCR_CHECK_USER_PROMPT = (
    "다음과 같은 기준을 참고하여 판단하세요:\n\n"
    "- 텍스트가 거의 없는 경우 (문자 수가 너무 적거나 10자 이하)\n"
    "- 대부분의 문자가 의미 없는 기호 또는 외계어처럼 보이는 경우 (예: \"Ê¾²¯Í­Å\" 등)\n"
    "- 전체의 70% 이상이 비ASCII 문자이거나 공백 비율이 과도하게 높을 경우\n"
    "- 텍스트가 존재하지만 문맥이 완전히 무의미하거나 손상된 경우\n"
    "- `GLYPH<c=4,font=/DDPDDH+HaansoftBatang>` 와 같은 인코딩 오류가 전체의 50% 이상인 경우\n\n"
    "판단은 다음 두 단계로 진행하세요:\n"
    "1. 추출된 텍스트의 품질을 분석합니다.\n"
    "2. OCR이 필요한지 여부를 `YES` 또는 `NO`로 명확하게 판단합니다.\n\n"
    "출력 형식:\n"
    "<analysis>\n... 분석 내용 ...\n</analysis>\n"
    "<decision>\nYES 또는 NO\n</decision>\n\n"
    "[INPUT]\n"
    "<text>\n{content}\n</text>\n"
    "<metadata>\n문자 수: {text_len}, 비ASCII 비율: {non_ascii_ratio:.2f}, 공백 비율: {space_ratio:.2f}\n</metadata>"
)


def _extract_text_sample(text: str, sample_len: int = 1000) -> str:
    """20%/50%/80% 위치에서 샘플링. 짧으면 그대로 반환."""
    n = len(text)
    if n <= sample_len * 3:
        return text[:3000]
    half = sample_len // 2
    parts = [text[max(0, int(n * r) - half): int(n * r) + half] for r in (0.2, 0.5, 0.8)]
    return " ".join(parts)


def _check_text_quality_llm(document: DoclingDocument, endpoint: str, timeout: int = 60) -> bool:
    """
    LLM을 호출해 텍스트 품질 판단. True = OCR 불필요, False = OCR 권장.
    호출 실패 시 _has_good_text 휴리스틱으로 폴백.
    """
    texts = []
    for item, _ in document.iterate_items():
        if isinstance(item, TextItem) and item.text:
            texts.append(item.text)

    text = " ".join(texts)
    if not text.strip():
        return False

    sample = _extract_text_sample(text)
    text_len = len(sample)
    non_ascii_ratio = sum(1 for c in sample if _is_non_meaningful_char(c)) / text_len if text_len else 0
    space_ratio = sample.count(" ") / text_len if text_len else 1.0

    user_msg = _OCR_CHECK_USER_PROMPT.format(
        content=sample,
        text_len=text_len,
        non_ascii_ratio=non_ascii_ratio,
        space_ratio=space_ratio,
    )

    try:
        payload = {
            "model": "model",
            "temperature": 0.0,
            "max_tokens": 4000,
            "messages": [
                {"role": "system", "content": _OCR_CHECK_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        }
        resp = requests.post(endpoint, json=payload, timeout=timeout)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        match = re.search(r"<decision>\s*(YES|NO)\s*</decision>", content, re.IGNORECASE)
        if match:
            return match.group(1).upper() == "NO"  # NO = OCR 불필요
        _log.warning("[DoclingLoader] LLM 응답에서 <decision> 파싱 실패, 휴리스틱으로 폴백")
    except Exception as e:
        _log.warning(f"[DoclingLoader] LLM 품질 체크 실패 ({e}), 휴리스틱으로 폴백")

    return _has_good_text(document)


_OCR_META_KEYS = {"ocr_mode", "auto_ocr_check_endpoint", "engine"}

# lang 기본값: config에 없을 때 주입
_ENGINE_LANG_DEFAULTS = {
    "paddle":       ["korean"],
    "easy":         ["ko", "en"],
    "tesseract":    ["kor", "eng"],
    "tesseractcli": ["kor", "eng"],
}


def _load_ocr_options(ocr_cfg: dict | str):
    """
    ocr 설정을 OcrOptions 인스턴스로 변환.

    현재 구조 (engine 명시 + paddle은 flat, upstage는 sub-dict):
        {"engine": "paddle", "ocr_endpoint": "...", "upstage": {...}}
        {"engine": "upstage", "upstage": {"api_key": "..."}}

    레거시 구조 (엔진 이름 단일 키):
        {"paddle": {"ocr_endpoint": "..."}}

    문자열 (기본값):
        "easy"
    """
    if isinstance(ocr_cfg, str):
        engine_key = ocr_cfg.lower()
        options_dict = {}
    else:
        explicit_engine = ocr_cfg.get("engine", "").lower().strip()
        if explicit_engine:
            engine_key = explicit_engine
            if engine_key == "upstage":
                raw = ocr_cfg.get("upstage") or {}
                options_dict = {k: v for k, v in raw.items() if v is not None}
            else:
                # paddle 등: meta키·dict값 제외한 flat 키가 옵션
                options_dict = {
                    k: v for k, v in ocr_cfg.items()
                    if k not in _OCR_META_KEYS and not isinstance(v, dict) and v is not None
                }
        else:
            # 레거시: 단일 엔진 키
            engine_entries = {k: v for k, v in ocr_cfg.items() if k not in _OCR_META_KEYS}
            assert len(engine_entries) == 1, "ocr.engine 미지정 시 엔진 키 하나만 허용"
            engine_key, raw_options = next(iter(engine_entries.items()))
            engine_key = engine_key.lower()
            options_dict = {k: v for k, v in (raw_options or {}).items() if v is not None}

    if "lang" not in options_dict and engine_key in _ENGINE_LANG_DEFAULTS:
        options_dict["lang"] = _ENGINE_LANG_DEFAULTS[engine_key]

    ocr_cls = OCR_ENGINE_MAP.get(engine_key)
    assert ocr_cls is not None, (
        f"지원하지 않는 OCR 엔진: {engine_key}. 가능한 값: {list(OCR_ENGINE_MAP.keys())}"
    )
    return ocr_cls(**options_dict)


FORMAT_MAP = {
    "pdf": InputFormat.PDF,
    "hwpx": InputFormat.XML_HWPX,
    "docx": InputFormat.DOCX,
    "md": InputFormat.MD,
    "html": InputFormat.HTML,
    "pptx": InputFormat.PPTX,
    "xlsx": InputFormat.XLSX,
    "csv": InputFormat.CSV,
    "image": InputFormat.IMAGE,
}

FORMAT_OPTION_MAP = {
    InputFormat.PDF: PdfFormatOption,
    InputFormat.XML_HWPX: HwpxFormatOption,
    InputFormat.DOCX: WordFormatOption,
    InputFormat.MD: MarkdownFormatOption,
    InputFormat.HTML: HTMLFormatOption,
}

PIPELINE_MAP = {
    "pdf": StandardPdfPipeline,
    "simple": SimplePipeline,
}

BACKEND_MAP = {
    "pypdf": PyPdfiumDocumentBackend,
    "pymu": PyMuPDFDocumentBackend,
    "msword": GenosMsWordDocumentBackend,
    "html": HTMLDocumentBackend,
    "md": MarkdownDocumentBackend,
    "hwp": HwpDocumentBackend,
    "hwpx": HwpxDocumentBackend,
}

# backend 값이 아래 키 중 하나이면 Docling 파이프라인 우회, 전용 로더로 처리
BYPASS_BACKEND_MAP: dict[str, type[BaseLoader]] = {
    "langchain_pdf": LangChainPdfLoader,
    "langchain_docx": LangChainDocxLoader,
    "langchain_pptx": LangChainPptxLoader,
    "langchain_md": LangChainMdLoader,
    "tabular": TabularLoader,
    "audio": AudioLoader,
}


class DoclingLoader(BaseLoader):
    """
    format_options config를 받아 DoclingDocument를 반환하는 로더.

    config 예시 (docling 파이프라인):
        {
            "pdf":  {"pipeline_options": "pdf", "backend": "pypdf", "generate_picture_images": True},
            "docx": {"pipeline_options": "simple", "backend": "msword"},
        }

    config 예시 (LangChain 백엔드 — Docling 파이프라인 우회):
        {
            "pdf":  {"backend": "langchain_pdf"},
            "docx": {"backend": "langchain_docx"},
            "pptx": {"backend": "langchain_pptx"},
        }
    """

    def __init__(self, format_options: dict, genos_url: str = "") -> None:
        self._format_options_cfg = format_options
        self._genos_url = genos_url
        self._bypass_loaders: dict[str, BaseLoader] = {}
        self._converter: DocumentConverter | None = None
        self._converter_ocr: DocumentConverter | None = None
        self._ocr_modes: dict[str, str] = {}
        self._ocr_check_endpoints: dict[str, str] = {}
        self._setup()

    def _setup(self) -> None:
        docling_formats: dict = {}
        for ext, opt in self._format_options_cfg.items():
            backend = opt.get("backend", "")
            if backend in BYPASS_BACKEND_MAP:
                cls = BYPASS_BACKEND_MAP[backend]
                self._bypass_loaders[ext] = cls(config=opt) if backend in ("tabular", "audio") else cls()
            else:
                docling_formats[ext] = opt

        if not docling_formats:
            return

        for ext, opt in docling_formats.items():
            ocr_val = opt.get("ocr")
            if ocr_val is not None and ocr_val is not False:
                mode = str(ocr_val.get("ocr_mode", "force") if isinstance(ocr_val, dict) else "force").lower().strip()
                if mode not in {"auto", "force", "disable"}:
                    _log.warning(f"[DoclingLoader] 알 수 없는 ocr_mode '{mode}' ({ext}), force로 대체")
                    mode = "force"
                if isinstance(ocr_val, dict):
                    endpoint = ocr_val.get("auto_ocr_check_endpoint", "")
                    if endpoint:
                        self._ocr_check_endpoints[ext] = endpoint
            else:
                mode = "disable"
            self._ocr_modes[ext] = mode

        auto_exts = {ext for ext, m in self._ocr_modes.items() if m == "auto"}
        disable_exts = {ext for ext, m in self._ocr_modes.items() if m == "disable"}

        # _converter: auto → OCR OFF, force → OCR ON, disable → OCR OFF
        self._converter = self._build_document_converter(docling_formats, ocr_off=auto_exts | disable_exts)

        # _converter_ocr: auto → OCR ON (force/disable는 _converter와 동일하므로 무관)
        if auto_exts:
            self._converter_ocr = self._build_document_converter(docling_formats, ocr_off=disable_exts)

    def _build_document_converter(self, native_formats: dict, ocr_off: set | None = None) -> DocumentConverter:
        allowed_formats = []
        format_options = {}

        for ext, opt in native_formats.items():
            input_format = FORMAT_MAP.get(ext)
            assert input_format is not None, f"지원하지 않는 확장자: {ext}. 가능한 값: {list(FORMAT_MAP.keys())}"
            allowed_formats.append(input_format)

            fmt_opt_cls = FORMAT_OPTION_MAP.get(input_format)
            if fmt_opt_cls is None:
                # pptx, xlsx, csv 등 pipeline_cls/backend_cls 인자 불필요한 포맷
                allowed_formats.append(input_format)
                continue

            pipeline_cls = PIPELINE_MAP.get(opt.get("pipeline_options", "simple"), SimplePipeline)
            backend_cls = BACKEND_MAP.get(opt.get("backend", "pypdf"), PyPdfiumDocumentBackend)

            fmt_opt = fmt_opt_cls(pipeline_cls=pipeline_cls, backend=backend_cls)

            if opt.get("generate_picture_images"):
                fmt_opt.pipeline_options.generate_picture_images = True
            if opt.get("save_images"):
                fmt_opt.pipeline_options.save_images = True
            if hasattr(fmt_opt.pipeline_options, "do_ocr"):
                ocr_val = opt.get("ocr")
                force_off = ocr_off is not None and ext in ocr_off
                if not force_off and ocr_val is not None and ocr_val is not False:
                    fmt_opt.pipeline_options.do_ocr = True
                    fmt_opt.pipeline_options.ocr_options = _load_ocr_options(ocr_val)
                else:
                    fmt_opt.pipeline_options.do_ocr = False
            if opt.get("picture_description") not in (None, False):
                fmt_opt.pipeline_options.picture_description = True
                raw = opt["picture_description"]
                assert len(raw) == 1, "picture_description dict는 kind 키 하나만 허용 (api | vlm)"
                kind, opts = next(iter(raw.items()))
                opts = {k: v for k, v in (opts or {}).items() if v is not None}
                if kind == "api":
                    if "serving_id" in opts and self._genos_url:
                        sid = opts.pop("serving_id")
                        opts["url"] = f"{self._genos_url}/api/gateway/rep/serving/{sid}/v1/chat/completions"
                    fmt_opt.pipeline_options.picture_description_options = PictureDescriptionApiOptions(**opts)
                    fmt_opt.pipeline_options.enable_remote_services = True
                elif kind == "vlm":
                    fmt_opt.pipeline_options.picture_description_options = PictureDescriptionVlmOptions(**opts)
                else:
                    raise ValueError(f"지원하지 않는 picture_description kind: {kind}. 가능한 값: api | vlm")

            if "genos_layout" in opt:
                # dots OCR (GENOS_LAYOUT) 레이아웃 모델 설정
                genos_cfg = {k: v for k, v in (opt["genos_layout"] or {}).items() if v is not None}
                page_batch_size = genos_cfg.pop("page_batch_size", None)

                fmt_opt.pipeline_options.layout_options.layout_model_type = LayoutModelType.GENOS_LAYOUT
                for k, v in genos_cfg.items():
                    setattr(fmt_opt.pipeline_options.layout_options.genos_layout_options, k, v)

                if page_batch_size is not None:
                    _docling_settings.perf.page_batch_size = int(page_batch_size)

                fmt_opt.pipeline_options.enable_remote_services = True

            format_options[input_format] = fmt_opt

        return DocumentConverter(allowed_formats=allowed_formats, format_options=format_options)

    def _convert(self, file_path: str, converter: DocumentConverter) -> DoclingDocument:
        conv_result = converter.convert(file_path, raises_on_error=True)
        document = conv_result.document
        output_path, output_file = os.path.split(file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(f"{output_path}/{filename}")
        reference_path = None if artifacts_dir.is_absolute() else artifacts_dir.parent
        return document._with_pictures_refs(image_dir=artifacts_dir, page_no=None, reference_path=reference_path)

    def load(self, file_path: str) -> DoclingDocument:
        ext = Path(file_path).suffix.lstrip(".").lower()
        if ext in self._bypass_loaders:
            return self._bypass_loaders[ext].load(file_path)

        if self._ocr_modes.get(ext) == "auto" and self._converter_ocr is not None:
            document = self._convert(file_path, self._converter)
            endpoint = self._ocr_check_endpoints.get(ext, "")
            if endpoint:
                good = _check_text_quality_llm(document, endpoint)
            else:
                good = _has_good_text(document)
            if not good:
                _log.info(f"[DoclingLoader] 텍스트 품질 불량, OCR 재시도: {file_path}")
                document = self._convert(file_path, self._converter_ocr)
            return document

        return self._convert(file_path, self._converter)
