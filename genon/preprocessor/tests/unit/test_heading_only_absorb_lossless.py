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

from genon.preprocessor.processing.chunking import chunk_quality as cq
from genon.preprocessor.processing.chunking.smart_chunker import SmartChunkerBase

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


# --- 짧은 본문 청크 병합(short_chunk_size) ---
# 상품설명서는 본문이 1~2줄뿐인 섹션이 많아 HEADER 경로가 대부분인 청크가 생긴다. 장이 바뀌어도
# 다음 청크에 붙이고, 끄면(0) 지금과 같아야 한다.
_SHORT_BODY = "벌금은 1사고당 2천만원 한도로 보상합니다."


def _short_then_next(next_is_table: bool = False):
    """짧은 본문 청크(제1장) + 다음 청크(제2장). next_is_table 이면 다음 청크가 표다."""
    doc = DoclingDocument(name="sample")
    short_item = doc.add_text(label=DocItemLabel.TEXT, text=_SHORT_BODY)
    if next_is_table:
        from docling_core.types.doc.document import TableData
        next_item = doc.add_table(data=TableData(num_rows=1, num_cols=1))
    else:
        next_item = doc.add_text(label=DocItemLabel.TEXT, text=_BODY)
    return [
        DocChunk(text=_SHORT_BODY, meta=DocMeta(doc_items=[short_item], headings=["제1장 > 2. 벌금"])),
        DocChunk(text=_BODY, meta=DocMeta(doc_items=[next_item], headings=["제2장 > 1. 보장"])),
    ]


@pytest.mark.parametrize("short_chunk_size, merged", [(0, False), (200, True)])
def test_short_body_merges_into_next_chunk_across_chapters(short_chunk_size, merged):
    chunker = SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=0, short_chunk_size=short_chunk_size)
    result = chunker._merge_heading_only_chunks(_short_then_next())

    if not merged:
        assert [c.text for c in result] == [_SHORT_BODY, _BODY]
        return
    assert len(result) == 1
    assert result[0].text == f"{_SHORT_BODY}\n{_BODY}"
    assert result[0].meta.headings == ["제1장 > 2. 벌금", "제2장 > 1. 보장"]


def _text_chunk(doc, text, heading):
    item = doc.add_text(label=DocItemLabel.TEXT, text=text)
    return DocChunk(text=text, meta=DocMeta(doc_items=[item], headings=[heading]))


_LINE = "보험금은 약관에서 정한 지급사유가 발생한 경우에 지급합니다."  # 33자


def _full_chunk(doc, heading, lines=11):
    """한도(400) 가까이 찬 본문 청크. 줄 경계에서 다시 나눌 수 있다."""
    return _text_chunk(doc, "\n".join([_LINE] * lines), heading)


@pytest.mark.parametrize("layout", [
    "short_pages_run",      # 짧은 쪽 20개 연속 → 정상 쪽 (예전: 한 청크로 쌓여 한도의 몇 배)
    "short_before_full",    # 문서 첫머리 짧은 쪽 → 꽉 찬 청크 (예전: 붙인 만큼 한도 초과)
    "short_after_full",     # 꽉 찬 청크 → 문서 끝 짧은 쪽
    "short_before_long_lines",  # 옮기지 않는 긴 줄은 원문 줄 그대로 남는다(예전: 기준 단위로 줄바꿈)
])
def test_short_merge_keeps_size_limit_without_short_chunks(layout):
    """짧은 청크를 남기지 않으면서 어떤 청크도 한도를 넘지 않고, 본문은 순서대로 모두 남는다."""
    doc = DoclingDocument(name="sample")
    short = lambda i: _text_chunk(doc, f"원문 {i}쪽 안내 문장입니다.", f"원문 {i}쪽")
    chunks = {
        "short_pages_run": [short(i) for i in range(20)] + [_full_chunk(doc, "본문", lines=5)],
        "short_before_full": [short(0), _full_chunk(doc, "본문")],
        "short_after_full": [_full_chunk(doc, "본문"), short(0)],
        "short_before_long_lines": [short(0), _text_chunk(
            doc, "\n".join([_LINE] * 3 + [_LINE + " " + _LINE * 3] * 2), "본문")],
    }[layout]
    chunker = SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=400, short_chunk_size=100,
        chunk_prefix_text="", CHUNK_PATH_MAX_LEAVES=5)
    result = chunker._merge_heading_only_chunks(chunks)

    sizes = [len(chunker._header_line(c.meta.headings, True) + c.text) for c in result]
    assert max(sizes) <= 400, sizes
    assert min(len(c.text) for c in result) >= 100
    assert "\n".join(c.text for c in result) == "\n".join(c.text for c in chunks)


def test_short_run_stays_out_of_table_chunk():
    """짧은 쪽들이 묶여 짧지 않게 되면 독립 청크로 둔다. 표가 든 다음 청크에 섞이지 않는다."""
    doc = DoclingDocument(name="sample")
    pages = [_text_chunk(doc, "여행 중 상해나 질병으로 치료를 받으면 실제 치료비를 보상합니다. " * 2, "원문 1쪽"),
             _text_chunk(doc, "전쟁·테러 지역 여행은 인수가 제한됩니다. 현금은 보상하지 않습니다.", "원문 2쪽")]
    from docling_core.types.doc.document import TableData
    table = DocChunk(text="| 구분 | 내용 |", meta=DocMeta(
        doc_items=[doc.add_table(data=TableData(num_rows=1, num_cols=2))], headings=["원문 3쪽"]))
    chunker = SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=1500, short_chunk_size=100, table_as_chunk=True)
    result = chunker._merge_heading_only_chunks([*pages, table])

    assert [c.text for c in result] == [f"{pages[0].text}\n{pages[1].text}", table.text]


def test_short_body_merges_into_table_chunk():
    """table_as_chunk 의 표 청크도 짧은 청크를 받는다(짧은 청크를 남기지 않는 것이 우선)."""
    chunker = SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=0, short_chunk_size=200, table_as_chunk=True)
    result = chunker._merge_heading_only_chunks(_short_then_next(next_is_table=True))

    assert [c.text for c in result] == [f"{_SHORT_BODY}\n{_BODY}"]


# --- 실제 검증 판정(chunk_quality)과 함께 쓸 때 ---
_DROP = cq.rejects_doc_chunk(cq.Config(action="drop"))


def _force(**kwargs):
    return SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=0, short_chunk_size=200, **kwargs)


def test_merge_never_creates_a_chunk_the_validation_drops():
    """하나하나는 정상인 같은 안내 문구 10개를 합치면 반복으로 판정되어 문서 전체가 실패했다.
    합친 결과가 버려질 청크가 되는 병합은 하지 않는다."""
    doc = DoclingDocument(name="sample")
    chunks = [_text_chunk(doc, "해당 특약은 가입 시 선택할 수 있습니다.", f"특약{i}") for i in range(10)]
    result = _force(merge_guard=_DROP)._merge_heading_only_chunks(chunks)

    assert not any(_DROP(c) for c in result)
    assert "\n".join(c.text for c in result).count("해당 특약은") == 10


def test_short_chunk_never_merges_over_limit():
    """이웃이 한도에 가깝고 표가 들어 나눌 수 없으면 한도를 넘겨 붙이지 않고 짧은 청크로 남긴다(#547).
    다음 섹션 제목은 각주 묶음 끝이 아니라 자기 섹션 앞에 놓인다(실물: hwpx 표 각주 → '5. 종류별 이혼')."""
    from docling_core.types.doc.document import TableData
    doc = DoclingDocument(name="sample")
    note = _text_chunk(doc, "1) 다문화 이혼 : 남녀 어느 한쪽이 외국인 또는 귀화자인 경우", "4. 지속기간")
    title = DocChunk(text="5. 종류별 이혼", meta=DocMeta(
        doc_items=[doc.add_title(text="5. 종류별 이혼")], headings=["5. 종류별 이혼"]))
    body = DocChunk(text="\n".join([_LINE] * 10), meta=DocMeta(
        doc_items=[doc.add_text(label=DocItemLabel.TEXT, text=_LINE),
                   doc.add_table(data=TableData(num_rows=1, num_cols=1))], headings=["5. 종류별 이혼"]))
    chunker = SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=400, short_chunk_size=100,
        chunk_prefix_text="", CHUNK_PATH_MAX_LEAVES=5)
    result = chunker._merge_heading_only_chunks([note, title, body])

    assert [c.text for c in result] == [note.text, f"{title.text}\n{body.text}"]


def test_trailing_short_skips_dropped_chunk_to_merge_backward():
    """문서 끝 짧은 청크 바로 앞이 버려질 청크면 그 앞의 정상 청크에 붙는다(실물: 상품요약서 7쪽)."""
    doc = DoclingDocument(name="sample")
    body = "\n".join([_LINE] * 7)  # 기준(200) 이상인 정상 청크
    chunks = [_text_chunk(doc, body, "가"),
              _text_chunk(doc, "x", "나"),                       # 너무 짧아 검증에서 버려진다
              _text_chunk(doc, "사고 접수 후 청구서를 앱에 올립니다.", "다")]
    result = _force(merge_guard=_DROP)._merge_heading_only_chunks(chunks)

    assert [c.text for c in result if not _DROP(c)] == [f"{body}\n사고 접수 후 청구서를 앱에 올립니다."]


def test_formula_chunk_kept_by_validation_is_not_skipped():
    """검증은 수식 청크를 길이 하한에서 예외로 둔다. 같은 판정을 쓰지 않으면 살아남는 수식 청크를
    건너뛰어 [가, 수식, 다] 가 [가+다, 수식] 으로 뒤집혔다."""
    doc = DoclingDocument(name="sample")
    formula = doc.add_text(label=DocItemLabel.FORMULA, text="x=1")
    chunks = [_text_chunk(doc, "납입 조건은 아래 수식을 적용합니다.", "가"),
              DocChunk(text="x=1", meta=DocMeta(doc_items=[formula], headings=["나"])),
              _text_chunk(doc, "일반 설명이 담긴 다음 문단입니다.", "다")]
    result = _force(merge_guard=_DROP)._merge_heading_only_chunks(chunks)

    text = "\n".join(c.text for c in result)
    assert text.index("납입 조건") < text.index("x=1") < text.index("일반 설명")


def test_heading_only_titles_survive_header_leaf_limit():
    """HEADER 는 경로를 5개까지만 렌더한다. 제목을 경로로만 남기면 6번째부터 산출물에서 사라졌다."""
    doc = DoclingDocument(name="sample")
    titles = [f"독립항목{i}" for i in range(8)]
    chunks = [DocChunk(text=t, meta=DocMeta(doc_items=[doc.add_title(text=t)], headings=[t]))
              for t in titles] + [_text_chunk(doc, _BODY, "본문")]
    result = _force()._merge_heading_only_chunks(chunks)

    assert len(result) == 1
    assert all(t in result[0].text for t in titles)


# 실물: monimo_product_slf_span_table_sample.md 의 5쪽 → 6쪽 경계. 6쪽 제목 바로 뒤의 표 조각이 한도에
# 가까워 제목이 다음 청크에 들어가지 못하면, 한도 안에 들어가는 앞(5쪽) 청크 끝에 붙어 HEADER 에 5·6쪽
# 경로가 함께 실렸다. 이를 막으려고 한도를 넘겨 표 조각 앞에 붙이자 1500자를 넘었다(#547, 1502자).
_P5 = "인터넷치아보험 span 표 재현 상품요약서 > 원문 5쪽 - 2. 보험금 지급사유"
_P6_TITLE = "원문 6쪽 - 피보험자가 보험기간 중 「\"치과치료보장개시일 이후에 치아에 대하여 '치아우식증"
_P6 = f"인터넷치아보험 span 표 재현 상품요약서 > {_P6_TITLE}"


@pytest.mark.parametrize("layout, table_len, title_left", [
    ("title_only", 1364, False),             # 현재 샘플: 제목 단독 청크, 제목 줄까지 넣으면 한도 초과
    ("title_only", 2060, True),              # 다음 표 조각이 이미 한도 초과 — 제목은 단독으로 남는다
    ("short_body_then_title", 1364, False),  # 설명 없는 실행: 짧은 5쪽 청크와 6쪽 제목이 한 묶음
])
def test_next_section_title_never_joins_previous_section(layout, table_len, title_left):
    """제목은 앞 섹션 청크에 붙지 않고, 한도를 넘겨 표 조각에 붙지도 않는다(#547). 표 조각 HEADER 가 이미
    렌더하는 제목은 본문에 넣지 않고 붙는다. 남은 짧은 본문은 같은 섹션인 앞 청크로 간다."""
    from docling_core.types.doc.document import TableData
    doc = DoclingDocument(name="sample")
    prev = _text_chunk(doc, "가" * 355, _P5)
    short = [_text_chunk(doc, "나" * 158, _P5)] if layout == "short_body_then_title" else []
    title = DocChunk(text=_P6_TITLE, meta=DocMeta(
        doc_items=[doc.add_heading(text=_P6_TITLE)], headings=[_P6]))
    table = DocChunk(text="다" * table_len, meta=DocMeta(
        doc_items=[doc.add_table(data=TableData(num_rows=1, num_cols=1))], headings=[_P6]))
    chunker = SmartChunkerBase.model_construct(
        include_chunk_header=True, max_tokens=1500, short_chunk_size=200,
        chunk_prefix_text="", CHUNK_PATH_MAX_LEAVES=5)
    result = chunker._merge_heading_only_chunks([prev, *short, title, table])

    assert [(c.text, c.meta.headings) for c in result] == [
        ("\n".join([prev.text, *(s.text for s in short)]), [_P5]),
        *([(_P6_TITLE, [_P6])] if title_left else []),
        (table.text, [_P6]),
    ]
    assert _P6_TITLE in chunker._header_line(result[-1].meta.headings, True)
