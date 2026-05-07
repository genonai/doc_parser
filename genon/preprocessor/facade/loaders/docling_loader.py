from pathlib import Path

from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    HwpxFormatOption,
    WordFormatOption,
    MarkdownFormatOption,
    HTMLFormatOption,
)
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, PipelineOptions
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.backend.pymupdf_backend import PyMuPDFDocumentBackend
from docling.backend.genos_msword_backend import GenosMsWordDocumentBackend
from docling.backend.html_backend import HTMLDocumentBackend
from docling.backend.md_backend import MarkdownDocumentBackend
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.pipeline.simple_pipeline import SimplePipeline
from docling_core.types import DoclingDocument

from .base_loader import BaseLoader
from .converters.registry import get_converter

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
}


class DoclingLoader(BaseLoader):
    """
    format_options config를 받아 DoclingDocument를 반환하는 로더.

    확장자에 "converter" 키가 있으면 → converter로 PDF 변환 후 로드.
    없으면 → docling이 직접 로드.

    config 예시:
        {
            "pdf":  {"pipeline_options": "pdf", "backend": "pypdf", "generate_picture_images": True},
            "docx": {"pipeline_options": "simple", "backend": "msword"},
            "hwpx": {"converter": "libreoffice"},
            "pptx": {"converter": "libreoffice"},
            "png":  {"converter": "image"},
        }
    """

    def __init__(self, format_options: dict) -> None:
        self._format_options_cfg = format_options
        self._converters: dict[str, object] = {}
        self._converter: DocumentConverter | None = None
        self._setup()

    def _setup(self) -> None:
        native_formats: dict[str, dict] = {}

        for ext, opt in self._format_options_cfg.items():
            if "converter" in opt:
                self._converters[ext] = get_converter(opt["converter"])
            else:
                native_formats[ext] = opt

        # PDF는 converter 후 항상 로드하므로 native_formats에 없어도 DocumentConverter에 추가
        if native_formats or self._converters:
            pdf_key = "pdf"
            if pdf_key not in native_formats and self._converters:
                native_formats[pdf_key] = {"pipeline_options": "pdf", "backend": "pypdf"}

        self._converter = self._build_document_converter(native_formats)

    def _build_document_converter(self, native_formats: dict) -> DocumentConverter:
        allowed_formats = []
        format_options = {}

        for ext, opt in native_formats.items():
            input_format = FORMAT_MAP.get(ext)
            assert input_format is not None, (
                f"지원하지 않는 확장자: {ext}. 가능한 값: {list(FORMAT_MAP.keys())}"
            )
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

            format_options[input_format] = fmt_opt

        return DocumentConverter(allowed_formats=allowed_formats, format_options=format_options)

    def load(self, file_path: str) -> DoclingDocument:
        ext = Path(file_path).suffix.lstrip(".").lower()

        if ext in self._converters:
            file_path = self._converters[ext].convert(file_path)

        conv_result = self._converter.convert(file_path, raises_on_error=True)
        return conv_result.document
