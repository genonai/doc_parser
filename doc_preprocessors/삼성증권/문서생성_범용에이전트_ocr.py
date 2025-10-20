from __future__ import annotations

from collections import defaultdict

import asyncio
import json
import os
import shutil
import subprocess
import sys
import warnings
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
    from transformers import AutoTokenizer
    from transformers.tokenization_utils_base import PreTrainedTokenizerBase
except ImportError:
    raise RuntimeError(
        "Module requires 'chunking' extra; to install, run: "
        "`pip install 'docling-core[chunking]'`"
    )

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PipelineOptions, AcceleratorDevice, AcceleratorOptions, 
    PdfPipelineOptions, TableFormerMode, TesseractOcrOptions
)
from docling.datamodel.document import ConversionResult
from docling.pipeline.simple_pipeline import SimplePipeline
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.document_converter import DocumentConverter, WordFormatOption, PdfFormatOption, FormatOption
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.backend.docling_parse_v4_backend import DoclingParseV4DocumentBackend
from docling_core.transforms.chunker.base import BaseChunk, BaseChunker
from docling_core.transforms.chunker.hierarchical_chunker import DocChunk, DocMeta
from docling_core.types.doc.document import (
    DoclingDocument as DLDocument, DoclingDocument, DocItem,
    PictureItem, SectionHeaderItem, TableItem, TextItem, ListItem, CodeItem, LevelNumber
)
from docling_core.types.doc.labels import DocItemLabel
from docling.backend.genos_msword_backend import GenosMsWordDocumentBackend
from utils import assert_cancelled
from genos_utils import upload_files

import logging

for n in ("fontTools", "fontTools.ttLib", "fontTools.ttLib.ttFont"):
    lg = logging.getLogger(n)
    lg.setLevel(logging.CRITICAL)
    lg.propagate = False
    logging.getLogger().setLevel(logging.WARNING)        

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
                # 기존 PictureItem 처리 방식 (docling에서 생성된 PictureItem)
                if item.image is not None and item.image.uri is not None:
                    path = str(item.image.uri)
                    name = path.rsplit("/", 1)[-1]
                    # print(f"PictureItem - path: {path}")
                    temp_list.append({'name': name, 'type': 'image', 'ref': item.self_ref})
                    # print(f"PictureItem - name: {name}, ref: {item.self_ref}")
            elif isinstance(item, ImageItem):
                if item.image is not None and item.image.uri is not None:
                    path = str(item.image.uri)
                    name = path.rsplit("/", 1)[-1]
                    # print(f"ImageItem - path: {path}")
                    temp_list.append({'name': name, 'type': 'image', 'ref': item.self_ref})
                    # print(f"ImageItem - name: {name}, ref: {item.self_ref}")
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
                if item.image is not None and item.image.uri is not None:
                    path = str(item.image.uri)
                    name = path.rsplit("/", 1)[-1]
                    temp_list.append({'path': path, 'name': name})
        print(temp_list)
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


class ImageItem:
    """PictureItem과 동일한 인터페이스를 가진 이미지 아이템"""
    
    def __init__(self, file_path: str):
        from docling_core.types.doc.document import ImageRef
        from docling_core.types.doc.labels import DocItemLabel
        from pathlib import Path
        from PIL import Image
        
        # 원본 파일 경로 저장
        self.original_file_path = file_path
        
        # 이미지 파일이 이미 존재하므로 PIL 없이 직접 ImageRef 생성
        # PIL은 이미지 크기와 유효성 검증에만 사용
        try:
            # PIL로 이미지 크기 추출 (선택적)
            with Image.open(file_path) as pil_image:
                image_size = self._get_image_size_from_pil(pil_image)
        except Exception as e:
            print(f"PIL 이미지 로드 실패, 기본값 사용: {e}")
            # PIL 실패 시 기본 크기 사용
            image_size = self._get_default_image_size()
        
        # ImageRef 직접 생성 (PIL 없이도 가능)
        self.image = ImageRef(
            mimetype=self._get_mimetype(file_path),
            dpi=72,
            size=image_size,
            uri=Path(file_path)  # 실제 파일 경로 직접 사용
        )
        
        # PictureItem의 필수 속성들
        self.label = DocItemLabel.PICTURE
        self.self_ref = f"image_{os.path.basename(file_path)}"
        
    
    
    def _get_mimetype(self, file_path: str) -> str:
        """파일 확장자에 따른 mimetype 반환"""
        ext = os.path.splitext(file_path)[-1].lower()
        mimetype_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png'
        }
        return mimetype_map.get(ext, 'image/jpeg')  # 기본값은 jpeg
    
    def _get_image_size_from_pil(self, pil_image):
        """PIL 이미지에서 크기 추출"""
        from docling_core.types.doc.base import Size
        return Size(width=pil_image.width, height=pil_image.height)
    
    def _get_default_image_size(self):
        """기본 이미지 크기 반환"""
        from docling_core.types.doc.base import Size
        return Size(width=100, height=100)
    
    def _get_image_size(self, file_path: str):
        """이미지 크기 가져오기 (PIL 사용)"""
        try:
            from PIL import Image
            from docling_core.types.doc.base import Size
            
            with Image.open(file_path) as img:
                return Size(width=img.width, height=img.height)
        except Exception:
            # 기본 크기 반환
            from docling_core.types.doc.base import Size
            return Size(width=100, height=100)


class ImageProcessor:
    """이미지 파일을 처리하는 클래스 - 단순 저장만"""
    
    def __init__(self):
        self.page_chunk_counts = defaultdict(int)

    def get_media_files(self, doc_items: list):
        temp_list = []
        for item in doc_items:
            if isinstance(item, (PictureItem, ImageItem)):
                # PictureItem과 ImageItem 모두 동일한 방식으로 처리
                if item.image is not None and item.image.uri is not None:
                    path = str(item.image.uri)
                    name = path.rsplit("/", 1)[-1]
                    temp_list.append({'path': path, 'name': name})
                    print(temp_list)
        return temp_list

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        """이미지 파일을 처리하여 최소한의 벡터 메타데이터 생성"""
        
        # 이미지 파일 존재 확인
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {file_path}")
        
        image_item = ImageItem(file_path)
        vectors = []
        doc_items = [image_item]
        
        # 최소한의 청크 1개 생성 (텍스트는 "."만)
        global_metadata = dict(
            n_chunk_of_doc=1,  # 이미지는 항상 1개 청크
            n_page=1,          # 이미지는 1페이지로 간주
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
        )
        
        # 벡터 메타데이터 생성 (DocxProcessor와 동일한 방식)
        vector = (GenOSVectorMetaBuilder()
                  .set_text(".")  # 텍스트는 "."만
                  .set_page_info(1, 1, 1)  # 페이지 0, 청크 인덱스 0, 총 1개 청크
                  .set_chunk_index(1)      # 문서 내 청크 인덱스 0
                  .set_global_metadata(**global_metadata)
                  .set_media_files(doc_items)  # DocxProcessor와 동일한 방식으로 media_files 설정
                  ).build()
        vectors.append(vector)
        # DocxProcessor와 동일한 방식으로 파일 업로드 처리
        file_list = self.get_media_files(doc_items)
        upload_task = asyncio.create_task(upload_files(file_list, request=request))
        await upload_task
        
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


class PdfImageProcessor:
    """PDF를 이미지로 처리하는 클래스"""
    
    def __init__(self):
        self.page_chunk_counts = defaultdict(int)
        
        # GPU/CPU 가속 설정
        device = AcceleratorDevice.AUTO
        num_threads = 8
        accelerator_options = AcceleratorOptions(num_threads=num_threads, device=device)
        
        self.pipe_line_options = PdfPipelineOptions()
        self.pipe_line_options.generate_page_images = True
        self.pipe_line_options.generate_picture_images = True
        self.pipe_line_options.do_ocr = True
        self.pipe_line_options.ocr_options.lang = ["ko", 'en']
        self.pipe_line_options.ocr_options.model_storage_directory = "/models/EasyOcr/" 
        self.pipe_line_options.ocr_options.force_full_page_ocr = True
        # paddle ocr 옵션 설정
        # ocr_options = PaddleOcrOptions(
        #     force_full_page_ocr=True,
        #     lang=['korean'],
        #     text_score=0.3)
        # self.pipe_line_options.ocr_options = ocr_options
        self.pipe_line_options.do_table_structure = True
        self.pipe_line_options.images_scale = 2
        self.pipe_line_options.table_structure_options.do_cell_matching = True
        self.pipe_line_options.table_structure_options.mode = TableFormerMode.ACCURATE
        self.pipe_line_options.accelerator_options = accelerator_options

        # PDF 이미지 처리용 컨버터 생성 
        self.converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_cls=StandardPdfPipeline,
                    pipeline_options=self.pipe_line_options,
                    backend=DoclingParseV4DocumentBackend
                ),
            }
        )
    
    def load_pdf_as_image(self, pdf_path: str, **kwargs) -> DoclingDocument:
        """PDF를 이미지로 처리하여 DoclingDocument로 로드"""
        try:
            conv_result: ConversionResult = self.converter.convert(pdf_path, raises_on_error=True)
            document = conv_result.document
            return document
        except Exception as e:
            print(f"[PdfImageProcessor] PDF 이미지 처리 실패: {e}")
            raise Exception(f"PDF 이미지 처리 실패: {str(e)}")
    
    def extract_text_from_pdf_document(self, document: DoclingDocument) -> str:
        """DoclingDocument에서 OCR로 추출된 텍스트 수집"""
        extracted_texts = []
        
        # 모든 텍스트 아이템에서 텍스트 추출
        for item, _ in document.iterate_items():
            if isinstance(item, TextItem) and item.text and item.text.strip():
                # 페이지 헤더/푸터 제외
                if item.label not in [DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER]:
                    extracted_texts.append(item.text.strip())
        
        # 추출된 텍스트가 없으면 기본 텍스트 반환
        if not extracted_texts:
            return "."
        
        return "\n".join(extracted_texts)
    
    def create_pdf_chunks(self, document: DoclingDocument, pdf_path: str) -> List[DocChunk]:
        """PDF 문서를 청크로 분할"""
        # OCR로 추출된 텍스트 가져오기
        extracted_text = self.extract_text_from_pdf_document(document)
        
        # PDF 파일 정보
        pdf_name = os.path.basename(pdf_path)
        
        # 텍스트가 추출되지 않은 경우 기본 텍스트 사용
        if not extracted_text or extracted_text.strip() == "":
            extracted_text = "추출된 텍스트가 없습니다."
        
        # 첫 번째 페이지의 기본 정보 사용
        page_no = 1
        from docling_core.types.doc.document import ProvenanceItem
        from docling_core.types.doc.base import BoundingBox
        
        prov = ProvenanceItem(
            page_no=page_no,
            bbox=BoundingBox(l=0, t=0, r=1, b=1),
            charspan=(0, len(extracted_text))
        )
        
        # 텍스트 아이템 생성
        text_item = document.add_text(
            label=DocItemLabel.TEXT,
            text=extracted_text,
            prov=prov
        )
        
        # 이미지 아이템도 추가 (있는 경우)
        doc_items: List[DocItem] = [text_item]
        for item, _ in document.iterate_items():
            if isinstance(item, PictureItem):
                doc_items.append(item)
                break
        
        # 단일 청크 생성
        chunk = DocChunk(
            text=f"{extracted_text}",
            meta=DocMeta(
                doc_items=doc_items,
                headings=None,
                captions=None,
                origin=document.origin,
            )
        )
        
        # 페이지별 청크 수 업데이트
        self.page_chunk_counts[page_no] = 1
        
        return [chunk]
    
    async def compose_vectors(self, document: DoclingDocument, chunks: List[DocChunk], 
                                  pdf_path: str, request: Request, **kwargs) -> List[dict]:
        """PDF 이미지 처리 결과를 벡터로 변환"""
        
        # PDF 파일 정보
        pdf_name = os.path.basename(pdf_path)
        
        # 글로벌 메타데이터
        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
        )
        current_page = None
        chunk_index_on_page = 0

        vectors = []
        for chunk_idx, chunk in enumerate(chunks):
            page = chunk.meta.doc_items[0].prov[0].page_no
            text = chunk.text

            if page != current_page:
                current_page = page
                chunk_index_on_page = 0

            vector = (GenOSVectorMetaBuilder()
                      .set_text(text)
                      .set_page_info(page, chunk_index_on_page, self.page_chunk_counts[page])
                      .set_chunk_index(chunk_idx)
                      .set_global_metadata(**global_metadata)
                      .set_chunk_bboxes(chunk.meta.doc_items, document)
                      .set_media_files(chunk.meta.doc_items)
                      ).build()
            vectors.append(vector)
            chunk_index_on_page += 1
        
        return vectors
    
    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        try:
            # 1) PDF를 이미지로 로드 (Docling 파이프라인으로 로드하되 OCR은 pipeline 옵션에 따름)
            document: DoclingDocument = self.load_pdf_as_image(file_path, **kwargs)

            # 2) PDF 문서를 청크로 분할
            chunks: List[DocChunk] = self.create_pdf_chunks(document, file_path)
            if not chunks:
                raise GenosServiceException("1", "PDF에서 청크를 생성할 수 없습니다")

            # 3) 벡터 생성
            vectors: list[dict] = await self.compose_vectors(document, chunks, file_path, request, **kwargs)
            if not vectors:
                raise GenosServiceException("1", "PDF 벡터 생성 실패")

            return vectors
        except GenosServiceException:
            raise
        except Exception as e:
            raise GenosServiceException("1", f"PDF 이미지 처리 실패: {str(e)}")


class DocumentProcessor:
    def __init__(self):
        self.page_chunk_counts = defaultdict(int)
        self.docx_processor = DocxProcessor()
        self.image_processor = ImageProcessor()
        self.pdf_image_processor = PdfImageProcessor()

    def get_loader(self, file_path: str):
        ext = os.path.splitext(file_path)[-1].lower()
        
        if ext == '.pdf':
            return PyMuPDFLoader(file_path)
        elif ext == '.doc':
            return UnstructuredWordDocumentLoader(file_path)
        else:
            raise ValueError(f"지원하지 않는 파일 형식입니다: {ext}. PDF, DOC, DOCX, JPG, JPEG, PNG 파일만 지원합니다.")

    def load_documents(self, file_path: str, **kwargs: dict) -> list[Document]:
        loader = self.get_loader(file_path)
        documents = loader.load()
        return documents

    def split_documents(self, documents, **kwargs: dict) -> list[Document]:
        text_splitter = RecursiveCharacterTextSplitter(**kwargs)
        chunks = text_splitter.split_documents(documents)
        chunks = [chunk for chunk in chunks if chunk.page_content]
        
        # Empty document 체크 - 텍스트가 없거나 모든 청크가 비어있는 경우
        if not chunks or not any(chunk.page_content.strip() for chunk in chunks):
            return []  # 빈 리스트 반환하여 이미지 처리로 넘어가도록 함

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
        if ext not in ['.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png']:
            raise ValueError(f"지원하지 않는 파일 형식입니다: {ext}. PDF, DOC, DOCX, JPG, JPEG, PNG 파일만 지원합니다.")
        
        # 이미지 파일 처리
        if ext in ['.jpg', '.jpeg', '.png']:
            return await self.image_processor(request, file_path, **kwargs)
        
        # DOCX 파일 처리 (고급 처리)
        elif ext == '.docx':
            return await self.docx_processor(request, file_path, **kwargs)
        
        # PDF, DOC 파일 처리 (기본 처리)
        else:
            documents: list[Document] = self.load_documents(file_path, **kwargs)
            await assert_cancelled(request)

            chunks: list[Document] = self.split_documents(documents, **kwargs)
            await assert_cancelled(request)

            # PDF 파일이고 텍스트 추출이 실패한 경우 이미지 처리로 전환
            if ext == '.pdf' and not chunks:
                print(f"[DocumentProcessor] PDF 텍스트 추출 실패, 이미지 처리로 전환: {file_path}")
                return await self.pdf_image_processor(request, file_path, **kwargs)

            vectors: list[dict] = self.compose_vectors(file_path, chunks, **kwargs)
            return vectors
