"""청크 텍스트 정규화(facade/chunking/text_norm.py) 단위 테스트.

정규화가 "의도한 문자만 바꾼다"를 단정문으로 고정한다. 본문 전문은 출력하지 않는다.
text_norm 자체는 docling 의존이 없으므로 importorskip 없이 항상 실행된다.
"""

import pytest

from genon.preprocessor.facade.chunking import text_norm as tn

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 모드 해석
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        (None, tn.MODE_OFF),
        (False, tn.MODE_OFF),          # yaml 의 `off` 는 PyYAML 이 bool False 로 읽는다
        (True, tn.MODE_SAFE),
        ("off", tn.MODE_OFF),
        ("safe", tn.MODE_SAFE),
        ("SAFE", tn.MODE_SAFE),
        ("on", tn.MODE_SAFE),
        ("정체불명", tn.MODE_OFF),      # 오타는 fallback
    ],
)
def test_resolve_mode(value, expected):
    assert tn.resolve_mode(value) == expected


def test_resolve_mode_unknown_uses_given_fallback():
    assert tn.resolve_mode("오타", tn.MODE_SAFE) == tn.MODE_SAFE


def test_is_enabled():
    assert tn.is_enabled("safe") is True
    assert tn.is_enabled("off") is False


# ---------------------------------------------------------------------------
# sanitize: 문자 위생
# ---------------------------------------------------------------------------

def test_nfc_recomposes_decomposed_hangul():
    decomposed = "한글"   # NFD "한글"
    assert tn.sanitize(decomposed) == "한글"


def test_removes_zero_width_and_bom():
    assert tn.sanitize("﻿한​국‍어­") == "한국어"


def test_special_spaces_become_plain_space():
    assert tn.sanitize("A B C　D") == "A B C D"


def test_control_chars_removed_but_tab_and_newline_kept():
    assert tn.sanitize("a\x00b\x07c\td\ne") == "abc\td\ne"


def test_crlf_normalized_to_lf():
    assert tn.sanitize("a\r\nb\rc") == "a\nb\nc"


def test_fullwidth_alnum_to_halfwidth():
    assert tn.sanitize("Ａ１ｚ") == "A1z"


def test_quotes_and_dashes_unified():
    assert tn.sanitize("“인용” ‘값’ 3–5") == '"인용" \'값\' 3-5'


def test_nfkc_only_symbols_are_preserved():
    """NFKC 일괄 변환은 하지 않는다 — 기호가 분해되면 의미가 훼손된다."""
    assert tn.sanitize("㈜한국 5㎡ ½") == "㈜한국 5㎡ ½"


def test_line_structure_is_not_changed_by_sanitize():
    text = "a  \n\n\n  b"
    assert tn.sanitize(text) == text


def test_code_fence_is_protected_from_ascii_mapping():
    text = "본문 “값”\n```\ncode = “x” - １\n```\n"
    result = tn.sanitize(text)
    assert '본문 "값"' in result
    assert 'code = “x” - １' in result


def test_inline_code_is_protected():
    assert tn.sanitize("`a–b` c–d") == "`a–b` c-d"


def test_zero_width_removed_even_inside_code():
    """문자 위생은 코드 안에서도 안전하므로 전 구간에 적용한다."""
    assert tn.sanitize("`a​b`") == "`ab`"


def test_sanitize_passthrough_for_non_string():
    assert tn.sanitize(None) is None
    assert tn.sanitize("") == ""


# ---------------------------------------------------------------------------
# tidy: 표현 정리
# ---------------------------------------------------------------------------

def test_tidy_strips_trailing_whitespace_and_collapses_blank_lines():
    assert tn.tidy("a   \n\n\n\nb\t\n") == "a\n\nb"


def test_tidy_keeps_single_blank_line():
    assert tn.tidy("a\n\nb") == "a\n\nb"


def test_tidy_preserves_code_fence_interior():
    text = "설명\n```\nx = 1\n\n\n\ny = 2   \n```\n"
    result = tn.tidy(text)
    assert "x = 1\n\n\n\ny = 2   " in result


def test_tidy_preserves_markdown_table_rows():
    text = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert tn.tidy(text) == text


def test_tidy_preserves_html_table_markup():
    text = "<table><tr><td>값</td></tr></table>"
    assert tn.tidy(text) == text


def test_tidy_preserves_header_line():
    text = "HEADER: 제1장 > 제1조\n본문"
    assert tn.tidy(text) == text


def test_is_blank():
    assert tn.is_blank("") is True
    assert tn.is_blank("   \n\t ") is True
    assert tn.is_blank("a") is False


# ---------------------------------------------------------------------------
# element / metadata 정규화
# ---------------------------------------------------------------------------

def test_sanitize_elements_normalizes_content_and_metadata():
    elements = [
        {
            "content": "﻿질​문",
            "metadata": {"question": "１번", "nested": {"v": "a b"}, "n": 3},
        },
        "문자열은 건너뛴다",
    ]
    result = tn.sanitize_elements(elements)
    assert result[0]["content"] == "질문"
    assert result[0]["metadata"]["question"] == "1번"
    assert result[0]["metadata"]["nested"]["v"] == "a b"
    assert result[0]["metadata"]["n"] == 3


def test_sanitize_document_handles_none_and_bad_object():
    """duck typing 실패 시 예외 없이 통과해야 한다(파이프라인을 죽이지 않는다)."""
    tn.sanitize_document(None)

    class NoIterate:
        pass

    tn.sanitize_document(NoIterate())


def test_sanitize_document_mutates_text_items_and_table_cells():
    class Item:
        def __init__(self, text):
            self.text = text
            self.orig = text

    class Cell:
        def __init__(self, text):
            self.text = text

    class Data:
        def __init__(self, cells):
            self.table_cells = cells

    class Table:
        def __init__(self, cells):
            self.data = Data(cells)

    item = Item("﻿본​문")
    cell = Cell("Ａ")

    class Doc:
        def iterate_items(self):
            return [(item, 0), (Table([cell]), 0)]

    tn.sanitize_document(Doc())
    assert item.text == "본문"
    assert item.orig == "본문"
    assert cell.text == "A"


# ---------------------------------------------------------------------------
# processor 공용 헬퍼
# ---------------------------------------------------------------------------

def test_mode_for_priority_kwargs_over_yaml():
    assert tn.mode_for({"text_cleanup": "safe"}, "off") == tn.MODE_SAFE
    assert tn.mode_for({"text_cleanup": "off"}, "safe") == tn.MODE_OFF
    assert tn.mode_for({}, "safe") == tn.MODE_SAFE
    assert tn.mode_for(None, None) == tn.MODE_OFF


def test_mode_for_kwargs_typo_falls_back_to_yaml():
    assert tn.mode_for({"text_cleanup": "saef"}, "safe") == tn.MODE_SAFE
    assert tn.mode_for({"text_cleanup": "saef"}, "off") == tn.MODE_OFF


def test_drop_blank_chunks_supports_both_attrs():
    class Chunk:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    docling_like = [Chunk(text="a"), Chunk(text="   "), Chunk(text="b")]
    assert len(tn.drop_blank_chunks(docling_like)) == 2

    langchain_like = [Chunk(page_content="a"), Chunk(page_content="")]
    assert len(tn.drop_blank_chunks(langchain_like, "page_content")) == 1


def test_prepare_document_returns_flag_and_skips_when_off():
    class Item:
        def __init__(self):
            self.text = "﻿a"

    item = Item()

    class Doc:
        def iterate_items(self):
            return [(item, 0)]

    assert tn.prepare_document(Doc(), {}, "off") is False
    assert item.text == "﻿a"
    assert tn.prepare_document(Doc(), {}, "safe") is True
    assert item.text == "a"


def test_sanitize_langchain_docs():
    class Doc:
        def __init__(self, content):
            self.page_content = content

    docs = [Doc("﻿한​글"), Doc(""), Doc(None)]
    tn.sanitize_langchain_docs(docs)
    assert docs[0].page_content == "한글"


def test_mode_from_cfg():
    assert tn.mode_from_cfg({"text_cleanup": "safe"}) == tn.MODE_SAFE
    assert tn.mode_from_cfg({"text_cleanup": False}) == tn.MODE_OFF   # yaml `off`
    assert tn.mode_from_cfg({}) == tn.MODE_OFF
    assert tn.mode_from_cfg(None) == tn.MODE_OFF


def test_mode_of_accepts_processor_like_owner():
    class Proc:
        _text_cleanup = "safe"

    class StubWithoutInit:
        """object.__new__ 로 만든 인스턴스처럼 속성이 없는 경우."""

    assert tn.mode_of(Proc()) == tn.MODE_SAFE
    assert tn.mode_of(StubWithoutInit()) == tn.MODE_OFF
    assert tn.enabled_for({}, StubWithoutInit()) is False
    assert tn.enabled_for({"text_cleanup": "safe"}, StubWithoutInit()) is True
