"""
이슈 #333 회귀 테스트 — TextLoader 긴 한 줄 잘림 방지.

배경:
  txt/json/md 는 TextLoader.load() 가 원문을 <pre> 로 감싸 weasyprint 로 PDF 변환한 뒤
  그 PDF 에서 텍스트를 추출해 청킹한다. <pre> 기본값(white-space: pre)은 자동 줄바꿈을
  하지 않아, A4 폭을 넘는 긴 줄이 렌더 단계에서 잘려(discard) PDF·청킹에서 누락됐다.
  수정: <pre> 에 white-space: pre-wrap; overflow-wrap: anywhere 적용 + content html.escape.

검증 경로:
  TextLoader.load() 는 weasyprint(HTML) 가 있어야 PDF 경로를 탄다. 없으면 원문을 그대로
  Document 로 반환(잘림 없음)하므로 이 회귀를 재현하지 못한다 → weasyprint 없으면 skip.
"""
from __future__ import annotations

from pathlib import Path
import sys
import pytest


def _import_processor():
    try:
        from facade.attachment_processor import _get_pdf_path, TextLoader
        return _get_pdf_path, TextLoader
    except ModuleNotFoundError:
        # 실행 루트에 따라 sys.path 보정 (기존 테스트와 동일 패턴)
        sys.path.append(str(Path(__file__).resolve().parents[3]))
        from facade.attachment_processor import _get_pdf_path, TextLoader
        return _get_pdf_path, TextLoader


def _has_weasyprint() -> bool:
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False


def _norm(s: str) -> str:
    """추출 텍스트 비교용: 모든 공백/개행 제거.

    weasyprint 가 wrap 하면서 삽입한 개행이나 PyMuPDF 추출 시 생기는 공백이
    토큰 중간에 끼어도(overflow-wrap 로 토큰이 쪼개질 수 있음) 원문 대조가 되도록 정규화.
    """
    return "".join(s.split())


def _extract_text(loader) -> str:
    docs = loader.load()
    return "".join(getattr(d, "page_content", "") or "" for d in docs)


requires_weasyprint = pytest.mark.skipif(
    not _has_weasyprint(), reason="weasyprint 미설치로 PDF 경로 회귀 검증 스킵"
)


@pytest.mark.unit
@requires_weasyprint
def test_long_single_line_txt_not_truncated(tmp_path: Path):
    """줄바꿈 없는 긴 한 줄의 끝 텍스트가 청킹 결과에서 누락되지 않아야 한다(이슈 #333)."""
    _get_pdf_path, TextLoader = _import_processor()

    # A4 폭을 확실히 넘기는 긴 한 줄 + 문장 끝 고유 토큰.
    # 수정 전에는 끝 토큰이 페이지 밖으로 밀려 렌더/추출에서 누락됨.
    head = "교정시설 수용관리 업무 안내 " + ("가나다라마바사아자차카타파하 " * 40)
    tail_token = "문장끝고유표식TAIL333"
    content = head + tail_token  # 개행 없는 단일 라인

    txt = tmp_path / "long_line.txt"
    txt.write_text(content, encoding="utf-8")

    extracted = _extract_text(TextLoader(str(txt)))

    # PDF 가 실제로 생성됐는지도 확인(경로가 폴백으로 새지 않았음을 보증)
    assert Path(_get_pdf_path(str(txt))).exists()
    assert _norm(tail_token) in _norm(extracted), (
        "긴 한 줄의 끝 토큰이 추출 텍스트에서 누락됨 — <pre> wrap 미적용 회귀"
    )


@pytest.mark.unit
@requires_weasyprint
def test_html_special_chars_preserved(tmp_path: Path):
    """<, & 등 HTML 특수문자가 태그로 해석돼 뒤 텍스트가 유실되지 않아야 한다(html.escape)."""
    _get_pdf_path, TextLoader = _import_processor()

    # escape 없으면 '<b>' 이후 '중요' 는 태그로 먹히고, 'a<b 그리고 ... ' 도 유실됨.
    content = "머리말 <b>중요구절</b> 그리고 조건 a<b 이고 기호 & 앰퍼샌드 끝토큰ESCAPE333"

    txt = tmp_path / "special_chars.txt"
    txt.write_text(content, encoding="utf-8")

    norm = _norm(_extract_text(TextLoader(str(txt))))

    for token in ("중요구절", "앰퍼샌드", "끝토큰ESCAPE333"):
        assert _norm(token) in norm, f"escape 누락으로 '{token}' 이 유실됨"


@pytest.mark.unit
@requires_weasyprint
def test_long_single_line_json_not_truncated(tmp_path: Path):
    """json 도 TextLoader 경로를 타므로 동일하게 끝 텍스트가 보존돼야 한다."""
    _get_pdf_path, TextLoader = _import_processor()

    long_val = ("동일한긴문자열반복 " * 50).strip()
    tail_token = "제이슨끝표식JSON333"
    content = '{"field": "' + long_val + " " + tail_token + '"}'  # 한 줄 json

    jf = tmp_path / "long_line.json"
    jf.write_text(content, encoding="utf-8")

    extracted = _extract_text(TextLoader(str(jf)))
    assert _norm(tail_token) in _norm(extracted)
