from pathlib import Path

from docling.document_converter import DocumentConverter, HwpxFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PipelineOptions
from docling.backend.genos_hwp_backend import GenosHwpDocumentBackend
from docling.backend.hwp_backend import HwpDocumentBackend
from docling.backend.xml.hwpx_backend import HwpxDocumentBackend
from docling_core.types import DoclingDocument

from .base_loader import BaseLoader

SUPPORTED_EXTENSIONS = {".hwp", ".hwpx"}


class GenosHwpLoader(BaseLoader):
    """HWP/HWPX를 GenosHwpDocumentBackend로 직접 로드.
    실패 시 레거시 백엔드(HwpDocumentBackend / HwpxDocumentBackend)로 폴백.
    """

    def __init__(self, config: dict) -> None:
        self.config = config

    def _make_converter(self, backend) -> DocumentConverter:
        opts = PipelineOptions()
        return DocumentConverter(
            format_options={
                InputFormat.HWP: HwpxFormatOption(pipeline_options=opts, backend=backend),
                InputFormat.XML_HWPX: HwpxFormatOption(pipeline_options=opts, backend=backend),
            }
        )

    def load(self, file_path: str) -> DoclingDocument:
        ext = Path(file_path).suffix.lower()
        assert ext in SUPPORTED_EXTENSIONS, f"GenosHwpLoader가 지원하지 않는 확장자: {ext}"

        try:
            converter = self._make_converter(GenosHwpDocumentBackend)
            return converter.convert(Path(file_path).resolve(), raises_on_error=True).document
        except Exception:
            fallback = HwpDocumentBackend if ext == ".hwp" else HwpxDocumentBackend
            converter = self._make_converter(fallback)
            return converter.convert(Path(file_path).resolve(), raises_on_error=True).document
