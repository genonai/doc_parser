"""md 마커 heading 승격 단위 테스트.

판정 규칙은 html_flatten 과 공유한다 — 같은 원문이 html 로 오든 md 로 오든 같은 자리에서
쪼개져야 한다. 여기서는 md 에만 있는 문맥 조건(코드펜스·목록·표·기존 heading)을 고정한다.
"""
import pytest

from genon.preprocessor.converters.md_marker_headings import (
    promote_markdown_marker_headings,
)

pytestmark = pytest.mark.unit


_SECTIONS = (
    "# 안내\n\n"
    "◈ 시행일자\n2026년 7월 1일부터.\n\n"
    "◈ 대상카드\n전 상품.\n\n"
    "◈ 기본내용\n이용 가능.\n\n"
    "▣ 이용방법\n앱에서 신청.\n\n"
    "▣ 참고사항\n일부 제외.\n"
)


def test_promotes_markers_and_assigns_levels_by_first_appearance():
    promoted, count = promote_markdown_marker_headings(_SECTIONS)
    assert count == 5
    # 먼저 등장한 마커가 상위 레벨(html_flatten._marker_levels 와 같은 규칙)
    assert "## ◈ 시행일자" in promoted
    assert "### ▣ 이용방법" in promoted
    # 마커 문자는 제거하지 않는다 — 청커 breadcrumb 에 원문 그대로 실린다
    assert "◈ 시행일자" in promoted
    assert promoted.startswith("# 안내")


@pytest.mark.parametrize(
    "text",
    [
        "```\n◈ 하나\n◈ 둘\n◈ 셋\n```\n",          # 코드펜스 안
        "## 소제목1\n## 소제목2\n◈ 하나\n◈ 둘\n◈ 셋\n",  # 이미 하위 heading 으로 계층 표현
        "◈ 하나\n◈ 둘\n",                            # 후보가 최소 개수 미만
        "본문에 마커가 없다.\n",
    ],
)
def test_gates_leave_text_untouched(text):
    promoted, count = promote_markdown_marker_headings(text)
    assert count == 0
    assert promoted == text


def test_structure_lines_and_sentences_are_not_promoted():
    """목록·표·서술형·장식선은 제외된다(html 경로와 같은 강등 규칙)."""
    text = (
        "◈ 이 문장은 서술형으로 끝나므로 제목이 아닙니다.\n"
        "■■■\n"
        "- ◈ 목록 항목\n"
        "| ◈ 표 셀 |\n"
        "> ◈ 인용문\n"
        "◈ 진짜제목하나\n"
        "◈ 진짜제목둘\n"
        "◈ 진짜제목셋\n"
    )
    promoted, count = promote_markdown_marker_headings(text)
    assert count == 3
    assert "- ◈ 목록 항목" in promoted        # 목록 구조는 그대로
    assert "| ◈ 표 셀 |" in promoted          # 표 행도 그대로
    assert "## ◈ 진짜제목하나" in promoted
