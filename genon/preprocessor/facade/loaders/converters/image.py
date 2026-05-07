from pathlib import Path

import fitz  # PyMuPDF

from .base_converter import BaseFormatConverter

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif", ".webp"}


class ImageConverter(BaseFormatConverter):
    def convert(self, src: str) -> str:
        in_path = Path(src).resolve()
        ext = in_path.suffix.lower()

        assert ext in SUPPORTED_EXTENSIONS, f"ImageConverter가 지원하지 않는 확장자: {ext}"

        pdf_path = in_path.with_suffix(".pdf")

        img_doc = fitz.open(src)
        pdf_bytes = img_doc.convert_to_pdf()
        img_doc.close()

        pdf_doc = fitz.open("pdf", pdf_bytes)
        pdf_doc.save(str(pdf_path))
        pdf_doc.close()

        return str(pdf_path)
