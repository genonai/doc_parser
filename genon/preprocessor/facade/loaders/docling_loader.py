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
from docling.datamodel.pipeline_options import PdfPipelineOptions
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
from .langchain_loaders import LangChainPdfLoader, LangChainDocxLoader, LangChainPptxLoader, LangChainMdLoader

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

# backend 값이 langchain_* 이름이면 LangChain 기반 래퍼로 처리 (Docling 파이프라인 우회)
LANGCHAIN_BACKEND_MAP: dict[str, type[BaseLoader]] = {
    "langchain_pdf": LangChainPdfLoader,
    "langchain_docx": LangChainDocxLoader,
    "langchain_pptx": LangChainPptxLoader,
    "langchain_md": LangChainMdLoader,
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

    def __init__(self, format_options: dict) -> None:
        self._format_options_cfg = format_options
        self._langchain_ext_loaders: dict[str, BaseLoader] = {}
        self._converter: DocumentConverter | None = None
        self._setup()

    def _setup(self) -> None:
        docling_formats: dict = {}
        for ext, opt in self._format_options_cfg.items():
            backend = opt.get("backend", "")
            if backend in LANGCHAIN_BACKEND_MAP:
                self._langchain_ext_loaders[ext] = LANGCHAIN_BACKEND_MAP[backend]()
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
            if "do_ocr" in opt:
                fmt_opt.pipeline_options.do_ocr = opt["do_ocr"]

            format_options[input_format] = fmt_opt

        return DocumentConverter(allowed_formats=allowed_formats, format_options=format_options)

    def load(self, file_path: str) -> DoclingDocument:
        ext = Path(file_path).suffix.lstrip(".").lower()
        if ext in self._langchain_ext_loaders:
            return self._langchain_ext_loaders[ext].load(file_path)
        conv_result = self._converter.convert(file_path, raises_on_error=True)
        return conv_result.document
