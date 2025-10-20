from __future__ import annotations

from collections import defaultdict
import warnings
import asyncio
import fitz
import subprocess
import sys
import tempfile
import unicodedata
import shutil
import json
import os
from datetime import datetime
from fastapi import Request
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import (
    PyMuPDFLoader,  # PDF
    UnstructuredWordDocumentLoader,  # DOC 
)
from langchain_core.documents import Document
from pathlib import Path
from pydantic import BaseModel, ConfigDict, PositiveInt, TypeAdapter, model_validator
from typing import Any, Iterable, Iterator, List, Optional, Union
from typing_extensions import Self

try:
    import semchunk
    from transformers import AutoTokenizer, PreTrainedTokenizerBase
except ImportError:
    raise RuntimeError(
        "Module requires 'chunking' extra; to install, run: "
        "`pip install 'docling-core[chunking]'`"
    )

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PipelineOptions
from docling.datamodel.document import ConversionResult
from docling.pipeline.simple_pipeline import SimplePipeline
from docling.document_converter import DocumentConverter, WordFormatOption
from docling_core.transforms.chunker import BaseChunk, BaseChunker, DocChunk, DocMeta
from docling_core.types import DoclingDocument as DLDocument
from docling_core.types.doc import (
    DocItem, DocItemLabel, DoclingDocument,
    PictureItem, SectionHeaderItem, TableItem, TextItem
)
from docling_core.types.doc.document import LevelNumber, ListItem, CodeItem
from docling.backend.genos_msword_backend import GenosMsWordDocumentBackend
from utils import assert_cancelled
from genos_utils import upload_files

import logging

for n in ("fontTools", "fontTools.ttLib", "fontTools.ttLib.ttFont"):
    lg = logging.getLogger(n)
    lg.setLevel(logging.CRITICAL)
    lg.propagate = False
    logging.getLogger().setLevel(logging.WARNING)        


def convert_to_pdf(file_path: str) -> str | None:
    """
    LibreOffice로 PDF 변환을 시도한다.
    실패해도 예외를 던지지 않고 None을 반환한다.
    """
    try:
        in_path = Path(file_path).resolve()
        out_dir = in_path.parent
        pdf_path = in_path.with_suffix('.pdf')

        # headless에서 UTF-8 locale 보장
        env = os.environ.copy()
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", "C.UTF-8")

        # 확장자에 따라 필터(특히 .ppt는 impress 필터)
        
        convert_arg = "pdf:writer_pdf_Export"


        # 비ASCII 파일명 이슈 대비 임시 ASCII 파일명 복사본 시도
        try:
            in_path.name.encode('ascii')
            candidates = [in_path]
            tmp_dir = None
        except UnicodeEncodeError:
            tmp_dir = Path(tempfile.mkdtemp())
            ascii_name = unicodedata.normalize('NFKD', in_path.stem).encode('ascii', 'ignore').decode('ascii') or "file"
            ascii_copy = tmp_dir / f"{ascii_name}{in_path.suffix}"
            shutil.copy2(in_path, ascii_copy)
            candidates = [ascii_copy, in_path]

        for cand in candidates:
            cmd = [
                "soffice", "--headless",
                "--convert-to", convert_arg,
                "--outdir", str(out_dir),
                str(cand)
            ]
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
            if proc.returncode == 0 and pdf_path.exists():
                # 성공
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                return pdf_path
            # 실패해도 계속 시도 (로그만 찍고 무시)
            print(f"[convert_to_pdf] stderr: {proc.stderr.strip()}")

        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return None
    except Exception as e:
        # 어떤 에러든 삼키고 None 반환
        print(f"[convert_to_pdf] error: {e}")
        return None


def install_packages(packages):
    for package in packages:
        try:
            __import__(package)
        except ImportError:
            print(f"[!] {package} 패키지가 없습니다. 설치를 시도합니다.")
            subprocess.run([sys.executable, "-m", "pip", "install", package], check=True)


class GenOSVectorMeta(BaseModel):
    class Config:
        extra = 'allow'

    text: str | None = None
    n_char: int | None = None
    n_word: int | None = None
    n_line: int | None = None
    i_page: int | None = None
    e_page: int | None = None
    i_chunk_on_page: int | None = None
    n_chunk_of_page: int | None = None
    i_chunk_on_doc: int | None = None
    n_chunk_of_doc: int | None = None
    n_page: int | None = None
    reg_date: str | None = None
    chunk_bboxes: str | None = None
    media_files: str | None = None


class GenOSVectorMetaBuilder:
    def __init__(self):
        """빌더 초기화"""
        self.text: Optional[str] = None
        self.n_char: Optional[int] = None
        self.n_word: Optional[int] = None
        self.n_line: Optional[int] = None
        self.i_page: Optional[int] = None
        self.e_page: Optional[int] = None
        self.i_chunk_on_page: Optional[int] = None
        self.n_chunk_of_page: Optional[int] = None
        self.i_chunk_on_doc: Optional[int] = None
        self.n_chunk_of_doc: Optional[int] = None
        self.n_page: Optional[int] = None
        self.reg_date: Optional[str] = None
        self.chunk_bboxes: Optional[str] = None
        self.media_files: Optional[str] = None

    def set_text(self, text: str) -> "GenOSVectorMetaBuilder":
        """텍스트와 관련된 데이터를 설정"""
        self.text = text
        self.n_char = len(text)
        self.n_word = len(text.split())
        self.n_line = len(text.splitlines())
        return self

    def set_page_info(self, i_page: int, i_chunk_on_page: int, n_chunk_of_page: int) -> "GenOSVectorMetaBuilder":
        """페이지 정보 설정"""
        self.i_page = i_page
        self.i_chunk_on_page = i_chunk_on_page
        self.n_chunk_of_page = n_chunk_of_page
        return self

    def set_chunk_index(self, i_chunk_on_doc: int) -> "GenOSVectorMetaBuilder":
        """문서 전체의 청크 인덱스 설정"""
        self.i_chunk_on_doc = i_chunk_on_doc
        return self

    def set_global_metadata(self, **global_metadata) -> "GenOSVectorMetaBuilder":
        """글로벌 메타데이터 병합"""
        for key, value in global_metadata.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self

    def set_chunk_bboxes(self, doc_items: list, document: DoclingDocument) -> "GenOSVectorMetaBuilder":
        chunk_bboxes = []
        for item in doc_items:
            for prov in item.prov:
                label = item.self_ref
                type_ = item.label
                size = document.pages.get(prov.page_no).size
                page_no = prov.page_no
                bbox = prov.bbox
                bbox_data = {
                    'l': bbox.l / size.width,
                    't': bbox.t / size.height,
                    'r': bbox.r / size.width,
                    'b': bbox.b / size.height,
                    'coord_origin': bbox.coord_origin.value
                }
                chunk_bboxes.append({
                    'page': page_no,
                    'bbox': bbox_data,
                    'type': type_,
                    'ref': label
                })
        self.e_page = max([bbox['page'] for bbox in chunk_bboxes]) if chunk_bboxes else None
        self.chunk_bboxes = json.dumps(chunk_bboxes)
        return self

    def set_media_files(self, doc_items: list) -> "GenOSVectorMetaBuilder":
        temp_list = []
        if not doc_items:
            self.media_files = ""
            return self
        for item in doc_items:
            if isinstance(item, PictureItem):
                path = str(item.image.uri)
                name = path.rsplit("/", 1)[-1]
                temp_list.append({'name': name, 'type': 'image', 'ref': item.self_ref})
        self.media_files = json.dumps(temp_list)
        return self

    def build(self) -> GenOSVectorMeta:
        """설정된 데이터를 사용해 최종적으로 GenOSVectorMeta 객체 생성"""
        return GenOSVectorMeta(
            text=self.text,
            n_char=self.n_char,
            n_word=self.n_word,
            n_line=self.n_line,
            i_page=self.i_page,
            e_page=self.e_page,
            i_chunk_on_page=self.i_chunk_on_page,
            n_chunk_of_page=self.n_chunk_of_page,
            i_chunk_on_doc=self.i_chunk_on_doc,
            n_chunk_of_doc=self.n_chunk_of_doc,
            n_page=self.n_page,
            reg_date=self.reg_date,
            chunk_bboxes=self.chunk_bboxes,
            media_files=self.media_files,
        )


class HierarchicalChunker(BaseChunker):
    r""" Chunker implementation leveraging the document layout.
    Args:
        merge_list_items (bool): Whether to merge successive list items.
            Defaults to True.
        delim (str): Delimiter to use for merging text. Defaults to "\n".
    """
    merge_list_items: bool = True

    def chunk(self, dl_doc: DLDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        r"""Chunk the provided document.
        Args:
            dl_doc (DLDocument): document to chunk

        Yields:
            Iterator[Chunk]: iterator over extracted chunks
        """
        heading_by_level: dict[LevelNumber, str] = {}
        list_items: list[TextItem] = []
        for item, level in dl_doc.iterate_items():
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
                                headings=[heading_by_level[k] for k in sorted(heading_by_level)] or None,
                                origin=dl_doc.origin,
                            ),
                        )
                        list_items = []  # reset

                if isinstance(item, SectionHeaderItem) or (
                        isinstance(item, TextItem) and item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]):
                    level = (
                        item.level
                        if isinstance(item, SectionHeaderItem)
                        else (0 if item.label == DocItemLabel.TITLE else 1)
                    )
                    heading_by_level[level] = item.text
                    text = ''.join(str(value) for value in heading_by_level.values())

                    # remove headings of higher level as they just went out of scope
                    keys_to_del = [k for k in heading_by_level if k > level]
                    for k in keys_to_del:
                        heading_by_level.pop(k, None)
                    c = DocChunk(
                        text=text,
                        meta=DocMeta(
                            doc_items=[item],
                            headings=[heading_by_level[k] for k in sorted(heading_by_level)] or None,
                            captions=captions,
                            origin=dl_doc.origin
                        ),
                    )
                    yield c
                    continue

                if isinstance(item, TextItem) or (
                        (not self.merge_list_items) and isinstance(item, ListItem)) or isinstance(item, CodeItem):
                    text = item.text

                elif isinstance(item, TableItem):
                    text = item.export_to_markdown(dl_doc)
                    captions = [c.text for c in [r.resolve(dl_doc) for r in item.captions]] or None

                elif isinstance(item, PictureItem):
                    text = ''.join(str(value) for value in heading_by_level.values())
                else:
                    continue
                c = DocChunk(
                    text=text,
                    meta=DocMeta(
                        doc_items=[item],
                        headings=[heading_by_level[k] for k in sorted(heading_by_level)] or None,
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
                    headings=[heading_by_level[k] for k in sorted(heading_by_level)] or None,
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
        "/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2"
    )
    max_tokens: int = int(1e30)  # type: ignore[assignment]
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
                    window_end = num_items
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
                    # noqa
                )
                return []
            text = doc_chunk.text
            segments = sem_chunker.chunk(text)
            chunks = [type(doc_chunk)(text=s, meta=doc_chunk.meta) for s in segments]
            return chunks

    def _merge_chunks_with_matching_metadata(self, chunks: list[DocChunk]):
        output_chunks = []
        window_start = 0
        window_end = 0
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


class DocxProcessor:
    def __init__(self):
        self.page_chunk_counts = defaultdict(int)
        self.pipeline_options = PipelineOptions()
        self.converter = DocumentConverter(
            format_options={
                InputFormat.DOCX: WordFormatOption(
                pipeline_cls=SimplePipeline, backend=GenosMsWordDocumentBackend
                ),
            }
        )

    def get_paths(self, file_path: str):
        output_path, output_file = os.path.split(file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(f"{output_path}/{filename}")
        if artifacts_dir.is_absolute():
            reference_path = None
        else:
            reference_path = artifacts_dir.parent
        return artifacts_dir, reference_path

    def get_media_files(self, doc_items: list):
        temp_list = []
        for item in doc_items:
            if isinstance(item, PictureItem):
                path = str(item.image.uri)
                name = path.rsplit("/", 1)[-1]
                temp_list.append({'path': path, 'name': name})
        return temp_list

    def safe_join(self, iterable):
        if not isinstance(iterable, (list, tuple, set)):
            return ''
        return ''.join(map(str, iterable)) + '\n'

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)
        return conv_result.document 

    def split_documents(self, documents: DoclingDocument, **kwargs: dict) -> List[DocChunk]:
        chunker = HybridChunker(max_tokens=int(1e30), merge_peers=True)
        chunks: List[DocChunk] = list(chunker.chunk(dl_doc=documents, **kwargs))
        for chunk in chunks:
            self.page_chunk_counts[chunk.meta.doc_items[0].prov[0].page_no] += 1
        return chunks

    async def compose_vectors(self, document: DoclingDocument, chunks: List[DocChunk], file_path: str, request: Request,
                              **kwargs: dict) -> list[dict]:
        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
        )

        current_page = None
        chunk_index_on_page = 0
        vectors = []
        upload_tasks = []
        for chunk_idx, chunk in enumerate(chunks):
            chunk_page = chunk.meta.doc_items[0].prov[0].page_no
            content = self.safe_join(chunk.meta.headings) + chunk.text

            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0

            vector = (GenOSVectorMetaBuilder()
                      .set_text(content)
                      .set_page_info(chunk_page, chunk_index_on_page, self.page_chunk_counts[chunk_page])
                      .set_chunk_index(chunk_idx)
                      .set_global_metadata(**global_metadata)
                      .set_chunk_bboxes(chunk.meta.doc_items, document)
                      .set_media_files(chunk.meta.doc_items)
                      ).build()
            vectors.append(vector)

            chunk_index_on_page += 1
            file_list = self.get_media_files(chunk.meta.doc_items)
            upload_tasks.append(asyncio.create_task(
                upload_files(file_list, request=request)
            ))

        if upload_tasks:
            await asyncio.gather(*upload_tasks)
        return vectors

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        document: DoclingDocument = self.load_documents(file_path, **kwargs)
        artifacts_dir, reference_path = self.get_paths(file_path)
        document = document._with_pictures_refs(image_dir=artifacts_dir, reference_path=reference_path)

        chunks: list[DocChunk] = self.split_documents(document, **kwargs)

        vectors = []
        if len(chunks) >= 1:
            vectors: list[dict] = await self.compose_vectors(document, chunks, file_path, request, **kwargs)
        else:
            raise GenosServiceException("1", f"chunk length is 0")
        return vectors


class GenosServiceException(Exception):
    """GenOS 와의 의존성 부분 제거를 위해 추가"""

    def __init__(self, error_code: str, error_msg: Optional[str] = None, msg_params: Optional[dict] = None) -> None:
        self.code = 1
        self.error_code = error_code
        self.error_msg = error_msg or "GenOS Service Exception"
        self.msg_params = msg_params or {}

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        return f"{class_name}(code={self.code!r}, errMsg={self.error_msg!r})"


class DocumentProcessor:
    def __init__(self):
        self.page_chunk_counts = defaultdict(int)
        self.docx_processor = DocxProcessor()

    def get_loader(self, file_path: str):
        ext = os.path.splitext(file_path)[-1].lower()
        
        if ext == '.pdf':
            return PyMuPDFLoader(file_path)
        elif ext == '.doc': # docx는 위에서 먼저 처리됨 
            return UnstructuredWordDocumentLoader(file_path)
        else:
            raise ValueError(f"지원하지 않는 파일 형식입니다: {ext}. PDF, DOC, DOCX 파일만 지원합니다.")

    def load_documents(self, file_path: str, **kwargs: dict) -> list[Document]:
        loader = self.get_loader(file_path)
        documents = loader.load()
        return documents

    def split_documents(self, documents, **kwargs: dict) -> list[Document]:
        text_splitter = RecursiveCharacterTextSplitter(**kwargs)
        chunks = text_splitter.split_documents(documents)
        chunks = [chunk for chunk in chunks if chunk.page_content]
        if not chunks:
            raise Exception('Empty document')

        for chunk in chunks:
            page = chunk.metadata.get('page', 1)
            self.page_chunk_counts[page] += 1
        return chunks

    def compose_vectors(self, file_path: str, chunks: list[Document], **kwargs: dict) -> list[dict]:
        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=max([chunk.metadata.get('page', 1) for chunk in chunks]),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z'
        )
        current_page = None
        chunk_index_on_page = 0

        vectors = []
        for chunk_idx, chunk in enumerate(chunks):
            page = chunk.metadata.get('page', 1)
            text = chunk.page_content

            if page != current_page:
                current_page = page
                chunk_index_on_page = 0
            # 첨부용에서는 bbox 정보 추출 X
            # if doc:
            #     fitz_page = doc.load_page(page)
            #     global_metadata['chunk_bboxes'] = json.dumps(merge_overlapping_bboxes([{
            #         'page': page + 1,
            #         'type': 'text',
            #         'bbox': {
            #             'l': rect[0] / fitz_page.rect.width,
            #             't': rect[1] / fitz_page.rect.height,
            #             'r': rect[2] / fitz_page.rect.width,
            #             'b': rect[3] / fitz_page.rect.height,
            #         }
            #     } for rect in fitz_page.search_for(text)], x_tolerance=1 / fitz_page.rect.width,
            #         y_tolerance=1 / fitz_page.rect.height))
            vectors.append(GenOSVectorMeta.model_validate({
                'text': text,
                'n_char': len(text),
                'n_word': len(text.split()),
                'n_line': len(text.splitlines()),
                'i_page': page,
                'e_page': page,
                'i_chunk_on_page': chunk_index_on_page,
                'n_chunk_of_page': self.page_chunk_counts[page],
                'i_chunk_on_doc': chunk_idx,
                **global_metadata
            }))
            chunk_index_on_page += 1

        return vectors

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        ext = os.path.splitext(file_path)[-1].lower()
        
        # 지원하는 확장자 체크
        if ext not in ['.pdf', '.doc', '.docx']:
            raise ValueError(f"지원하지 않는 파일 형식입니다: {ext}. PDF, DOC, DOCX 파일만 지원합니다.")
        
        if ext in ['.docx', '.doc']:
            pdf_path = convert_to_pdf(file_path)
        if ext == '.docx':
            # DOCX는 GenosMsWordDocumentBackend 사용
            return await self.docx_processor(request, file_path, **kwargs)
        else:
            # PDF, DOC는 기존 방식 사용
            documents: list[Document] = self.load_documents(file_path, **kwargs)
            await assert_cancelled(request)

            chunks: list[Document] = self.split_documents(documents, **kwargs)
            await assert_cancelled(request)

            vectors: list[dict] = self.compose_vectors(file_path, chunks, **kwargs)
            return vectors
