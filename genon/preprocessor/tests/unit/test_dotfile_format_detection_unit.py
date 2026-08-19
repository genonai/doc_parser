"""
점으로 시작하는 파일명의 포맷 판정 회귀 테스트 (이슈 #349).

모니모 카드 고객센터(ADCC) 원천은 `.INC_235488_02_20260626103138.html` 처럼 파일명이 점으로
시작하고, 본문은 `<!DOCTYPE>`/`<html>` 없이 `<section data-subj-seq=…>` 로 시작하는 fragment 다.
이 두 조건이 겹치면 docling 이 포맷을 판정하지 못해 `ConversionError: File format not allowed`
로 죽었다.

- 확장자 판정: `_guess_format` 의 `DocumentStream` 분기가 `not name.startswith(".")` 로 걸러
  선행점 파일명의 확장자를 무시했다 → 이쪽을 `PurePath(name).suffix` 로 교정했다.
- 내용 판정: `_detect_html_xhtml` 는 `<!doctype html|<html|<head|<body` 만 매치하므로 fragment
  에서는 원래부터 None 이다(상류 동작, 손대지 않음).

그래서 테스트는 반드시 **`DocumentStream` 분기**를 태워야 한다. genon 의 facade 들이
`converter.convert(file_path)` 에 str 을 넘기고 docling 이 이를 DocumentStream 으로 바꾸기
때문이다. `Path` 분기는 `obj.suffix` 를 써서 원래부터 정상이라 회귀를 잡지 못한다.
"""
from __future__ import annotations

from io import BytesIO

import pytest


# 원천과 같은 형태의 fragment — <!DOCTYPE>/<html>/<body> 가 없어 내용 판정으로는 구제되지 않는다.
FRAGMENT_HTML = (
    '<section data-subj-seq="665" data-level="1"><h1>[AI 에이전트용]</h1>'
    '<div class="parag" data-parag-id="6606"><div class="se-contents">'
    '<style type="text/css" data-publishing-style="true">.se-contents { line-height: 1.5; }</style>'
    '<table class="__se_tbl" style="box-sizing: revert; font-size: revert; color: revert;">'
    "<tbody><tr><td>문서ID</td><td>CS-HPP-0231</td></tr></tbody></table>"
    "</div></div></section>"
)


def _stream(name: str, content: str):
    from docling.datamodel.base_models import DocumentStream

    return DocumentStream(name=name, stream=BytesIO(content.encode("utf-8")))


def _guess(name: str, content: str):
    from docling.datamodel.document import _DocumentConversionInput

    conv_input = _DocumentConversionInput(path_or_stream_iterator=[])
    return conv_input._guess_format(_stream(name, content))


# ---------------------------------------------------------------------------
# 1. 이번 버그: 선행점 + fragment
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_dotfile_html_fragment_is_detected_as_html():
    """선행점 파일명 + fragment 본문이 HTML 로 판정된다 (수정 전에는 None 이었다)."""
    from docling.datamodel.base_models import InputFormat

    assert _guess(".INC_235488_02_20260626103138.html", FRAGMENT_HTML) == InputFormat.HTML


@pytest.mark.unit
def test_dotfile_markdown_is_detected_as_md():
    """확장자 판정 교정이므로 html 전용이 아니다 — .md 원천도 같이 살아난다."""
    from docling.datamodel.base_models import InputFormat

    assert _guess(".INC_235490_01_20260626103140.md", "# 제목\n\n본문입니다.") == InputFormat.MD


# ---------------------------------------------------------------------------
# 2. 상류 의도 보존 — 확장자 없는 dotfile 은 여전히 확장자로 판정하지 않는다
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("name", [".gitignore", ".env"])
def test_extensionless_dotfile_does_not_borrow_extension(name):
    """`.gitignore`/`.env` 는 확장자가 없다 — 이름에서 mime 을 얻어선 안 된다.

    상류가 `startswith(".")` 로 막으려던 대상이 이쪽이다. PurePath.suffix 는 ''를 주므로
    확장자 판정이 일어나지 않고 내용 판정으로 떨어진다(= 이 내용은 text/plain → None).
    """
    assert _guess(name, "node_modules\n__pycache__\n") is None


# ---------------------------------------------------------------------------
# 3. 일반 파일명은 동작이 바뀌지 않는다
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize(
    "name, content, expected_attr",
    [
        ("sample.html", "<!DOCTYPE html><html><body><p>본문</p></body></html>", "HTML"),
        ("archive.tar.html", FRAGMENT_HTML, "HTML"),   # 다중 확장자
        ("SAMPLE.HTML", FRAGMENT_HTML, "HTML"),        # 대문자 확장자
        ("sample.md", "# 제목\n\n본문", "MD"),
    ],
)
def test_ordinary_filenames_unchanged(name, content, expected_attr):
    from docling.datamodel.base_models import InputFormat

    assert _guess(name, content) == getattr(InputFormat, expected_attr)


@pytest.mark.unit
def test_extensionless_name_still_unresolved():
    """확장자도 없고 내용도 sniff 안 되는 이름은 그대로 판정 실패여야 한다."""
    assert _guess("noext", "그냥 평문입니다") is None


# ---------------------------------------------------------------------------
# 4. 왕복 — 실제 변환이 되살아나고 본문/표가 보존된다
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_dotfile_fragment_converts_and_keeps_table(tmp_path):
    """실패했던 입력이 실제로 변환되고, 제목·표가 살아 있고 CSS 가 본문으로 새지 않는다.

    HTML 은 stock HTMLDocumentBackend + SimplePipeline 이라 레이아웃/OCR 모델서버가 필요 없다.
    """
    from docling.document_converter import DocumentConverter

    src = tmp_path / ".INC_235488_02_20260626103138.html"
    src.write_text(FRAGMENT_HTML, encoding="utf-8")

    # facade 들과 같게 str 로 넘긴다 → docling 내부에서 DocumentStream 분기를 탄다.
    result = DocumentConverter().convert(str(src), raises_on_error=True)
    markdown = result.document.export_to_markdown()

    assert "[AI 에이전트용]" in markdown
    assert "CS-HPP-0231" in markdown
    assert len(result.document.tables) == 1
    # <style> 내용과 style 속성의 CSS 는 본문 텍스트로 새어나오면 안 된다.
    assert "revert" not in markdown
    assert "line-height" not in markdown
