"""docling_core HybridChunker 계열의 포크본.

첨부용 전처리기가 오래전 docling_core 에서 복사해 독자적으로 키운 청커다.
설치된 docling_core(2.85) 와는 이미 API 가 갈라져 있어 그대로 상속할 수 없고,
동시에 여러 facade 가 쓸 수 있도록 여기 한 벌만 둔다.

이름을 docling_core 와 다르게 둔 이유
    facade 안에서 HierarchicalChunker / HybridChunker 라는 이름을 그대로 쓰면
    같은 모듈에서 import 한 docling_core 타입들과 섞여 어느 쪽 구현인지 읽는
    사람이 알 수 없다. 포크본임이 이름에서 드러나게 한다.

업스트림과 갈라진 지점(되돌리려면 여기부터 본다)
    - tokenizer_type="char": HF 토크나이저를 로드하지 않고 문자 수로 길이를 센다.
      업스트림에는 대응물이 없다. 외부 모델 의존을 없애는 것이 목적이다.
    - captions: DocMeta.captions 를 병합 판정 기준에 포함한다. 업스트림은 이
      필드를 deprecated 로 두고 판정에서 뺐다.
    - doc_serializer: 업스트림은 chunk() 에서 serializer 를 만들어 하위 메서드까지
      넘긴다. 이 포크본은 DocItem.text 를 직접 읽는 옛 방식이다.
    - _triplet_serialize / compact_tables: 표 직렬화가 genon 쪽으로 갈라져 있다.
    - 표 행 분할: 한 행이 분할 예산을 넘으면 경고를 남긴다(업스트림에 없음).

docling 타입 import 는 피하라는 규칙의 예외다. BaseChunker 를 상속하고
DocChunk/DocMeta 를 생성하는 클래스라 duck typing 으로 대체할 수 없다.
같은 이유로 facade/chunking/smart_chunker.py 도 docling 타입을 직접 쓴다.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Union

from pandas import DataFrame
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

from docling_core.transforms.chunker import BaseChunk, BaseChunker, DocChunk, DocMeta
from docling_core.types import DoclingDocument as DLDocument
from docling_core.types.doc import (
    DocItem, DocItemLabel, DoclingDocument,
    PictureItem, SectionHeaderItem, TableItem, TextItem
)
from docling_core.types.doc.document import LevelNumber, ListItem, CodeItem

from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.common.markdown_export import export_markdown
from genon.preprocessor.facade.chunking.rich_cells import table_embedded_refs
from genon.preprocessor.facade.chunking.table_html import (
    MD_TABLE_PARAMS, drop_blank_markdown_rows, render_degenerate,
)
from genon.preprocessor.facade.chunking.table_shape import analyze_grid
from genon.preprocessor.facade.chunking.table_splitter import split_table_rows

_log = logging.getLogger(__name__)

# 사실상 무제한. 호출부가 max_tokens 를 항상 명시하므로 이 값은 안전망이다.
DEFAULT_HYBRID_MAX_TOKENS = int(1e30)


resolve_compact_tables = cp.resolve_compact_tables


class HierarchicalDocChunker(BaseChunker):
    r""" Chunker implementation leveraging the document layout.
    Args:
        merge_list_items (bool): Whether to merge successive list items.
            Defaults to True.
        delim (str): Delimiter to use for merging text. Defaults to "\n".
    """
    merge_list_items: bool = True

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
            f"{rows[i]}, {cols[j]} = {str(table_df.iloc[i, j]).strip()}"
            for i in range(1, nrows)
            for j in range(1, ncols)
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
        # 표마다 반복 파싱/경고하지 않도록 루프 진입 전에 한 번만 해석한다.
        compact_tables = resolve_compact_tables(kwargs)
        # rich cell 내용은 표 직렬화 결과에 이미 들어 있다(smart_chunker 와 같은 이유).
        rich_refs = table_embedded_refs(dl_doc)
        for item, level in dl_doc.iterate_items():
            if getattr(item, "self_ref", None) in rich_refs:
                continue
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
                    # 레이아웃용 표(안내 배너 등)는 표기형태와 무관하게 평문으로 낸다.
                    # 캡션은 아래 공통 경로에서 실으므로 여기서는 넘기지 않는다.
                    text = render_degenerate(getattr(item, "data", None))
                    if not text:
                        # compact_tables 는 컬럼 정렬 패딩을 없애 대형 표 markdown 크기를 줄인다.
                        text = export_markdown(
                            dl_doc, item=item,
                            compact_tables=compact_tables, **MD_TABLE_PARAMS,
                        )
                    text = drop_blank_markdown_rows(text)
                    # dataframe으로 추출할 때 사용되는 코드
                    # if table_df.shape[0] < 1 or table_df.shape[1] < 2:
                    #     # at least two cols needed, as first column contains row headers
                    #     continue
                    # text = self._triplet_serialize(table_df=table_df)
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


class TokenAwareHybridChunker(BaseChunker):
    r"""Chunker doing tokenization-aware refinements on top of document layout chunking.
    Args:
        tokenizer: The tokenizer to use; either instantiated object or name or path of
            respective pretrained model
        max_tokens: The maximum number of tokens per chunk. If not set, limit is
            resolved from the tokenizer
        merge_peers: Whether to merge undersized chunks sharing same relevant metadata
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    tokenizer: Union[PreTrainedTokenizerBase, str, Path] = (
            Path(cp.DEFAULT_TOKENIZER_LOCAL_PATH)
            if Path(cp.DEFAULT_TOKENIZER_LOCAL_PATH).exists()
            else cp.DEFAULT_TOKENIZER_ID
        )
    max_tokens: int = DEFAULT_HYBRID_MAX_TOKENS  # type: ignore[assignment]
    merge_peers: bool = True
    # 토큰 수 계산 방식. "char"(default)=문자 수 기준 | "huggingface"=HF 토크나이저 기준
    tokenizer_type: str = "char"
    _inner_chunker: HierarchicalDocChunker = HierarchicalDocChunker()

    @model_validator(mode="after")
    def _patch_tokenizer_and_max_tokens(self) -> Self:
        mode = (self.tokenizer_type or "char").strip().lower()
        if mode not in {"char", "huggingface"}:
            _log.warning(f"[TokenAwareHybridChunker] Unknown tokenizer_type '{mode}', fallback to 'char'.")
            mode = "char"
        self.tokenizer_type = mode
        if mode == "char":
            # 문자 수 기반: HF 토크나이저 로드 불필요 (외부 모델 의존 제거)
            self._tokenizer = None
            if self.max_tokens is None:
                self.max_tokens = DEFAULT_HYBRID_MAX_TOKENS
        else:
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
        if self._tokenizer is None:   # 문자 수 기반
            return len(text)
        return len(self._tokenizer.tokenize(text))

    class _ChunkLengthInfo(BaseModel):
        total_len: int
        text_len: int
        other_len: int

    def _count_chunk_tokens(self, doc_chunk: DocChunk):
        ser_txt = self.contextualize(chunk=doc_chunk)
        if self._tokenizer is None:   # 문자 수 기반
            return len(ser_txt)
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
                    # 아직 청크에 여유가 있고, 남은 아이템도 있으므로 계속 추가 시도
                    continue
                else:
                    # 현재 윈도우의 모든 아이템이 청크에 들어갔고, 더 이상 아이템이 없음
                    window_end = num_items  # signalizing the last loop
            elif window_start == window_end:
                # 아이템 1개도 청크에 안 들어감 → 단독 청크로 처리, 이후 재분할
                window_end += 1
                window_start = window_end
            else:
                # 마지막 아이템 빼고 청크 생성 → 남은 아이템으로 새 윈도우 시작
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
            # 헤더/캡션을 제외하고 본문 텍스트에 할당 가능한 토큰 수 계산
            available_length = self.max_tokens - lengths.other_len
            # char 모드는 문자 수 카운터 len 사용
            counter = len if self._tokenizer is None else self._tokenizer
            sem_chunker = semchunk.chunkerify(
                counter, chunk_size=available_length
            )
            if available_length <= 0:
                warnings.warn(
                    f"Headers and captions for this chunk are longer than the total amount of size for the chunk, chunk will be ignored: {doc_chunk.text=}"
                    # noqa
                )
                return []

            # HierarchicalChunker가 만든 단일 TableItem 청크는 일반 semchunk에 보내면
            # Markdown 헤더가 첫 조각에만 남는다. 공통 행 분할기로 각 조각에 헤더와
            # 구분선을 반복하여 모두 독립적인 Markdown 표가 되게 한다.
            doc_items = list(doc_chunk.meta.doc_items or [])
            if len(doc_items) == 1 and isinstance(doc_items[0], TableItem):
                table_item = doc_items[0]
                try:
                    grid = table_item.data.grid
                    num_cols = table_item.data.num_cols
                except Exception:
                    grid, num_cols = None, 0
                # 첨부 경로는 항상 markdown 이고 입력이 HTML 계열이 아니어도 thead 를
                # 신뢰한다(HierarchicalChunker 가 이미 표 하나만 떼어 준 상태).
                shape = analyze_grid(grid, num_cols, is_html_origin=True)
                if shape is not None:
                    result = split_table_rows(
                        grid=grid,
                        num_cols=shape.num_cols,
                        single_text=doc_chunk.text,
                        limit=available_length,
                        count_text=self._count_text_tokens,
                        table_format="markdown",
                        header_row_count=shape.header_row_count,
                    )
                    for index in result.oversized_piece_indexes:
                        _log.warning(
                            "[TokenAwareHybridChunker] 표의 단일 행이 분할 예산(%d)을 초과해 "
                            "행 구조를 보존한 채 유지합니다: table=%s size=%d",
                            available_length, getattr(table_item, "self_ref", ""),
                            self._count_text_tokens(result.pieces[index]),
                        )
                    return [
                        type(doc_chunk)(text=piece, meta=doc_chunk.meta)
                        for piece in result.pieces
                    ]

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
                    # 토큰 수 여유 있음 → 청크 확장 계속
                    window_end += 1
                    new_chunk = candidate
                else:
                    ready_to_append = True

            if ready_to_append or window_end == num_chunks:
                # no more room OR the start of new metadata.
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
