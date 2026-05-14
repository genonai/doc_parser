"""
구버전 LangChain 로더(PyMuPDFLoader, Unstructured*, TextLoader)를 DoclingDocument로 래핑한다.

- LangChainPdfLoader : PyMuPDFLoader — 페이지 단위 (1 page = 1 TextItem)
- LangChainDocxLoader: UnstructuredWordDocumentLoader(mode='elements') — 요소 단위
- LangChainPptxLoader: UnstructuredPowerPointLoader — 단일 문서 (구버전 동작 일치)
- LangChainMdLoader  : TextLoader — 단일 문서 (구버전 동작 일치)
"""
from __future__ import annotations

import os
from typing import Sequence

from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DocumentOrigin,
    PageItem,
    ProvenanceItem,
    Size,
)
from docling_core.types.doc.document import DoclingDocument

from .base_loader import BaseLoader

_DEFAULT_PAGE_SIZE = Size(width=612.0, height=792.0)

# Unstructured category → DocItemLabel 매핑
_CATEGORY_MAP: dict[str, DocItemLabel] = {
    "Title": DocItemLabel.SECTION_HEADER,
    "Header": DocItemLabel.SECTION_HEADER,
    "NarrativeText": DocItemLabel.PARAGRAPH,
    "UncategorizedText": DocItemLabel.PARAGRAPH,
    "ListItem": DocItemLabel.LIST_ITEM,
    "CodeSnippet": DocItemLabel.CODE,
    "Table": DocItemLabel.PARAGRAPH,  # TableItem 변환 없이 텍스트로 처리
}
# 건너뛸 카테고리 (내용 없거나 레이아웃 마커)
_SKIP_CATEGORIES = {"PageBreak", "Image", "Footer", "FigureCaption"}


def _ensure_page(doc: DoclingDocument, page_no: int) -> None:
    if page_no not in doc.pages:
        doc.pages[page_no] = PageItem(page_no=page_no, size=_DEFAULT_PAGE_SIZE)


def _add_lc_doc(doc: DoclingDocument, lc_doc, page_no: int, label: DocItemLabel) -> None:
    text = lc_doc.page_content.strip()
    if not text:
        return
    _ensure_page(doc, page_no)
    bbox = BoundingBox(l=0.0, t=0.0, r=_DEFAULT_PAGE_SIZE.width, b=_DEFAULT_PAGE_SIZE.height)
    prov = ProvenanceItem(page_no=page_no, bbox=bbox, charspan=(0, len(text)))
    doc.add_text(label=label, text=text, orig=text, prov=prov)


def _pdf_lc_docs_to_docling(file_path: str, lc_docs: Sequence) -> DoclingDocument:
    """PyMuPDFLoader 출력(페이지당 1 doc, 0-based page) → DoclingDocument."""
    doc = DoclingDocument(name=os.path.basename(file_path))
    for lc_doc in lc_docs:
        raw_page = lc_doc.metadata.get("page", 0)
        page_no = (raw_page + 1) if isinstance(raw_page, int) else 1
        _add_lc_doc(doc, lc_doc, page_no, DocItemLabel.PARAGRAPH)
    return doc


def _elements_lc_docs_to_docling(file_path: str, lc_docs: Sequence) -> DoclingDocument:
    """Unstructured elements 출력(1-based page_number) → DoclingDocument."""
    doc = DoclingDocument(name=os.path.basename(file_path))
    for lc_doc in lc_docs:
        category = lc_doc.metadata.get("category", "")
        if category in _SKIP_CATEGORIES:
            continue
        page_no = lc_doc.metadata.get("page_number", 1)
        if not isinstance(page_no, int):
            page_no = 1
        label = _CATEGORY_MAP.get(category, DocItemLabel.PARAGRAPH)
        _add_lc_doc(doc, lc_doc, page_no, label)
    return doc


class LangChainPdfLoader(BaseLoader):
    """PyMuPDFLoader로 PDF를 페이지 단위로 읽어 DoclingDocument로 반환."""

    def load(self, file_path: str) -> DoclingDocument:
        from langchain_community.document_loaders import PyMuPDFLoader
        lc_docs = PyMuPDFLoader(file_path).load()
        return _pdf_lc_docs_to_docling(file_path, lc_docs)


class LangChainDocxLoader(BaseLoader):
    """UnstructuredWordDocumentLoader(elements)로 DOCX를 읽어 DoclingDocument로 반환."""

    def load(self, file_path: str) -> DoclingDocument:
        from langchain_community.document_loaders import UnstructuredWordDocumentLoader
        lc_docs = UnstructuredWordDocumentLoader(file_path, mode="elements").load()
        return _elements_lc_docs_to_docling(file_path, lc_docs)


class LangChainPptxLoader(BaseLoader):
    """UnstructuredPowerPointLoader(single)로 PPTX/PPT를 읽어 DoclingDocument로 반환."""

    def load(self, file_path: str) -> DoclingDocument:
        from langchain_community.document_loaders import UnstructuredPowerPointLoader
        lc_docs = UnstructuredPowerPointLoader(file_path).load()
        return _pdf_lc_docs_to_docling(file_path, lc_docs)


class LangChainMdLoader(BaseLoader):
    """
    구버전 TextLoader와 동일한 로직으로 TXT/JSON/MD를 읽어 DoclingDocument로 반환.

    chardet 인코딩 감지 → HTML 래핑 → WeasyPrint PDF 변환 → PyMuPDFLoader.
    WeasyPrint 사용 불가 시 raw 텍스트를 단일 TextItem으로 반환.
    """

    _ENCODINGS_FALLBACK = ["utf-8", "cp949", "euc-kr", "iso-8859-1", "latin-1"]

    def _read_with_encoding(self, file_path: str) -> str:
        import chardet
        with open(file_path, "rb") as f:
            raw = f.read()
        detected = chardet.detect(raw).get("encoding") or ""
        candidates = (
            [detected] if detected and detected.lower() not in ("ascii", "unknown") else []
        )
        candidates += self._ENCODINGS_FALLBACK
        for enc in candidates:
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def load(self, file_path: str) -> DoclingDocument:
        import os
        import tempfile
        from pathlib import Path

        content = self._read_with_encoding(file_path)

        # WeasyPrint로 HTML → PDF 변환 후 PyMuPDFLoader로 읽기 (구버전 동일 경로)
        try:
            from weasyprint import HTML
            from langchain_community.document_loaders import PyMuPDFLoader

            html = f"<html><meta charset='utf-8'><body><pre>{content}</pre></body></html>"

            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_html = os.path.join(tmp_dir, "input.html")
                tmp_pdf  = os.path.join(tmp_dir, "output.pdf")
                with open(tmp_html, "w", encoding="utf-8") as f:
                    f.write(html)
                HTML(tmp_html).write_pdf(tmp_pdf)
                lc_docs = PyMuPDFLoader(tmp_pdf).load()

            return _pdf_lc_docs_to_docling(file_path, lc_docs)

        except Exception:
            # WeasyPrint 불가 시 raw 텍스트 단일 TextItem으로 폴백
            doc = DoclingDocument(name=os.path.basename(file_path))
            _ensure_page(doc, 1)
            from docling_core.types.doc import BoundingBox, ProvenanceItem
            bbox = BoundingBox(l=0.0, t=0.0, r=_DEFAULT_PAGE_SIZE.width, b=_DEFAULT_PAGE_SIZE.height)
            prov = ProvenanceItem(page_no=1, bbox=bbox, charspan=(0, len(content)))
            doc.add_text(label=DocItemLabel.PARAGRAPH, text=content, orig=content, prov=prov)
            return doc
