import logging
from io import BytesIO
from pathlib import Path
from typing import Union, Set
import fitz  # PyMuPDF
from docling_core.types.doc import (
    DoclingDocument,
    DocumentOrigin,
    DocItemLabel,
    ProvenanceItem,
    BoundingBox,
    Size,
)
from docling.backend.abstract_backend import DeclarativeDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument

_log = logging.getLogger(__name__)


class PyMuPDFDocumentBackend(DeclarativeDocumentBackend):
    """PyMuPDF를 사용한 PDF 텍스트 추출 백엔드 (모든 페이지를 하나의 텍스트로 처리)"""
    def __init__(self, in_doc: InputDocument, path_or_stream: Union[Path, BytesIO]) -> None:
        super().__init__(in_doc, path_or_stream)
        self.doc = None
        self.valid = False
        try:
            if isinstance(path_or_stream, BytesIO):
                self.doc = fitz.open(stream=path_or_stream.getvalue(), filetype="pdf")
            elif isinstance(path_or_stream, Path):
                self.doc = fitz.open(str(path_or_stream))
            if self.doc and getattr(self.doc, "page_count", 0) > 0:
                self.valid = True
        except Exception as e:
            _log.error(f"Failed to open PDF document: {e}")
            self.valid = False

    def is_valid(self) -> bool:
        return self.valid

    @classmethod
    def supported_formats(cls) -> Set[InputFormat]:
        return {InputFormat.PDF}

    @classmethod
    def supports_pagination(cls) -> bool:
        # 페이지 구분 없이 하나의 페이지로 처리
        return True

    def unload(self) -> None:
        if self.doc:
            self.doc.close()
            self.doc = None

    def convert(self) -> DoclingDocument:
        """PDF 전체를 하나의 페이지(1로 고정)와 하나의 텍스트 덩어리로 변환"""
        if not self.is_valid():
            raise RuntimeError("Invalid or unsupported PDF document")

        # DocumentOrigin 설정
        origin = DocumentOrigin(
            filename=self.file.name or "file",
            mimetype="application/pdf",
            binary_hash=self.document_hash,
        )
        # DoclingDocument 생성
        doc = DoclingDocument(name=self.file.stem or "file", origin=origin)

        # 단일 페이지로 처리: 항상 1번 페이지, 크기는 1×1
        page_no = 1
        doc.pages[page_no] = doc.add_page(
            page_no=page_no,
            size=Size(width=1, height=1),
        )

        # PDF의 모든 페이지 텍스트를 합치기
        texts = []
        for pnum in range(self.doc.page_count):
            try:
                page = self.doc.load_page(pnum)
                txt = page.get_text() or ""
            except Exception as e:
                _log.warning(f"페이지 {pnum} 읽기 실패: {e}")
                txt = ""
            texts.append(txt)
        full_text = "\n".join(texts)

        # 단락 단위로 분할하고 추가
        lines = full_text.split("\n")
        buffer = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if buffer:
                    paragraph = " ".join(buffer)
                    doc.add_text(
                        label=DocItemLabel.PARAGRAPH,
                        text=paragraph,
                        parent=None,
                        prov=ProvenanceItem(
                            page_no=page_no,
                            bbox=BoundingBox(l=0, t=0, r=1, b=1),
                            charspan=(0, len(paragraph)),
                        ),
                    )
                    buffer = []
            else:
                buffer.append(stripped)
        # 남은 버퍼
        if buffer:
            paragraph = " ".join(buffer)
            doc.add_text(
                label=DocItemLabel.PARAGRAPH,
                text=paragraph,
                parent=None,
                prov=ProvenanceItem(
                    page_no=page_no,
                    bbox=BoundingBox(l=0, t=0, r=1, b=1),
                    charspan=(0, len(paragraph)),
                ),
            )

        return doc
