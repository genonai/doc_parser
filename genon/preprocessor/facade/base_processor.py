from typing import Any, List

from fastapi import Request
from langchain_core.documents import Document
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    HwpxFormatOption,
    WordFormatOption,
    MarkdownFormatOption,
    HTMLFormatOption,
)
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    PdfPipelineOptions,
    TableFormerMode,
    PipelineOptions,
    PaddleOcrOptions,
)
from docling.datamodel.base_models import InputFormat
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.backend.pymupdf_backend import PyMuPDFDocumentBackend
from docling.backend.genos_msword_backend import GenosMsWordDocumentBackend
from docling.backend.html_backend import HTMLDocumentBackend
from docling.backend.md_backend import MarkdownDocumentBackend

from docling_core.transforms.chunker import BaseChunker, DocChunk
from docling_core.types import DoclingDocument
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.pipeline.simple_pipeline import SimplePipeline


from genon.preprocessor.facade.chunkers import CHUNKERS
from genon.preprocessor.facade.metadata import GenOSVectorMetaBuilder
from genon.preprocessor.facade.utils.logging_utils import setup_logging

# TODO all ext
FORMAT_MAP = {
    "pdf": InputFormat.PDF,
    # "hwp": InputFormat.HWP,
    # "hwpx":InputFormat.HWPX,# TODO
    # "doc":InputFormat.DOC, # TODO
    "docx": InputFormat.DOCX,
    # "ppt": InputFormat.PPT, #TODO
    # "pptx": InputFormat.PPTX,
    # "xlsx": InputFormat.XLSX,
    # "csv": InputFormat.CSV,
    "md": InputFormat.MD,
    # "json": InputFormat.JSON,
    "html": InputFormat.HTML,
}


# TODO all ext
FORMAT_OPTION_MAP = {
    InputFormat.PDF: PdfFormatOption,
    # InputFormat.HWP: HwpFormatOption # TODO 왜 HwpFormatOption..?
    # "hwpx":InputFormat.HWPX,# TODO
    # "doc":InputFormat.DOC, # TODO
    InputFormat.DOCX: WordFormatOption,
    InputFormat.MD: MarkdownFormatOption,
    # "ppt": InputFormat.PPT, #TODO
    # InputFormat.PPTX,
    # InputFormat.XLSX,
    # InputFormat.CSV,
    # InputFormat.JSON,
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


class BaseProcessor:
    pipeline: list[str] = None
    format_options: None
    chunker: BaseChunker = None
    loaders: list = None
    converter: DocumentConverter = None
    config: dict = None

    def __init__(self, config: dict) -> None:

        self.config = config
        setup_logging(int(config.get("log_level", 0)))
        self.return_level = config.get("return_level", "vector")

        assert self.return_level in [
            "document",
            "chunk",
            "vector",
        ], f"다음 리턴 레벨 중에서 골라주세요: document | chunk | vector, 현재 리턴 레벨: {self.return_level}"

        self.allowed_formats = self._build_allowed_formats()
        self.format_options = self._build_format_options()
        self.converter = DocumentConverter(
            allowed_formats=self.allowed_formats,
            format_options=self.format_options,
        )

        self.chunker = CHUNKERS[self.config["chunker"]]()
        self.genos_meta_builder = GenOSVectorMetaBuilder()

    def _build_allowed_formats(self):
        allowed_formats = []
        for _format in self.config["format_options"].keys():
            format = FORMAT_MAP.get(_format, None)
            assert format is not None, f"@@@@ 잘못된 확장자입니다. {_format}, 가능한 확장자: {list(FORMAT_MAP.keys())}"
            allowed_formats.append(format)
        return allowed_formats

    def _build_format_options(self):
        format_options = {}
        for _format, option in self.config["format_options"].items():
            format = FORMAT_MAP.get(_format, None)

            format_options[format] = FORMAT_OPTION_MAP[format](
                pipeline_cls=PIPELINE_MAP[option["pipeline_options"]],
                backend=BACKEND_MAP[option["backend"]],
            )

            if "generate_picture_images" in option and option["generate_picture_images"] == True:
                format_options[format].pipeline_options.generate_picture_images = True

            if "save_images" in option and option["save_images"] == True:
                format_options[format].pipeline_options.save_images = True

        return format_options

    def load_documents(self, file_path: str, **kwargs: dict) -> list[Document]:
        """
        설명: 확장자에 해당하는 DocumentConverter를 사용하여 ConversionResult 리턴
        """
        # TODO: OneAgent 호출인지 판단.

        conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)

        return conv_result.document

    def split_documents(self, documents: list[Document], **kwargs: dict) -> list[Document]:
        chunks = list(self.chunker.chunk(documents, **kwargs))
        return chunks

    async def compose_vectors(
        self, request: Request, file_path: str, document: DoclingDocument, chunks: List[DocChunk], **kwargs: dict
    ) -> list[dict]:
        return await self.genos_meta_builder(document, chunks, file_path, request, **kwargs)

    async def __call__(self, request: Request, file_path: str, **kwargs: dict) -> Any:
        documents = self.load_documents(file_path, **kwargs)
        if self.return_level == "document":
            return documents

        chunks = self.split_documents(documents, **kwargs)
        if self.return_level == "chunk":
            return self.chunks

        vectors = await self.compose_vectors(request, file_path, documents, chunks, **kwargs)
        return vectors
