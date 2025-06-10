import logging
from io import BytesIO
from pathlib import Path
from typing import Optional, Union, Set
import fitz  # PyMuPDF
from docling_core.types.doc import DoclingDocument, DocumentOrigin, DocItemLabel, ProvenanceItem, BoundingBox, Size
from docling.backend.abstract_backend import DeclarativeDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument

_log = logging.getLogger(__name__)


class PyMuPDFDocumentBackend(DeclarativeDocumentBackend):
    """PyMuPDF를 사용한 PDF 텍스트 추출 백엔드"""
    
    def __init__(self, in_doc: InputDocument, path_or_stream: Union[Path, BytesIO]) -> None:
        """PyMuPDF 백엔드 초기화"""
        super().__init__(in_doc, path_or_stream)
        self.doc = None
        self.valid = False
        
        try:
            if isinstance(path_or_stream, BytesIO):
                self.doc = fitz.open(stream=path_or_stream.getvalue(), filetype="pdf")
            elif isinstance(path_or_stream, Path):
                self.doc = fitz.open(str(path_or_stream))
            
            if self.doc and self.doc.page_count > 0:
                self.valid = True
        except Exception as e:
            _log.error(f"Failed to open PDF document: {e}")
            self.valid = False

    def is_valid(self) -> bool:
        """문서가 유효한지 확인"""
        return self.valid

    @classmethod
    def supported_formats(cls) -> Set[InputFormat]:
        """지원하는 파일 형식 반환"""
        return {InputFormat.PDF}

    @classmethod
    def supports_pagination(cls) -> bool:
        """페이지네이션 지원 여부"""
        return True

    def unload(self) -> None:
        """리소스 해제"""
        if self.doc:
            self.doc.close()
            self.doc = None

    def convert(self) -> DoclingDocument:
        """PDF를 DoclingDocument로 변환"""
        if not self.is_valid():
            raise RuntimeError("Invalid or unsupported PDF document")

        # DocumentOrigin 설정
        origin = DocumentOrigin(
            filename=self.file.name or "file",
            mimetype="application/pdf",
            binary_hash=self.document_hash
        )
        
        # DoclingDocument 생성
        doc = DoclingDocument(name=self.file.stem or "file", origin=origin)
        
        # 각 페이지의 텍스트를 추출하여 TextItem으로 추가
        for page_num in range(self.doc.page_count):
            page = self.doc.load_page(page_num)
            
            # 페이지 크기 정보 가져오기
            page_rect = page.rect
            page_width = page_rect.width
            page_height = page_rect.height
            
            # DoclingDocument에 페이지 추가 (1-based page numbering)
            doc_page_num = page_num + 1
            doc.pages[doc_page_num] = doc.add_page(
                page_no=doc_page_num, 
                size=Size(width=page_width, height=page_height)
            )
            
            # 페이지 텍스트 추출
            text = page.get_text()
            
            if text.strip():  # 빈 텍스트가 아닌 경우만 추가
                # 페이지별로 텍스트를 분할하여 추가
                # 줄 단위로 분할하되, 너무 짧은 줄은 합침
                lines = text.split('\n')
                current_paragraph = []
                
                for line in lines:
                    line = line.strip()
                    if not line:  # 빈 줄이면 현재 문단을 완성
                        if current_paragraph:
                            paragraph_text = ' '.join(current_paragraph)
                            if paragraph_text.strip():
                                doc.add_text(
                                    label=DocItemLabel.PARAGRAPH,
                                    text=paragraph_text,
                                    parent=None,
                                    prov=ProvenanceItem(
                                        page_no=doc_page_num,
                                        bbox=BoundingBox(l=0, t=0, r=1, b=1),
                                        charspan=(0, len(paragraph_text))
                                    )
                                )
                            current_paragraph = []
                    else:
                        current_paragraph.append(line)
                
                # 마지막 문단 처리
                if current_paragraph:
                    paragraph_text = ' '.join(current_paragraph)
                    if paragraph_text.strip():
                        doc.add_text(
                            label=DocItemLabel.PARAGRAPH,
                            text=paragraph_text,
                            parent=None,
                            prov=ProvenanceItem(
                                page_no=doc_page_num,
                                bbox=BoundingBox(l=0, t=0, r=1, b=1),
                                charspan=(0, len(paragraph_text))
                            )
                        )
            
            page = None  # 메모리 해제
        
        return doc 