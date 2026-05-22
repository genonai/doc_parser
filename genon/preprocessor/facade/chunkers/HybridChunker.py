from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Optional, Union

import semchunk
from pydantic import BaseModel, ConfigDict, PositiveInt, TypeAdapter, model_validator
from transformers import AutoTokenizer, PreTrainedTokenizerBase
from typing_extensions import Self

from docling_core.transforms.chunker import BaseChunk, BaseChunker, DocChunk, DocMeta
from docling_core.types.doc import DoclingDocument, TextItem

from .HierarchicalChunker import HierarchicalChunker
from .tokenizer import CharTokenizer, resolve_tokenizer, DEFAULT_TOKENIZER


class HybridChunker(BaseChunker):
    r"""Chunker doing tokenization-aware refinements on top of document layout chunking.
    Args:
        tokenizer: The tokenizer to use; either instantiated object or name or path of
            respective pretrained model
        max_tokens: The maximum number of tokens per chunk. If not set, limit is
            resolved from the tokenizer
        merge_peers: Whether to merge undersized chunks sharing same relevant metadata
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    tokenizer: Union[PreTrainedTokenizerBase, str, Path] = DEFAULT_TOKENIZER
    max_tokens: int = int(1e30)  # type: ignore[assignment]
    merge_peers: bool = True
    _inner_chunker: HierarchicalChunker = HierarchicalChunker()

    @model_validator(mode="after")
    def _patch_tokenizer_and_max_tokens(self) -> Self:
        self._tokenizer = resolve_tokenizer(self.tokenizer)
        if self.max_tokens is None:
            self.max_tokens = TypeAdapter(PositiveInt).validate_python(
                self._tokenizer.model_max_length
            )
        return self

    def _count_text_tokens(self, text: Optional[Union[str, list[str]]]):
        if text is None:
            return 0
        elif isinstance(text, list):
            total = 0
            for t in text:
                total += self._count_text_tokens(t)
            return total
        return len(self._tokenizer.tokenize(text))

    class _ChunkLengthInfo(BaseModel):
        total_len: int
        text_len: int
        other_len: int

    def _count_chunk_tokens(self, doc_chunk: DocChunk):
        ser_txt = self.serialize(chunk=doc_chunk)
        return len(self._tokenizer.tokenize(text=ser_txt))

    def _doc_chunk_length(self, doc_chunk: DocChunk):
        text_length = self._count_text_tokens(doc_chunk.text)
        total = self._count_chunk_tokens(doc_chunk=doc_chunk)
        return self._ChunkLengthInfo(
            total_len=total,
            text_len=text_length,
            other_len=total - text_length,
        )

    def _make_chunk_from_doc_items(
            self, doc_chunk: DocChunk, window_start: int, window_end: int
    ):
        doc_items = doc_chunk.meta.doc_items[window_start: window_end + 1]
        meta = DocMeta(
            doc_items=doc_items,
            headings=doc_chunk.meta.headings,
            captions=doc_chunk.meta.captions,
            origin=doc_chunk.meta.origin,
        )
        window_text = (
            doc_chunk.text
            if len(doc_chunk.meta.doc_items) == 1
            else self.delim.join(
                [
                    doc_item.text
                    for doc_item in doc_items
                    if isinstance(doc_item, TextItem)
                ]
            )
        )
        new_chunk = DocChunk(text=window_text, meta=meta)
        return new_chunk

    def _split_by_doc_items(self, doc_chunk: DocChunk) -> list[DocChunk]:
        chunks = []
        window_start = 0
        window_end = 0  # an inclusive index
        num_items = len(doc_chunk.meta.doc_items)
        while window_end < num_items:
            new_chunk = self._make_chunk_from_doc_items(
                doc_chunk=doc_chunk,
                window_start=window_start,
                window_end=window_end,
            )
            if self._count_chunk_tokens(doc_chunk=new_chunk) <= self.max_tokens:
                if window_end < num_items - 1:
                    window_end += 1
                    continue
                else:
                    window_end = num_items  # signalizing the last loop
            elif window_start == window_end:
                window_end += 1
                window_start = window_end
            else:
                new_chunk = self._make_chunk_from_doc_items(
                    doc_chunk=doc_chunk,
                    window_start=window_start,
                    window_end=window_end - 1,
                )
                window_start = window_end
            chunks.append(new_chunk)
        return chunks

    def _split_using_plain_text(self, doc_chunk: DocChunk) -> list[DocChunk]:
        lengths = self._doc_chunk_length(doc_chunk)
        if lengths.total_len <= self.max_tokens:
            return [doc_chunk]
        else:
            available_length = self.max_tokens - lengths.other_len
            sem_chunker = semchunk.chunkerify(
                self._tokenizer, chunk_size=available_length
            )
            if available_length <= 0:
                warnings.warn(
                    f"Headers and captions for this chunk are longer than the total amount of size for the chunk, chunk will be ignored: {doc_chunk.text=}"
                )
                return []
            text = doc_chunk.text
            segments = sem_chunker.chunk(text)
            chunks = [type(doc_chunk)(text=s, meta=doc_chunk.meta) for s in segments]
            return chunks

    def _merge_chunks_with_matching_metadata(self, chunks: list[DocChunk]):
        output_chunks = []
        window_start = 0
        window_end = 0  # an inclusive index
        num_chunks = len(chunks)

        while window_end < num_chunks:
            chunk = chunks[window_end]
            headings_and_captions = (chunk.meta.headings, chunk.meta.captions)
            ready_to_append = False

            if window_start == window_end:
                current_headings_and_captions = headings_and_captions
                window_end += 1
                first_chunk_of_window = chunk

            else:
                chks = chunks[window_start: window_end + 1]
                doc_items = [it for chk in chks for it in chk.meta.doc_items]
                candidate = DocChunk(
                    text=self.delim.join([chk.text for chk in chks]),
                    meta=DocMeta(
                        doc_items=doc_items,
                        headings=current_headings_and_captions[0],
                        captions=current_headings_and_captions[1],
                        origin=chunk.meta.origin,
                    ),
                )

                if (headings_and_captions == current_headings_and_captions
                        and self._count_chunk_tokens(doc_chunk=candidate) <= self.max_tokens
                ):
                    window_end += 1
                    new_chunk = candidate
                else:
                    ready_to_append = True

            if ready_to_append or window_end == num_chunks:
                if window_start + 1 == window_end:
                    output_chunks.append(first_chunk_of_window)
                else:
                    output_chunks.append(new_chunk)
                window_start = window_end

        return output_chunks

    def chunk(self, dl_doc: DoclingDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        r"""Chunk the provided document.
        Args:
            dl_doc (DLDocument): document to chunk
        Yields:
            Iterator[Chunk]: iterator over extracted chunks
        """
        res: Iterable[DocChunk]
        res = self._inner_chunker.chunk(dl_doc=dl_doc, **kwargs)  # type: ignore
        res = [x for c in res for x in self._split_by_doc_items(c)]
        res = [x for c in res for x in self._split_using_plain_text(c)]

        if self.merge_peers:
            res = self._merge_chunks_with_matching_metadata(res)
        return iter(res)
