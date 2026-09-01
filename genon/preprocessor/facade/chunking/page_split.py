"""PPT 전용 페이지 기반 청킹.

intelligent / convert 두 facade 가 같은 구현을 들고 있던 것을 한 벌로 모았다.
프로세서와 청커 클래스를 인자로 받는다 — 청커는 facade 마다 다른 파생 클래스이고,
설정값은 프로세서 속성에 있다.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from docling_core.transforms.chunker import DocChunk, DocMeta

from genon.preprocessor.facade.chunking import header_path as hp
from genon.preprocessor.facade.chunking import text_norm as tn
from genon.preprocessor.facade.common import config_parse as cp

_log = logging.getLogger(__name__)


def split_documents_by_page(processor, documents, chunker_cls, *,
                            min_chunk_size: Optional[int] = None, **kwargs) -> List[DocChunk]:
    """기본 1 page = 1 chunk.

    chunk_size(kwargs > yaml) 가 주어지고 chunk_mode 가 resize_all 이면 연속 페이지를
    토큰 기준 chunk_size 이하가 되도록 greedy 병합한다. 같은 페이지의 native text 와
    주입된 page description TextItem 은 prov.page_no 로 동일 페이지 청크에 자연히 묶인다.
    """
    chunk_size = cp.parse_optional_int(kwargs.get("chunk_size"), "chunk_size")
    if chunk_size is None:
        chunk_size = getattr(processor, "_chunk_size", None)
    if min_chunk_size:
        chunk_size = cp.clamp_chunk_size(chunk_size, min_chunk_size)
    # chunk_mode(0/1 또는 'split_only'/'resize_all') > yaml > "split_only"
    chunk_mode = cp.resolve_chunk_mode(kwargs, getattr(processor, "_chunk_mode", "split_only"))
    chunker = chunker_cls(
        max_tokens=chunk_size if chunk_size is not None else 0,
        merge_peers=True,
        tokenizer=processor._tokenizer,
        tokenizer_type=processor._tokenizer_type,
        chunk_mode=chunk_mode,
        include_chunk_header=cp.resolve_include_chunk_header(
            kwargs, getattr(processor, "_include_chunk_header", True)),
    )
    # 표 출력 설정(table_format/compact_tables/table_text_formats/table_row_serialization)을
    # 한 벌로 흘린다. 개별 setdefault 로 두면 설정이 늘 때마다 이 경로만 빠진다.
    # table_as_chunk 는 넘기지 않는다 — 이 경로의 계약은 "1 page = 1 chunk" 이고 표 격리는
    # 그 계약과 정면으로 충돌한다(누락이 아니라 의도).
    cp.apply_table_output_defaults(kwargs, processor)

    # 청크 텍스트 정규화(text_cleanup=safe): 청킹 입력에 문자 위생을 먼저 적용.
    _cleanup = tn.prepare_document(documents, kwargs, processor)

    # 전체 아이템 base chunk(정상 경로와 동일한 아이템 수집/헤더/누락표 복구 재사용)
    base = next(iter(chunker.preprocess(dl_doc=documents, **kwargs)), None)
    if base is None:
        return []
    items = base.meta.doc_items
    header_short = getattr(base, "_header_short_info_list", []) or []

    # prov page_no 로 그룹(아이템 순서 유지). prov 없으면 직전 페이지에 귀속.
    page_items: dict = {}
    page_headers: dict = {}
    last_page = 1
    for idx, it in enumerate(items):
        prov = getattr(it, "prov", None) or []
        pg = prov[0].page_no if prov and getattr(prov[0], "page_no", None) else last_page
        last_page = pg
        page_items.setdefault(pg, []).append(it)
        page_headers.setdefault(pg, []).append(header_short[idx] if idx < len(header_short) else {})

    # 페이지별 1 청크 직렬화
    page_chunks: List[DocChunk] = []
    for pg in sorted(page_items.keys()):
        its = page_items[pg]
        text = chunker._generate_section_text_with_heading(its, page_headers[pg], documents, **kwargs)
        if text and text.strip() and text.strip() != ".":
            page_chunks.append(DocChunk(
                text=text,
                # headings 를 채워야 compose_vectors 가 `HEADER:` 를 붙인다(본문 접두는 제거됨).
                meta=DocMeta(doc_items=its,
                             headings=chunker._extract_header_paths(page_headers[pg]),
                             captions=None, origin=documents.origin),
            ))

    # chunk_size>0 이면 연속 페이지 greedy 병합 (split_only 는 1 page = 1 chunk 유지)
    if chunk_mode == "resize_all" and chunk_size and chunk_size > 0 and page_chunks:
        merged: List[DocChunk] = [page_chunks[0]]
        for ch in page_chunks[1:]:
            cand_text = merged[-1].text + "\n" + ch.text
            # headings 를 None 으로 덮으면 위에서 채운 섹션 경로가 사라져 병합 청크에만
            # HEADER 가 안 붙는다. 경로를 합집합으로 승계하고, 크기 판정에도 그 헤더 라인을
            # 포함한다(경로가 길어지면 병합 여부가 달라져야 한다).
            cand_headings = hp.union_paths(merged[-1].meta.headings, ch.meta.headings)
            cand_size = chunker._count_tokens(
                chunker._header_line(cand_headings, chunker.include_chunk_header) + cand_text)
            if cand_size <= chunk_size:
                merged[-1] = DocChunk(
                    text=cand_text,
                    meta=DocMeta(
                        doc_items=merged[-1].meta.doc_items + ch.meta.doc_items,
                        headings=cand_headings, captions=None, origin=documents.origin,
                    ),
                )
            else:
                merged.append(ch)
        page_chunks = merged

    if _cleanup:
        page_chunks = tn.drop_blank_chunks(page_chunks)
    for ch in page_chunks:
        if ch.meta.doc_items and ch.meta.doc_items[0].prov:
            processor.page_chunk_counts[ch.meta.doc_items[0].prov[0].page_no] += 1
    # compose_vectors 가 읽는 표 기록을 프로세서로 돌려준다(정상 경로의 split_documents 와
    # 같은 배선). 이게 없으면 PPT 청크에는 표기형태 필드와 표 조각 순서가 실리지 않는다.
    processor._table_split_totals = getattr(chunker, "_table_split_totals", {})
    processor._table_variants = getattr(chunker, "_table_variants", None)
    _log.info(f"[ppt] page-based chunks: {len(page_chunks)} (chunk_size={chunk_size})")
    return page_chunks
