"""PromptTemplate 단위 테스트 — 변수 치환 / escape / strict·lenient / doc_context.

순수 로직(processing.enrichment.prompt_template)만 import 한다.
"""
import logging
from unittest.mock import MagicMock

import pytest

from processing.enrichment.prompt_template import (
    PromptTemplate,
    RESERVED_VAR_NAMES,
    DOC_RESERVED,
    ITEM_RESERVED,
)


@pytest.mark.unit
class TestRender:
    @pytest.mark.parametrize("template,kwargs,expected", [
        # 이중 중괄호 토큰 치환
        ("hi {{raw_text}}", {"raw_text": "X"}, "hi X"),
        # 네 겹은 리터럴 중괄호로 이스케이프
        ("{{{{literal}}}}", {}, "{{literal}}"),
        # single-brace JSON 은 토큰이 아니므로 그대로 통과 (str.format KeyError 회피)
        ('result {{raw_text}} fmt {"created_date": "Y"}', {"raw_text": "DOC"},
         'result DOC fmt {"created_date": "Y"}'),
        # 치환된 값 안의 {{table_count}} 는 재확장되지 않아야 한다
        ("{{raw_text}}", {"raw_text": "{{table_count}}", "table_count": "99"}, "{{table_count}}"),
        # 토큰 안쪽 공백은 무시된다
        ("[{{ raw_text }}]", {"raw_text": "Z"}, "[Z]"),
    ], ids=["substitute", "escape", "json-body", "no-re-expansion", "inner-whitespace"])
    def test_render(self, template, kwargs, expected):
        assert PromptTemplate(template, allowed_names=[]).render(**kwargs) == expected

    def test_referenced_excludes_escaped(self):
        t = PromptTemplate("{{{{filename}}}} {{page_count}}")
        assert t.referenced == frozenset({"page_count"})

    def test_known_token_without_value_is_empty(self):
        t = PromptTemplate("[{{filename}}]")  # reserved, 값 미주입
        assert t.render() == "[]"


@pytest.mark.unit
class TestStrictLenient:
    def test_strict_unknown_raises_at_construction(self):
        with pytest.raises(ValueError):
            PromptTemplate("{{foo}}")  # reserved 도 user-defined 도 아님

    def test_strict_allows_user_defined(self):
        t = PromptTemplate("{{company}}", allowed_names=["company"])
        assert t.render(company="GenON") == "GenON"

    def test_strict_allows_reserved(self):
        # reserved 는 자동 허용
        t = PromptTemplate("{{filename}} {{page_count}}")
        assert t.referenced == frozenset({"filename", "page_count"})

    def test_lenient_unknown_renders_empty_with_warning(self, caplog):
        t = PromptTemplate("[{{foo}}]", mode="lenient")
        with caplog.at_level(logging.WARNING):
            out = t.render()
        assert out == "[]"
        assert any("foo" in r.message for r in caplog.records)


@pytest.mark.unit
class TestSingleBraceShim:
    def test_single_brace_substituted_with_deprecation(self, caplog):
        t = PromptTemplate("{raw_text}", allowed_names=[])
        with caplog.at_level(logging.WARNING):
            out = t.render(raw_text="VAL")
        assert out == "VAL"
        assert any("deprecated" in r.message.lower() for r in caplog.records)

    def test_single_brace_only_for_supplied_keys(self):
        # 값이 주입되지 않은 키의 단일 중괄호는 JSON 처럼 그대로 보존
        t = PromptTemplate("{{raw_text}} {other}", allowed_names=[])
        assert t.render(raw_text="A") == "A {other}"


@pytest.mark.unit
class TestDocContext:
    def _doc(self):
        doc = MagicMock()
        doc.origin.filename = "report.pdf"
        doc.origin.mimetype = "application/pdf"
        doc.num_pages.return_value = 7
        doc.tables = [1, 2, 3]
        doc.pictures = [1, 1]
        h1 = MagicMock(); h1.label = MagicMock(value="section_header"); h1.text = "1장"
        body = MagicMock(); body.label = MagicMock(value="text"); body.text = "본문"
        h2 = MagicMock(); h2.label = MagicMock(value="title"); h2.text = "제목"
        doc.texts = [h1, body, h2]
        doc.export_to_text.return_value = "FULL TEXT"
        return doc

    def test_extracts_reserved(self):
        ctx = PromptTemplate.doc_context(self._doc())
        assert ctx["filename"] == "report.pdf"
        assert ctx["mimetype"] == "application/pdf"
        assert ctx["page_count"] == 7
        assert ctx["table_count"] == 3
        assert ctx["picture_count"] == 2
        assert ctx["section_headers"] == "1장\n제목"
        assert ctx["full_text"] == "FULL TEXT"

    def test_overrides_win(self):
        ctx = PromptTemplate.doc_context(self._doc(), filename="OVERRIDE", raw_text="RT")
        assert ctx["filename"] == "OVERRIDE"
        assert ctx["raw_text"] == "RT"

    def test_needed_limits_computation(self):
        doc = self._doc()
        ctx = PromptTemplate.doc_context(doc, needed={"filename"})
        assert ctx == {"filename": "report.pdf"}
        # full_text 는 needed 에 없으므로 export_to_text 호출 안 됨
        doc.export_to_text.assert_not_called()

    def test_render_with_doc_context(self):
        t = PromptTemplate("{{filename}} p{{page_count}} t{{table_count}}")
        ctx = PromptTemplate.doc_context(self._doc(), needed=t.referenced)
        assert t.render(**ctx) == "report.pdf p7 t3"


@pytest.mark.unit
def test_reserved_catalog_membership():
    assert "raw_text" in DOC_RESERVED
    assert "before_context" in ITEM_RESERVED
    assert ITEM_RESERVED <= RESERVED_VAR_NAMES
    assert DOC_RESERVED <= RESERVED_VAR_NAMES
