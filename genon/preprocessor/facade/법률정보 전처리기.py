from __future__ import annotations

import json
import os
from pathlib import Path

# import fitz
from collections import defaultdict
from datetime import datetime
from typing import Optional, Iterable, Any, List, Dict, Tuple

# import fitz
from fastapi import Request
#from langchain.text_splitter import RecursiveCharacterTextSplitter
#from langchain_core.documents import Document
from pydantic import BaseModel

#from docling.backend.docling_parse_backend import DoclingParseDocumentBackend
from docling.backend.docling_parse_v2_backend import DoclingParseV2DocumentBackend
#from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
#from docling.datamodel.document import ConversionStatus
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    #EasyOcrOptions,
    #OcrEngine,
    #PdfBackend,
    PdfPipelineOptions,
    TableFormerMode,
)
# docling imports
from docling.document_converter import DocumentConverter, PdfFormatOption, HTMLFormatOption
from docling.datamodel.document import ConversionResult, InputDocument
from docling_core.transforms.chunker import HierarchicalChunker as OrgHierarchicalChunker
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker as OrgHybridChunker
from docling_core.transforms.chunker import (
    BaseChunk,
    BaseChunker,
    DocChunk,
    DocMeta,
)
from docling_core.types import DoclingDocument

from docling_core.types.doc import (
    BoundingBox,
    #CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    DocumentOrigin,
    GroupLabel,
    #ImageRef,
    #ProvenanceItem,
    #Size,
    #TableCell,
    #TableData,
    #GroupItem,

    DocItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    PageItem
)
from collections import Counter
import re
from docling.backend.html_backend import HTMLDocumentBackend
import subprocess

# ============================================

import warnings
from typing import Iterable, Iterator, Optional, Union

from pydantic import BaseModel, ConfigDict, PositiveInt, TypeAdapter, model_validator
from typing_extensions import Self

try:
    import semchunk
    from transformers import AutoTokenizer, PreTrainedTokenizerBase
except ImportError:
    raise RuntimeError(
        "Module requires 'chunking' extra; to install, run: "
        "`pip install 'docling-core[chunking]'`"
    )

#os.environ['HF_HOME'] = "/home/mnc/temp/.cache/huggingface"
####################################################
#################### 전처리 코드 #####################
####################################################

####################################################
#################### 전처리 코드 #####################
####################################################

#
# Copyright IBM Corp. 2024 - 2024
# SPDX-License-Identifier: MIT
#

"""Chunker implementation leveraging the document structure."""

from typing import Any, ClassVar, Final, Iterator, Literal, Optional

from pandas import DataFrame
from pydantic import Field, StringConstraints, field_validator

#from docling_core.transforms.chunker import BaseChunk, BaseChunker, BaseMeta
from docling_core.types import DoclingDocument as DLDocument
from docling_core.types.doc.document import (
    #DocItem,
    DocumentOrigin,
    LevelNumber,
    ListItem,
    #SectionHeaderItem,
    #TableItem,
    #TextItem,
)
from docling_core.types.doc.labels import DocItemLabel



class HierarchicalChunker(BaseChunker):
    r"""Chunker implementation leveraging the document layout.

    Args:
        merge_list_items (bool): Whether to merge successive list items.
            Defaults to True.
        delim (str): Delimiter to use for merging text. Defaults to "\n".
    """

    merge_list_items: bool = True
    delim: str = "\n"
    

    @classmethod
    def _triplet_serialize(cls, table_df: DataFrame) -> str:

        # copy header as first row and shift all rows by one
        table_df.loc[-1] = table_df.columns  # type: ignore[call-overload]
        table_df.index = table_df.index + 1
        table_df = table_df.sort_index()

        rows = [str(item).strip() for item in table_df.iloc[:, 0].to_list()]
        cols = [str(item).strip() for item in table_df.iloc[0, :].to_list()]

        nrows = table_df.shape[0]
        ncols = table_df.shape[1]
        texts = [
            f"{str(table_df.iloc[i, j]).strip()}"
            for i in range(0, nrows)
            for j in range(0, ncols)
        ]
        output_text = ". ".join(texts)

        return output_text

    def chunk(self, dl_doc: DLDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        r"""Chunk the provided document.

        Args:
            dl_doc (DLDocument): document to chunk

        Yields:
            Iterator[Chunk]: iterator over extracted chunks
        """
        heading_by_level: dict[LevelNumber, str] = {}
        list_items: list[TextItem] = []
        title_cnt = 1
        re_pattern_jang = r'^제\d{1,3}장.*'
        re_pattern_jeol = r'^제\d{1,3}절.*'
        re_pattern_jo = r'^제\d{1,3}조.*'

        
        iterate_items = [(item, level) for item, level in dl_doc.iterate_items()]
        if len(iterate_items) == 0:
            iterate_items = []
            for ref_item in dl_doc.body.children:
                cref = ref_item.cref
                parts = cref.split("/")
                kind = parts[-2]
                idx = int(parts[-1])

                if kind == "texts":
                    iterate_items.append((dl_doc.texts[idx], 0))
                elif kind == "tables":
                    iterate_items.append((dl_doc.tables[idx], 0))
                else:
                    print("@@@@ 무슨 타입이지", kind)
                


        # for item, level in dl_doc.iterate_items():
        for item, level in iterate_items:
            captions = None
            if isinstance(item, DocItem):

                # first handle any merging needed
                if self.merge_list_items:
                    if isinstance(
                        item, ListItem
                    ) or (  # TODO remove when all captured as ListItem:
                        isinstance(item, TextItem)
                        and item.label == DocItemLabel.LIST_ITEM
                    ):
                        list_items.append(item)
                        continue
                    elif list_items:  # need to yield
                        yield DocChunk(
                            text=self.delim.join([i.text for i in list_items]),
                            meta=DocMeta(
                                doc_items=list_items,
                                headings=[
                                    heading_by_level[k]
                                    for k in sorted(heading_by_level) if k != 4
                                ]
                                or None,
                                origin=dl_doc.origin,
                            ),
                        )
                        list_items = []  # reset

                if isinstance(item, SectionHeaderItem) or (
                    isinstance(item, TextItem)
                    and item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]
                ):
                    level = (
                        item.level
                        if isinstance(item, SectionHeaderItem)
                        else (0 if item.label == DocItemLabel.TITLE else 1)
                    )
                    heading_by_level[level] = item.text

                    # remove headings of higher level as they just went out of scope
                    keys_to_del = [k for k in heading_by_level if k > level]
                    for k in keys_to_del:
                        heading_by_level.pop(k, None)
                    continue

                if isinstance(item, TextItem) or (
                    (not self.merge_list_items) and isinstance(item, ListItem)
                ):
                    text = item.text
                elif isinstance(item, TableItem):
                    item_header = item.data.table_cells[0].text.strip()
                    if title_cnt == 1:
                        heading_by_level[1] = item_header
                        title_cnt += 1
                    else:
                        if re.match(re_pattern_jang, item_header):
                            heading_by_level[2] = item_header
                        elif re.match(re_pattern_jeol, item_header):
                            heading_by_level[3] = item_header
                        elif re.match(re_pattern_jo, item_header):
                            heading_by_level[4] = item_header
                    # if len(item.data.table_cells) == 1:
                    #     print(item_header)
                    #     print()
                    #     continue
                    table_df = item.export_to_dataframe()
                    if table_df.shape[0] < 1 or table_df.shape[1] < 1:
                        # at least two cols needed, as first column contains row headers
                        continue
                    text = self._triplet_serialize(table_df=table_df)
                    captions = [
                        c.text for c in [r.resolve(dl_doc) for r in item.captions]
                    ] or None
                elif isinstance(item, PictureItem):
                    text = ''.join(str(value) for key, value in heading_by_level.items() if key != 4)
                else:
                    continue
                c = DocChunk(
                    text=text,
                    meta=DocMeta(
                        doc_items=[item],
                        headings=[heading_by_level[k] for k in sorted(heading_by_level)]
                        or None,
                        captions=captions,
                        origin=dl_doc.origin,
                    ),
                )
                yield c

        if self.merge_list_items and list_items:  # need to yield
            yield DocChunk(
                text=self.delim.join([i.text for i in list_items]),
                meta=DocMeta(
                    doc_items=list_items,
                    headings=[heading_by_level[k] for k in sorted(heading_by_level)]
                    or None,
                    origin=dl_doc.origin,
                ),
            )

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

    tokenizer: Union[PreTrainedTokenizerBase, str] = (
        "sentence-transformers/all-MiniLM-L6-v2"
    )
    max_tokens: int = None  # type: ignore[assignment]
    merge_peers: bool = True

    _inner_chunker: HierarchicalChunker = HierarchicalChunker()

    @model_validator(mode="after")
    def _patch_tokenizer_and_max_tokens(self) -> Self:
        self._tokenizer = (
            self.tokenizer
            if isinstance(self.tokenizer, PreTrainedTokenizerBase)
            else AutoTokenizer.from_pretrained(self.tokenizer)
        )
        if self.max_tokens is None:
            self.max_tokens = TypeAdapter(PositiveInt).validate_python(
                self._tokenizer.model_max_length
            )
        return self

    def _count_tokens(self, text: Optional[Union[str, list[str]]]):
        if text is None:
            return 0
        elif isinstance(text, list):
            total = 0
            for t in text:
                total += self._count_tokens(t)
            return total
        return len(self._tokenizer.tokenize(text, max_length=None))

    class _ChunkLengthInfo(BaseModel):
        total_len: int
        text_len: int
        other_len: int

    def _doc_chunk_length(self, doc_chunk: DocChunk):
        text_length = self._count_tokens(doc_chunk.text)
        headings_length = self._count_tokens(doc_chunk.meta.headings)
        captions_length = self._count_tokens(doc_chunk.meta.captions)
        total = text_length + headings_length + captions_length
        return self._ChunkLengthInfo(
            total_len=total,
            text_len=text_length,
            other_len=total - text_length,
        )

    def _make_chunk_from_doc_items(
        self, doc_chunk: DocChunk, window_text: str, window_start: int, window_end: int
    ):
        meta = DocMeta(
            doc_items=doc_chunk.meta.doc_items[window_start : window_end + 1],
            headings=doc_chunk.meta.headings,
            captions=doc_chunk.meta.captions,
            origin=doc_chunk.meta.origin,
        )
        new_chunk = DocChunk(text=window_text, meta=meta)
        return new_chunk

    def _merge_text(self, t1, t2):
        if t1 == "":
            return t2
        elif t2 == "":
            return t1
        else:
            return f"{t1}{self.delim}{t2}"

    def _split_by_doc_items(self, doc_chunk: DocChunk) -> list[DocChunk]:
        if doc_chunk.meta.doc_items is None or len(doc_chunk.meta.doc_items) <= 1:
            return [doc_chunk]
        length = self._doc_chunk_length(doc_chunk)
        if length.total_len <= self.max_tokens:
            return [doc_chunk]
        else:
            chunks = []
            window_start = 0
            window_end = 0
            window_text = ""
            window_text_length = 0
            other_length = length.other_len
            num_items = len(doc_chunk.meta.doc_items)
            while window_end < num_items:
                doc_item = doc_chunk.meta.doc_items[window_end]
                if isinstance(doc_item, TextItem):
                    text = doc_item.text
                else:
                    raise RuntimeError("Non-TextItem split not implemented yet")
                text_length = self._count_tokens(text)
                if (
                    text_length + window_text_length + other_length < self.max_tokens
                    and window_end < num_items - 1
                ):
                    # Still room left to add more to this chunk AND still at least one
                    # item left
                    window_end += 1
                    window_text_length += text_length
                    window_text = self._merge_text(window_text, text)
                elif text_length + window_text_length + other_length < self.max_tokens:
                    # All the items in the window fit into the chunk and there are no
                    # other items left
                    window_text = self._merge_text(window_text, text)
                    new_chunk = self._make_chunk_from_doc_items(
                        doc_chunk, window_text, window_start, window_end
                    )
                    chunks.append(new_chunk)
                    window_end = num_items
                elif window_start == window_end:
                    # Only one item in the window and it doesn't fit into the chunk. So
                    # we'll just make it a chunk for now and it will get split in the
                    # plain text splitter.
                    window_text = self._merge_text(window_text, text)
                    new_chunk = self._make_chunk_from_doc_items(
                        doc_chunk, window_text, window_start, window_end
                    )
                    chunks.append(new_chunk)
                    window_start = window_end + 1
                    window_end = window_start
                    window_text = ""
                    window_text_length = 0
                else:
                    # Multiple items in the window but they don't fit into the chunk.
                    # However, the existing items must have fit or we wouldn't have
                    # gotten here. So we put everything but the last item into the chunk
                    # and then start a new window INCLUDING the current window end.
                    new_chunk = self._make_chunk_from_doc_items(
                        doc_chunk, window_text, window_start, window_end - 1
                    )
                    chunks.append(new_chunk)
                    window_start = window_end
                    window_text = ""
                    window_text_length = 0
            return chunks

    def _split_using_plain_text(
        self,
        doc_chunk: DocChunk,
    ) -> list[DocChunk]:
        lengths = self._doc_chunk_length(doc_chunk)
        if lengths.total_len <= self.max_tokens:
            # data = [DocChunk(**doc_chunk.export_json_dict())]
            data = [DocChunk(text=doc_chunk.text, meta=doc_chunk.meta)]
            return data
        else:
            # How much room is there for text after subtracting out the headers and
            # captions:
            available_length = self.max_tokens - lengths.other_len
            sem_chunker = semchunk.chunkerify(
                self._tokenizer, chunk_size=available_length
            )
            if available_length <= 0:
                warnings.warn(
                    f"Headers and captions for this chunk are longer than the total amount of size for the chunk, chunk will be ignored: {doc_chunk.text=}"  # noqa
                )
                return []
            text = doc_chunk.text
            segments = sem_chunker.chunk(text)
            chunks = [DocChunk(text=s, meta=doc_chunk.meta) for s in segments]
            return chunks

    def _merge_chunks_with_matching_metadata(self, chunks: list[DocChunk]):
        output_chunks = []
        window_start = 0
        window_end = 0
        num_chunks = len(chunks)
        while window_end < num_chunks:
            chunk = chunks[window_end]
            lengths = self._doc_chunk_length(chunk)
            headings_and_captions = (chunk.meta.headings, chunk.meta.captions)
            ready_to_append = False
            if window_start == window_end:
                # starting a new block of chunks to potentially merge
                current_headings_and_captions = headings_and_captions
                window_text = chunk.text
                window_other_length = lengths.other_len
                window_text_length = lengths.text_len
                window_items = chunk.meta.doc_items
                window_end += 1
                first_chunk_of_window = chunk
            elif (
                headings_and_captions == current_headings_and_captions
                and window_text_length + window_other_length + lengths.text_len
                <= self.max_tokens
            ):
                # there is room to include the new chunk so add it to the window and
                # continue
                window_text = self._merge_text(window_text, chunk.text)
                window_text_length += lengths.text_len
                window_items = window_items + chunk.meta.doc_items
                window_end += 1
            else:
                ready_to_append = True

            if ready_to_append or window_end == num_chunks:
                # no more room OR the start of new metadata.  Either way, end the block
                # and use the current window_end as the start of a new block
                if window_start + 1 == window_end:
                    # just one chunk so use it as is
                    output_chunks.append(first_chunk_of_window)
                else:
                    new_meta = DocMeta(
                        doc_items=window_items,
                        headings=current_headings_and_captions[0],
                        captions=current_headings_and_captions[1],
                        origin=chunk.meta.origin,
                    )
                    new_chunk = DocChunk(
                        text=window_text,
                        meta=new_meta,
                    )
                    output_chunks.append(new_chunk)
                # no need to reset window_text, etc. because that will be reset in the
                # next iteration in the if window_start == window_end block
                window_start = window_end

        return output_chunks

    def chunk(self, dl_doc: DoclingDocument, **kwargs) -> Iterator[BaseChunk]:
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


class GenOSVectorMeta(BaseModel):
    class Config:
        extra = 'allow'

    text: str = None
    n_char: int = None
    n_word: int = None
    n_line: int = None
    i_page: int = None
    i_chunk_on_page: int = None
    n_chunk_of_page: int = None
    i_chunk_on_doc: int = None
    n_chunk_of_doc: int = None
    n_page: int = None
    reg_date: str = None
    bboxes: str = None
    doc_items: list = None

class GenOSVectorMetaBuilder:
    def __init__(self):
        """빌더 초기화"""
        self.text: Optional[str] = None
        self.n_char: Optional[int] = None
        self.n_word: Optional[int] = None
        self.n_line: Optional[int] = None
        self.i_page: Optional[int] = None
        self.i_chunk_on_page: Optional[int] = None
        self.n_chunk_of_page: Optional[int] = None
        self.i_chunk_on_doc: Optional[int] = None
        self.n_chunk_of_doc: Optional[int] = None
        self.n_page: Optional[int] = None
        self.reg_date: Optional[str] = None
        self.bboxes: str = None
        self.doc_items = []
        self.doc_items: list = None
        self.args: str = None
        self.url: str = None
        self.title: str = None
        self.chapter: str = None
        self.section: str = None
        self.article: str = None

    def set_text(self, text: str) -> "GenOSVectorMetaBuilder":
        """텍스트와 관련된 데이터를 설정"""
        print(f"@@@@ [Error] self.title: {self.title}")
        print(f"@@@@ [Error] text: {text}")
        self.text = self.title + text

        self.n_char = len(text)
        self.n_word = len(text.split())
        self.n_line = len(text.splitlines())
        return self

    def set_page_info(
            self, i_page: int, i_chunk_on_page: int, n_chunk_of_page: int
    ) -> "GenOSVectorMetaBuilder":
        """페이지 정보 설정"""
        self.i_page = i_page
        self.i_chunk_on_page = i_chunk_on_page
        self.n_chunk_of_page = n_chunk_of_page
        return self

    def set_chunk_index(self, i_chunk_on_doc: int) -> "GenOSVectorMetaBuilder":
        """문서 전체의 청크 인덱스 설정"""
        self.i_chunk_on_doc = i_chunk_on_doc
        return self

    def set_bboxes(self, bbox: BoundingBox) -> "GenOSVectorMetaBuilder":
        """Bounding Boxes 정보 설정"""
        #         bboxes.append({
        #             'p1': {'x': rect[0] / fitz_page.rect.width, 'y': rect[1] / fitz_page.rect.height},
        #             'p2': {'x': rect[2] / fitz_page.rect.width, 'y': rect[3] / fitz_page.rect.height},
        #         })
        # NOTE: docling은 BOTTOMLEFT인데 해당 좌표 그대로 활용되는지 ?
        conv = []
        if bbox != []:
            conv.append({
                'p1': {'x': bbox.l, 'y': bbox.t},
                'p2': {'x': bbox.r, 'y': bbox.b},
            })
        else:
            # conv.append({
            #     'p1': {'x': 0, 'y': 0},
            #     'p2': {'x': 0, 'y': 0},
            # })
            conv.append({})
        self.bboxes = json.dumps(conv)
        return self

    def set_global_metadata(self, **global_metadata) -> "GenOSVectorMetaBuilder":
        """글로벌 메타데이터 병합"""
        for key, value in global_metadata.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self

    def set_doc_items(self, doc_items: list) -> "GenOSVectorMetaBuilder":
        self.doc_items = doc_items
        return self
    
    def set_args(self, args: str) -> "GenOSVectorMetaBuilder":
        self.args = args
        return self

    def set_doc_url(self, url: str) -> "GenOSVectorMetaBuilder":
        self.url = url
        return self

    def set_doc_headings(self, headings: list) -> "GenOSVectorMetaBuilder":
        re_pattern_jang = r'^제\d{1,3}장.*'
        re_pattern_jeol = r'^제\d{1,3}절.*'
        re_pattern_jo = r'^제\d{1,3}조.*'
        if headings:
            for h in headings:
                if re.match(re_pattern_jang, h):
                    self.chapter = h
                elif re.match(re_pattern_jeol, h):
                    self.section = h
                elif re.match(re_pattern_jo, h):
                    if ")" in h:
                        self.article = h[:h.find(")") + 1]
                    else:
                        self.article = h[:h.find("조") + 1]
                elif h != "":
                    self.title = h
        return self

    def build(self) -> GenOSVectorMeta:
        """설정된 데이터를 사용해 최종적으로 GenOSVectorMeta 객체 생성"""
        return GenOSVectorMeta(
            text=self.text,
            n_char=self.n_char,
            n_word=self.n_word,
            n_line=self.n_line,
            i_page=self.i_page,
            i_chunk_on_page=self.i_chunk_on_page,
            n_chunk_of_page=self.n_chunk_of_page,
            i_chunk_on_doc=self.i_chunk_on_doc,
            n_chunk_of_doc=self.n_chunk_of_doc,
            n_page=self.n_page,
            reg_date=self.reg_date,
            bboxes=self.bboxes,
            #doc_items=self.doc_items,
            #args=self.args,
            doc_url=self.url,
            title=self.title,
            chapter=self.chapter,
            section=self.section,
            article=self.article,
        )


class DocumentProcessor:

    def __init__(self):
        '''
        initialize Document Converter
        '''
        self.page_chunk_counts = defaultdict(int)
        device = AcceleratorDevice.AUTO
        num_threads = 4
        table_mode = TableFormerMode.FAST
        accelerator_options = AcceleratorOptions(num_threads=num_threads, device=device)
        pipe_line_options = PdfPipelineOptions()
        pipe_line_options.generate_page_images = True
        # pipe_line_options.generate_table_images = True
        pipe_line_options.generate_picture_images = True
        pipe_line_options.do_ocr = True
        pipe_line_options.ocr_options.lang = ["ko", 'en']
        pipe_line_options.ocr_options.model_storage_directory = "/nfs-root/aiModel/.EasyOCR/model"
        #pipe_line_options.ocr_options.model_storage_directory = "/home/mnc/temp/.EasyOCR/model"
        #pipe_line_options.artifacts_path = Path("/home/mnc/temp/.cache/huggingface/hub/models--ds4sd--docling-models/snapshots/36bebf56681740529abd09f5473a93a69373fbf0/")
        pipe_line_options.do_table_structure = True
        pipe_line_options.images_scale = 2
        pipe_line_options.table_structure_options.do_cell_matching = True
        pipe_line_options.accelerator_options = accelerator_options

        self.converter = DocumentConverter(
            format_options={
                InputFormat.HTML: HTMLFormatOption(
                    # pipeline_options=pipe_line_options,
                    # backend=DoclingParseV2DocumentBackend
                )
            }
        )

    def load_documents_with_docling(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        # docling 설정
        # 필요에 따라 사용자 지정 가능. 여기서는 genos_vanilla 와 비슷하게 PDF를 처리한다 가정.
        # TODO: kwargs 와의 연결
        # TODO: Langchain document 를 꼭 써야하나?
        # 실제 변환 실행
        # ConversionResult 리스트를 받는다.
        #
        # NOTE: 처리시 파일 하나를 병렬로 처리하는지?? 아니면 폴더 단위로 병렬 처리 하는지??
        # NOTE: 파일 하나 처리 시 convert로 변경.
        # conv_results = doc_converter.convert_all([file_path], raises_on_error=True)
        conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)
        return conv_result.document

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        # ducling 방식으로 문서 로드
        return self.load_documents_with_docling(file_path, **kwargs)
        # return documents

    def split_documents(self, documents: DoclingDocument, **kwargs: dict) -> List[DocChunk]:
        ##FIXME: Hierarchical Chunker 로 수정
        ## NOTE: HierarchicalChunker -> HybridChunker로 공식 문서 변경 됨.
        ## 관련 url : https://ds4sd.github.io/docling/usage/#chunking
        ## 관련 url : https://ds4sd.github.io/docling/examples/hybrid_chunking/
        #chunker: HybridChunker = HybridChunker()
        # TODO: 전처리 코드 넣기

        
        # TODO: 최종 전처리 document로 chunk 추출
        chunker: HierarchicalChunker = HierarchicalChunker()
        chunks: List[DocChunk] = list(chunker.chunk(dl_doc=documents, **kwargs))

        
        # TODO: 페이지 관련 이슈 처리 시 해당 부분도 수정 필요
        for chunk in chunks:
            #self.page_chunk_counts[chunk.meta.doc_items[0].prov[0].page_no] += 1
            self.page_chunk_counts[1] += 1
        return chunks

    def safe_join(self, iterable):
        if not isinstance(iterable, (list, tuple, set)):
            return ''
        return ''.join(map(str, iterable)) + '\n'

    def compose_vectors(self, document: DoclingDocument, chunks: List[DocChunk], file_path: str, **kwargs: dict) -> \
            list[dict]:
        pdf_path = file_path.replace('.hwp', '.pdf').replace('.txt', '.pdf').replace('.json', '.pdf')

        # if os.path.exists(pdf_path):
        #     doc = fitz.open(pdf_path)

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z'
        )

        current_page = None
        chunk_index_on_page = 0
        vectors = []
        doc_url = ""

        for chunk_idx, chunk in enumerate(chunks):
            ## NOTE: chunk가 두 페이지에 걸쳐 있는 경우 첫번째 아이템을 사용
            ## NOTE: chunk가 두 페이지에 걸쳐서 있는 경우 bounding box 처리를 어떻게 해야하는 지...
            ## NOTE: 현재 구조에서는 처리가 불가
            ## NOTE: 임시로 페이지 넘어가는 경우 chunk를 분할해서 처리
            # chunk_page = chunk.meta.doc_items[0].prov[0].page_no
            chunk_page = 1
            if chunk_idx == 0 and chunk.text.startswith("http://"):
                doc_url = chunk.text
                global_metadata["url"] = chunk.text
                continue
            #content = self.safe_join(chunk.meta.headings) + chunk.text
            
            
            # @@@@ 성민: 임베딩 모델 맥스렝스 넘어서 터진다.
            new_chunks = []
            cur_chunk = ""
            split_texts = chunk.text.split(" ")
            for split_text in split_texts:
                if len(cur_chunk) + len(split_text) > 2000:
                    new_chunks.append(cur_chunk)
                    cur_chunk = split_text
                else:
                    cur_chunk = " ".join([cur_chunk, split_text])
            
            if cur_chunk:
                new_chunks.append(cur_chunk)
            
            for new_chunk in new_chunks:
                vector = (GenOSVectorMetaBuilder()
                      .set_doc_headings(chunk.meta.headings)
                      .set_text(new_chunk)
                      .set_page_info(chunk_page, chunk_index_on_page, self.page_chunk_counts[chunk_page])
                      .set_chunk_index(chunk_idx)
                    #   .set_bboxes(chunk.meta.doc_items[0].prov[0].bbox)
                      .set_bboxes([])
                      .set_global_metadata(**global_metadata)
                      #.set_doc_items(chunk.meta.doc_items)
                      #.set_args(json.dumps(kwargs))
                      .set_doc_url(doc_url)
                      
                      ).build()
                vectors.append(vector)

            # @@@@ 성민: 아래 주석하고 위로
            # content = chunk.text
            # vector = (GenOSVectorMetaBuilder()
            #           .set_doc_headings(chunk.meta.headings)
            #           .set_text(content)
            #           .set_page_info(chunk_page, chunk_index_on_page, self.page_chunk_counts[chunk_page])
            #           .set_chunk_index(chunk_idx)
            #         #   .set_bboxes(chunk.meta.doc_items[0].prov[0].bbox)
            #           .set_bboxes([])
            #           .set_global_metadata(**global_metadata)
            #           #.set_doc_items(chunk.meta.doc_items)
            #           #.set_args(json.dumps(kwargs))
            #           .set_doc_url(doc_url)
                      
            #           ).build()
            # vectors.append(vector)

            # page = chunk_page
            # text = chunk.page_content

            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0

            # bboxes_json = None
            # if os.path.exists(pdf_path):
            #     fitz_page = doc.load_page(page)
            #     bboxes = []
            #     # text 검색 시 fitz의 search_for 문맥이 주어진 text chunk 에 매칭되는 바운딩박스를 찾을 수 있는지 확인
            #     # 많은 경우 chunk가 PDF 내 같은 text를 그대로 match하지 못할 수 있음.
            #     # 여기서는 원본 genos_vanilla와 동일한 로직 유지.
            #     # 특정 성능 문제나 결과 없을 경우 try-except 추가 가능.
            #     search_results = fitz_page.search_for(text)
            #     for rect in search_results:
            #         bboxes.append({
            #             'p1': {'x': rect[0] / fitz_page.rect.width, 'y': rect[1] / fitz_page.rect.height},
            #             'p2': {'x': rect[2] / fitz_page.rect.width, 'y': rect[3] / fitz_page.rect.height},
            #         })
            #     bboxes_json = json.dumps(bboxes)

            chunk_index_on_page += 1

        return vectors

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        # in_doc = InputDocument(
        #     path_or_stream=file_path,
        #     format=InputFormat.HTML,
        #     backend=HTMLDocumentBackend,
        # )
        # backend = HTMLDocumentBackend(
        #     in_doc=in_doc,
        #     path_or_stream=file_path,
        # )
        # document = backend.convert()

        # old_file = file_path
        # file_path = file_path[:-4] + ".html"
        # subprocess.run(["mv", old_file, file_path])
        #subprocess.run(["touch", old_file+".testfile.txt"])
        code = """
from bs4 import BeautifulSoup
import sys
file = sys.argv[1]
with open(file, "r", encoding="utf-8") as f:
    html_content = f.read()
soup = BeautifulSoup(html_content, "html.parser")
first_script = soup.find('script')
if first_script:
    first_script.decompose()
if not soup.body:
    sys.exit()
original_table = soup.find("table", {"align":"center"})
if not original_table:
    sys.exit()
new_tables = []
trs = original_table.find_all("tr")
for tr in trs:
    if tr.find("span", {"class":"titleText"}):
        currnet_table = soup.new_tag("table")
        new_tables.append(currnet_table)
    currnet_table.append(tr.extract())
for new_table in new_tables:
    original_table.insert_before(new_table)
original_table.decompose()
with open(file, "w", encoding="utf-8") as f:
    f.write(str(soup))
        """
        subprocess.run(["python3", "-c", code, file_path])
        document: DoclingDocument = self.load_documents(file_path, **kwargs)
        # await assert_cancelled(request)
        # Extract Chunk from DoclingDocument
        chunks: List[DocChunk] = self.split_documents(document, **kwargs)
        await assert_cancelled(request)

        vectors = []
        if len(chunks) > 1:
            vectors: list[dict] = self.compose_vectors(document, chunks, file_path, **kwargs)
        else:
            raise GenosServiceException(1, f"chunk length is 0")
        
        print("@@@@", [len(vector.text) for vector in vectors])

        return vectors


class GenosServiceException(Exception):
    # GenOS 와의 의존성 부분 제거를 위해 추가
    def __init__(self, error_code: str, error_msg: Optional[str] = None, msg_params: Optional[dict] = None) -> None:
        self.code = 1
        self.error_code = error_code
        self.error_msg = error_msg or "GenOS Service Exception"
        self.msg_params = msg_params or {}

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        return f"{class_name}(code={self.code!r}, errMsg={self.error_msg!r})"


# GenOS 와의 의존성 제거를 위해 추가
async def assert_cancelled(request: Request):
    if await request.is_disconnected():
        raise GenosServiceException(1, f"Cancelled")
