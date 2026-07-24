"""
이슈 #333 회귀 테스트 — TextLoader 긴 한 줄 잘림 방지.

배경:
  txt/json/md 는 TextLoader.load() 가 원문을 <pre> 로 감싸 weasyprint 로 PDF 변환한 뒤
  그 PDF 에서 텍스트를 추출해 청킹한다. <pre> 기본값(white-space: pre)은 자동 줄바꿈을
  하지 않아, A4 폭을 넘는 긴 줄이 렌더 단계에서 잘려(discard) PDF·청킹에서 누락됐다.
  수정: <pre> 에 white-space: pre-wrap; overflow-wrap: anywhere 적용 + content html.escape.

주의:
  - 콘텐츠는 ASCII 로 구성한다. weasyprint→PDF→PyMuPDF round-trip 은 CJK 글리프에
    대응 폰트가 필요한데 CI 러너엔 한글 폰트가 없어(.notdef) 추출 텍스트가 깨진다.
    잘림/escape 버그는 언어 무관이므로 ASCII 로도 동일하게 재현·검증된다.
  - TextLoader.load() 는 weasyprint(HTML) 가 있어야 PDF 경로를 탄다. 없으면 원문을
    그대로 Document 로 반환(잘림 없음)해 회귀를 재현 못하므로 → weasyprint 없으면 skip.
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
    # ImportError = 패키지 미설치, OSError = 네이티브 라이브러리(pango/gobject 등) 로드 실패.
    # 프로덕션(attachment_processor)도 두 경우 모두 HTML=None 폴백을 타므로 skip 이 맞다.
    # 그 외 예기치 않은 예외는 표면화되도록 좁게 잡는다(Ruff BLE001).
    try:
        import weasyprint  # noqa: F401
        return True
    except (ImportError, OSError):
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
    """공백은 있지만 개행 없는 긴 한 줄의 끝 토큰이 누락되지 않아야 한다(white-space: pre-wrap)."""
    _get_pdf_path, TextLoader = _import_processor()

    # A4 폭을 확실히 넘기는 긴 한 줄(개행 없음) + 문장 끝 고유 토큰.
    # 수정 전(white-space: pre)에는 끝 토큰이 페이지 밖으로 밀려 렌더/추출에서 누락됨.
    content = "HEADSTART " + ("longword " * 200) + "ENDSENTINEL333"

    txt = tmp_path / "long_line.txt"
    txt.write_text(content, encoding="utf-8")

    extracted = _extract_text(TextLoader(str(txt)))

    # PDF 가 실제로 생성됐는지도 확인(경로가 폴백으로 새지 않았음을 보증)
    assert Path(_get_pdf_path(str(txt))).exists()
    assert "ENDSENTINEL333" in _norm(extracted), (
        "긴 한 줄의 끝 토큰이 추출 텍스트에서 누락됨 — white-space: pre-wrap 미적용 회귀"
    )


@pytest.mark.unit
@requires_weasyprint
def test_no_space_long_token_not_truncated(tmp_path: Path):
    """공백 없는 초장문(URL·연속 문자열 등)의 끝 토큰이 누락되지 않아야 한다(overflow-wrap: anywhere)."""
    _get_pdf_path, TextLoader = _import_processor()

    # 공백이 전혀 없는 한 덩어리 → pre-wrap 만으로는 안 쪼개짐, overflow-wrap: anywhere 필요.
    content = "P" + ("A" * 2000) + "NOSPACETAIL333"

    txt = tmp_path / "no_space.txt"
    txt.write_text(content, encoding="utf-8")

    extracted = _extract_text(TextLoader(str(txt)))
    assert "NOSPACETAIL333" in _norm(extracted), (
        "공백 없는 초장문의 끝 토큰이 누락됨 — overflow-wrap: anywhere 미적용 회귀"
    )


@pytest.mark.unit
@requires_weasyprint
def test_html_special_chars_preserved(tmp_path: Path):
    """<, & 등 HTML 특수문자가 태그로 해석돼 뒤 텍스트가 유실되지 않아야 한다(html.escape)."""
    _get_pdf_path, TextLoader = _import_processor()

    # escape 없으면 'a<b' 이 열린 태그로 해석돼 그 뒤 토큰이 통째로 유실됨.
    content = "PREFIXTOKEN a<b MIDDLEWORD AMPERSANDWORD & END TAILESCAPE333"

    txt = tmp_path / "special_chars.txt"
    txt.write_text(content, encoding="utf-8")

    norm = _norm(_extract_text(TextLoader(str(txt))))

    for token in ("AMPERSANDWORD", "TAILESCAPE333"):
        assert token in norm, f"escape 누락으로 '{token}' 이 유실됨"


@pytest.mark.unit
@requires_weasyprint
def test_long_single_line_json_not_truncated(tmp_path: Path):
    """json 도 TextLoader 경로를 타므로 동일하게 끝 텍스트가 보존돼야 한다."""
    _get_pdf_path, TextLoader = _import_processor()

    content = '{"field": "' + ("wordval " * 200) + 'JSONTAIL333"}'  # 한 줄 json

    jf = tmp_path / "long_line.json"
    jf.write_text(content, encoding="utf-8")

    extracted = _extract_text(TextLoader(str(jf)))
    assert "JSONTAIL333" in _norm(extracted)
