from typing import Any, List

from fastapi import Request
from langchain_core.documents import Document
from docling.document_converter import DocumentConverter, PdfFormatOption, HwpxFormatOption, WordFormatOption
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
from docling.backend.msword_backend import MsWordDocumentBackend
from docling.backend.genos_msword_backend import GenosMsWordDocumentBackend
from docling_core.transforms.chunker import BaseChunker, DocChunk
from docling_core.types import DoclingDocument
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.pipeline.simple_pipeline import SimplePipeline


from utils.chunkers import CHUNKERS
from utils.metadata import GenOSVectorMetaBuilder


# load 파이프라인
# open -> table -> reading order

"""
모델들
- detection model
- recognition model
- ocr
    - easy
    -  paddle

- table 모델

- vlm
    - 이미지 디스크립션 모델
    - 문서 로테이션 모델
    - TOC 생성 모델
"""

"""
컴포넌트
 - base64로 오면 저장 (oneagent용)
 - 파일 오픈: 확장자 별로....
 - pdf로 저장(GenOS에서 보여주려고)
 - 리딩오더
 - 레이아웃
 - 테이블 디텍션
 - 이미지 디스크립션
 - ocr -> 용도별로
 - 이미지 로테이션
"""

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
    # "md": InputFormat.MD,
    # "json": InputFormat.JSON,
    # "html": InputFormat.HTML,
}


# TODO all ext
FORMAT_OPTION_MAP = {
    InputFormat.PDF: PdfFormatOption,
    # InputFormat.HWP: HwpFormatOption # TODO 왜 HwpFormatOption은 없는지
    # "hwpx":InputFormat.HWPX,# TODO
    # "doc":InputFormat.DOC, # TODO
    InputFormat.DOCX: WordFormatOption,
    # "ppt": InputFormat.PPT, #TODO
    # InputFormat.PPTX,
    # InputFormat.XLSX,
    # InputFormat.CSV,
    # InputFormat.MD,
    # InputFormat.JSON,
    # InputFormat.HTML,
}

PIPELINE_MAP = {
    "pdf": StandardPdfPipeline,
    "simple": SimplePipeline,
}

BACKEND_MAP = {
    "pypdf": PyPdfiumDocumentBackend,
    "msword": GenosMsWordDocumentBackend,
}


class BaseProcessor:
    pipeline: list[str] = None
    format_options: None
    chunker: BaseChunker = None
    loaders: list = None
    converter: DocumentConverter = None
    config: dict = None

    def __init__(self, config: dict) -> None:
        # mapping 해주자
        self.config = config
        self.allowed_formats = self._build_allowed_formats()
        self.format_options = self._build_format_options()
        self.converter = DocumentConverter(
            allowed_formats=self.allowed_formats,
            format_options=self.format_options,
        )

        # self.loaders = LOADERS["pdf"]  # 로더 왜 필요하더라

        self.chunker = CHUNKERS["simple"]()
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

            # @@@ 성민: pdf 일때만 이미지 저장이 가능하네
            if "generate_picture_images" in option and option["generate_picture_images"] == True:
                format_options[format].pipeline_options.generate_picture_images = True

        return format_options

    def load_documents(self, file_path: str, **kwargs: dict) -> list[Document]:
        """
        설명: 확장자에 해당하는 DocumentConverter를 사용하여 ConversionResult 리턴
        """
        # TODO: OneAgent 호출인지 판단. 이게 여기 있어야 할까 __call_ 에 있어야 할까.

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
        chunks = self.split_documents(documents, **kwargs)
        vectors = await self.compose_vectors(request, file_path, documents, chunks, **kwargs)
        return vectors
