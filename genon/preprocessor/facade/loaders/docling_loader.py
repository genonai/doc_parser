import os
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

from .base_loader import BaseLoader
from .tabular_loader import TabularLoader
from .audio_loader import AudioLoader
from .langchain_loaders import LangChainPdfLoader, LangChainDocxLoader, LangChainPptxLoader, LangChainMdLoader

_OCR_YAML_DIR = Path(__file__).parent.parent / "configs" / "ocr"

OCR_ENGINE_MAP = {
    "easy":       EasyOcrOptions,
    "paddle":     PaddleOcrOptions,
    "tesseract":  TesseractOcrOptions,
    "tesseractcli": TesseractCliOcrOptions,
    "rapid":      RapidOcrOptions,
}


def _load_ocr_options(do_ocr_cfg: dict | str):
    """
    do_ocr 설정을 OcrOptions 인스턴스로 변환.

    형식 1 — 인라인 dict:
        {"paddle": {"force_full_page_ocr": True, "lang": ["korean"]}}

    형식 2 — 엔진 이름 문자열 (기본값 사용):
        "easy"
    """
    if isinstance(do_ocr_cfg, str):
        engine_key = do_ocr_cfg.lower()
        options_dict = {}
    else:
        assert len(do_ocr_cfg) == 1, "do_ocr dict는 엔진 이름 키 하나만 허용"
        engine_key, raw_options = next(iter(do_ocr_cfg.items()))
        engine_key = engine_key.lower()
        options_dict = {k: v for k, v in (raw_options or {}).items() if v is not None}

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
        if docling_formats:
            self._converter = self._build_document_converter(docling_formats)

    def _build_document_converter(self, native_formats: dict) -> DocumentConverter:
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
                if "do_ocr" in opt:
                    fmt_opt.pipeline_options.do_ocr = True
                    fmt_opt.pipeline_options.ocr_options = _load_ocr_options(opt["do_ocr"])
                else:
                    fmt_opt.pipeline_options.do_ocr = False
            if "do_picture_description" in opt:
                fmt_opt.pipeline_options.do_picture_description = True
                raw = opt["do_picture_description"]
                assert len(raw) == 1, "do_picture_description dict는 kind 키 하나만 허용 (api | vlm)"
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

    def load(self, file_path: str) -> DoclingDocument:
        ext = Path(file_path).suffix.lstrip(".").lower()
        if ext in self._bypass_loaders:
            return self._bypass_loaders[ext].load(file_path)
        conv_result = self._converter.convert(file_path, raises_on_error=True)
        document = conv_result.document

        output_path, output_file = os.path.split(file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(f"{output_path}/{filename}")
        reference_path = None if artifacts_dir.is_absolute() else artifacts_dir.parent
        document = document._with_pictures_refs(image_dir=artifacts_dir, page_no=None, reference_path=reference_path)

        return document
