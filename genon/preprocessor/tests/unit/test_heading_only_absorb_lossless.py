"""제목-only 청크 흡수(_merge_heading_only_chunks)가 어떤 설정에서도 무손실인지 검증.

배경: md 의 `#` 는 TITLE 아이템이 되고, 바로 뒤에 `##` 가 오면 TITLE 하나짜리 섹션이 된다.
30자를 넘는 제목은 3단계 단독 타이틀 병합에서 제외되므로 단독 청크로 남고, _merge_heading_only_chunks
가 이를 다음 청크로 흡수한다. 이때 제목 텍스트는 HEADER 라인(=headings 경로)으로만 렌더되므로
include_chunk_header 가 꺼져 있으면 제목이 산출물에서 완전히 사라졌다
(실측: 상품설명서 생명 md 의 40자 H1 이 청크 어디에도 없었다).
"""

import pytest

pytestmark = pytest.mark.unit

from docling_core.transforms.chunker import DocChunk, DocMeta
from docling_core.types import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

from genon.preprocessor.facade.chunking.smart_chunker import SmartChunkerBase

# 3단계 병합 임계(30자)를 넘는 제목. 실제 문제 문서의 H1 과 같은 길이대다.
_LONG_TITLE = "삼성 1540 청춘대표·4180 인생대표·팩 건강보험(2607) 상품요약서"
_SHORT_TITLE = "문서 개요"
_BODY = "피보험자 가입나이 만 15세 이상 40세 이하"


def _chunker(include_chunk_header: bool) -> SmartChunkerBase:
    """검증 대상 메서드만 쓰는 최소 인스턴스. max_tokens=0 이면 _fits 가 토크나이저를 타지 않는다."""
    return SmartChunkerBase.model_construct(
        include_chunk_header=include_chunk_header, max_tokens=0)


def _chunks(title: str):
    """제목-only 청크 + 본문 청크. doc_items 는 실제 DocItem 이어야 유형 판정이 동작한다."""
    doc = DoclingDocument(name="sample")
    title_item = doc.add_title(text=title)
    body_item = doc.add_text(label=DocItemLabel.TEXT, text=_BODY)
    return [
        DocChunk(text=title, meta=DocMeta(doc_items=[title_item], headings=[title])),
        DocChunk(text=_BODY, meta=DocMeta(doc_items=[body_item], headings=[title])),
    ]


def test_header_off_keeps_title_in_body():
    """헤더 라인이 꺼져 있으면 제목을 본문에 이어붙여 유실을 막는다."""
    result = _chunker(include_chunk_header=False)._merge_heading_only_chunks(_chunks(_LONG_TITLE))

    assert len(result) == 1
    assert _LONG_TITLE in result[0].text
    assert _BODY in result[0].text


def test_header_on_keeps_body_free_of_title():
    """헤더 라인이 켜져 있으면 제목은 경로로만 렌더되므로 본문에 중복시키지 않는다."""
    result = _chunker(include_chunk_header=True)._merge_heading_only_chunks(_chunks(_LONG_TITLE))

    assert len(result) == 1
    assert result[0].text == _BODY
    assert _LONG_TITLE in result[0].meta.headings


def test_short_title_also_absorbed_losslessly():
    """30자 이하 제목도 같은 경로를 탄다(3단계에서 병합되지 못하고 남은 경우)."""
    result = _chunker(include_chunk_header=False)._merge_heading_only_chunks(_chunks(_SHORT_TITLE))

    assert len(result) == 1
    assert _SHORT_TITLE in result[0].text


def test_trailing_title_merges_backward_losslessly():
    """문서 끝에 남은 제목-only 청크는 이전 청크로 후방 병합되며 텍스트가 보존된다."""
    body_chunk, title_chunk = _chunks(_LONG_TITLE)[1], _chunks(_LONG_TITLE)[0]
    result = _chunker(include_chunk_header=False)._merge_heading_only_chunks(
        [body_chunk, title_chunk])

    assert len(result) == 1
    assert _LONG_TITLE in result[0].text
    assert result[0].text.index(_BODY) < result[0].text.index(_LONG_TITLE)
