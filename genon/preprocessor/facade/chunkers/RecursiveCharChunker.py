"""
구버전 RecursiveCharacterTextSplitter와 동일한 방식의 chunker.

DoclingDocument의 각 TextItem 텍스트를 chunk_size 문자 단위로 분할한다.
구버전 attachment_processor의 split_documents 동작을 DocChunk로 래핑.
"""
from __future__ import annotations

from typing import Any, Iterator

from docling_core.transforms.chunker import BaseChunker, DocChunk, DocMeta
from docling_core.types.doc.document import DoclingDocument


class RecursiveCharChunker(BaseChunker):
    """RecursiveCharacterTextSplitter(chunk_size, chunk_overlap)를 DoclingDocument에 적용."""

    max_tokens: int = 1000
    chunk_overlap: int = 100
    tokenizer: str = "char"

    def chunk(self, dl_doc: DoclingDocument, **kwargs: Any) -> Iterator[DocChunk]:
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.max_tokens,
            chunk_overlap=self.chunk_overlap,
        )

        origin = dl_doc.origin

        for text_item in dl_doc.texts:
            text = text_item.text.strip()
            if not text:
                continue

            segments = splitter.split_text(text)
            meta = DocMeta(doc_items=[text_item], origin=origin)

            for segment in segments:
                if segment.strip():
                    yield DocChunk(text=segment, meta=meta)
