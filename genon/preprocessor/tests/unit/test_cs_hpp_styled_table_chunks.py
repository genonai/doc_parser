"""고객센터 카드 문서(cs_hpp)의 표가 청크에 깨끗하게 실리는지 고정한다.

fixture 는 실제 보고된 문서를 옮긴 markdown 이다 — `# [AI 에이전트용]` 하나에 ◈/▣/※ 마커
문단, 그리고 인라인 `style="color:revert"` · `<colgroup>` · `<span style="color: rgb(51,51,51)">`
로 뒤덮인 원시 HTML 표 두 개(첫 표는 `colspan="4"` 안내 한 줄)가 본문에 박혀 있다.

두 가지를 본다.

1. 원문의 표시용 마크업(style/colgroup/span)이 산출물로 새지 않는다.
2. 표 설명 enricher 가 붙인 annotation 이 docling_core 에 의해 `meta` 로 이관된 뒤에도
   `<details class="docling-meta">` 블록으로 청크 본문에 딸려 나오지 않는다.
   (`/parser` → docling JSON → `/chunker` 왕복이 그 이관을 일으킨다.)
"""

from pathlib import Path

import pytest


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cs_hpp_styled_tables.md"

PRESENTATION_MARKUP = ("style=", "colgroup", "<col", "<span", "revert", "rgb(")
# 청크 텍스트에는 위에 더해 재직렬화가 다시 붙이던 것들도 남으면 안 된다. docling 의
# `export_to_html` 자체는 여전히 rich cell 서브트리를 내보내므로 문서 수준 단정과 구분한다.
CHUNK_MARKUP = PRESENTATION_MARKUP + (
    "<p>", "<ul", "<li", "<a href", "inline-group", "<!-- image -->", "&gt;",
)
META_LEAK_MARKUP = (
    "docling-meta", "docling_legacy", "table_retrieval",
    "retrieval_context", "repeat_context_on_split", "include_search_terms",
)

CHUNKER_MODULES = [
    "genon.preprocessor.facade.chunking_processor",
    "genon.preprocessor.facade.intelligent_processor",
    "genon.preprocessor.facade.convert_processor",
]


def _document():
    pytest.importorskip("docling.document_converter", exc_type=ImportError)
    from docling.document_converter import DocumentConverter

    return DocumentConverter().convert(FIXTURE).document


def _attach_table_descriptions(doc):
    """custom_fields 통합 호출이 표마다 붙이는 annotation 을 그대로 재현한다."""
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    for index, table in enumerate(doc.tables):
        context = f"네이버페이 간편결제 안내 표 {index + 1}"
        table.annotations.append(core.DescriptionAnnotation(
            text=context, provenance="facade_table_text_description",
        ))
        table.annotations.append(core.MiscAnnotation(content={
            "provenance": "facade_table_text_description",
            "table_retrieval": {
                "retrieval_context": context,
                "key_facts": ["비밀번호 6자리 입력 후 결제하기"],
                "search_terms": ["네이버페이 결제 방법"],
                "include_search_terms": False,
                "repeat_context_on_split": True,
            },
        }))
    return doc


def _roundtrip(doc):
    """/parser 산출 docling JSON 을 /chunker 가 다시 읽는 경로."""
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    return core.DoclingDocument.model_validate(doc.model_dump(mode="json"))


@pytest.mark.unit
def test_source_presentation_markup_does_not_reach_parsed_output():
    doc = _document()
    assert len(doc.tables) == 2  # 본문에 박힌 원시 HTML 표가 표로 인식된다

    markdown = doc.export_to_markdown()
    table_html = doc.tables[1].export_to_html(doc=doc)
    for token in PRESENTATION_MARKUP:
        assert token not in markdown
        assert token not in table_html

    # 표시용 마크업만 사라지고 내용은 남는다.
    assert "◈ 시행일자" in markdown
    assert "2015년 6월 25일" in markdown
    assert "네이버페이 고객센터 1588-3819" in markdown
    # colspan 은 표 구조 정보라 HTML 형식에서는 보존된다.
    assert 'colspan="4"' in doc.tables[0].export_to_html(doc=doc)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", CHUNKER_MODULES)
def test_styled_table_chunks_carry_no_markup_and_no_enricher_meta(module_name):
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _roundtrip(_attach_table_descriptions(_document()))
    assert doc.tables[0].meta is not None  # 이관이 실제로 일어났는지 먼저 고정

    chunker = module.GenosSmartChunker(
        max_tokens=4000, chunk_mode="split_only", tokenizer_type="char"
    )
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)]

    assert texts
    for text in texts:
        for token in CHUNK_MARKUP:
            assert token not in text
        for token in META_LEAK_MARKUP:
            assert token not in text

    body = "\n".join(texts)
    # 픽스처의 표 2개 중 첫째는 `colspan="4"` 안내 배너(레이아웃용)라 표기 없이 평문으로
    # 나간다. 표로 실리는 것은 정형 표 하나뿐이고, 설명은 표마다 접두 한 번씩만 붙는다.
    assert body.count("<table>") == 1
    assert body.count("[표 검색 설명]") == 2
    assert body.count("네이버페이 간편결제 안내 표 1") == 1
    assert body.count("네이버페이 간편결제 안내 표 2") == 1
    # 본문 내용은 그대로 실린다.
    assert "해당 목차는 [AI 에이전트용] 이므로" in body
    assert "카드번호, 유효기간, CVC" in body
    assert "※ (2023년 9월 15일부터) 신세계 제휴카드는 이용 가능" in body
