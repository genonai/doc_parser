"""텍스트 원천이 PDF 를 거치지 않고 docling 으로 파싱되는지 고정.

두 가지를 잠근다.

  1단계 — parser 의 TextLoader 는 A4 렌더를 하지 않는다(RENDER_PDF=False).
          렌더 경로는 파생 PDF 를 입력 파일 옆에 남기고 요청이 끝나도 지우지 않았다.
          attachment 는 페이지 메타가 필요하므로 기본값(True)을 유지해야 한다.

  2단계 — 평문은 `<pre>` HTML 로 감싸 docling 에 태우고, docling 이 붙인 코드 라벨을
          일반 텍스트로 되돌린다. `<pre>` 는 줄바꿈·들여쓰기를 보존하는 유일한 표현이라
          감싸는 방식은 유지하고 라벨만 보정한다.

LLM 도 모델서버도 부르지 않는다 — HTML 백엔드는 로컬 파싱이다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ── 1단계: 렌더 스위치 ──────────────────────────────────────────────────────

def test_parser_textloader_does_not_render_pdf():
    """parser 의 TextLoader 는 렌더를 끈다. attachment 는 켜 둔 채로 남는다."""
    from genon.preprocessor.processing.common.loaders import TextLoaderBase
    from genon.preprocessor.processing.core.parser import TextLoader

    assert TextLoader.RENDER_PDF is False
    assert TextLoaderBase.RENDER_PDF is True, "attachment 는 페이지 메타가 필요하다"


def test_textloader_without_render_leaves_no_sibling_pdf(tmp_path: Path):
    """렌더를 끄면 입력 파일 옆에 파생 PDF 가 생기지 않고, 원문이 그대로 나온다."""
    from genon.preprocessor.processing.common.loaders import TextLoaderBase

    src = tmp_path / "notice.json"
    src.write_text('{"body": "line-1\\nline-2"}', encoding="utf-8")

    class NoRender(TextLoaderBase):
        RENDER_PDF = False

    docs = NoRender(str(src)).load()

    assert len(docs) == 1
    assert docs[0].page_content == '{"body": "line-1\\nline-2"}'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notice.json"]


# ── 2단계: 텍스트 → HTML → docling ─────────────────────────────────────────

def test_text_to_html_escapes_and_preserves_whitespace():
    """`<`/`&` 는 이스케이프하고, 자동 줄바꿈 스타일을 유지한다(이슈 #333)."""
    from genon.preprocessor.converters.plain_text import text_to_html

    out = text_to_html("a < b & c\n  들여쓰기")

    assert "a &lt; b &amp; c" in out
    assert "white-space: pre-wrap" in out
    assert "overflow-wrap: anywhere" in out
    assert "  들여쓰기" in out


def test_demote_code_items_turns_pre_block_into_text():
    """`<pre>` 를 docling 이 코드로 읽은 것을 일반 텍스트로 되돌린다.

    라벨만 바뀌고 본문·참조는 그대로여야 한다 — CodeItem.label 이 Literal 이라
    아이템 자체를 교체하므로 self_ref 보존이 회귀 지점이다.
    """
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import CodeItem, DocItemLabel

    from genon.preprocessor.converters.plain_text import text_to_html
    from genon.preprocessor.processing.common import docling_ops as dops

    source = "제목: 결제 취소\n  - 환불: 3~5영업일"

    import tempfile

    with tempfile.TemporaryDirectory() as work_dir:
        html_path = Path(work_dir) / "x.html"
        html_path.write_text(text_to_html(source), encoding="utf-8")
        doc = DocumentConverter().convert(str(html_path), raises_on_error=True).document

    # docling 은 <pre> 를 코드 블록으로 읽는다 — 이 전제가 깨지면 보정도 불필요해진다.
    assert any(isinstance(item, CodeItem) for item in doc.texts)
    refs_before = [item.self_ref for item in doc.texts]

    dops.demote_code_items(doc)

    assert not any(isinstance(item, CodeItem) for item in doc.texts)
    assert [item.label for item in doc.texts] == [DocItemLabel.TEXT]
    assert [item.self_ref for item in doc.texts] == refs_before
    # 줄바꿈과 들여쓰기가 원문 그대로 남는다(렌더 왕복이 없으므로).
    assert doc.texts[0].text == source
    # 코드 라벨이 남아 있으면 마크다운 출력이 ``` 펜스로 감싼다.
    assert "```" not in doc.export_to_markdown()
