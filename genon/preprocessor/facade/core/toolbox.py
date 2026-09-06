"""고객이 훅에서 쓰는 기존 기능 모음 (#363 08-3).

facade/preprocessor.py 의 pre_source / post_parse / pre_chunk / post_chunk 안에서
쓸 만한 것을 한 곳으로 모아 재수출한다. 여기 있는 것은 전부 **이미 있던 기능**이고
새 구현은 없다 — 고객이 모듈 배치를 몰라도 쓸 수 있게 하는 것이 목적이다.

    from facade.core import toolbox as tb
    tb.regex_sub(v, pattern=r"\\D", repl="")

가장 값이 큰 것은 **값 변환기**다. `custom_field_*.yaml` 의 `transform:` 이 부르는 것과
**같은 함수**라, 설정으로 하던 변환과 코드로 하는 변환이 어긋나지 않는다.
"""

from __future__ import annotations

# ── 값 변환 — yaml 의 transform: 과 같은 함수 ────────────────────────────────
from genon.preprocessor.facade.enrichment.field_transforms import (
    json_to_markdown,                       # JSON -> 청킹 친화 마크다운(## 섹션 자동)
    strip_inline_html,                      # 인라인 태그만 제거
    transform_date_int as date_int,         # 날짜 -> YYYYMMDD 정수
    transform_date_int_flex as date_int_flex,   # 비표준 날짜 표기까지
    transform_html_text as html_text,       # HTML 강제 평문화(html_renderer= 로 표 렌더 주입)
    transform_regex_extract as regex_extract,
    transform_regex_sub as regex_sub,
    transform_text as text,                 # JSON/HTML/평문 자동 판별 평문화
    transform_text_norm as text_norm,       # 공백·문장부호 정규화
    transform_to_int as to_int,
    transform_truncate as truncate,
)

# ── 엑셀·CSV ────────────────────────────────────────────────────────────────
from genon.preprocessor.converters.xlsx_processor import (
    load_sheets,      # 파일 -> {시트명: 2차원 행}. 병합셀은 펴진 상태
    load_tables,      # 헤더 자동판정까지 끝낸 표 블록 목록
)

# ── JSON 본문 ───────────────────────────────────────────────────────────────
from genon.preprocessor.converters.json_text import (
    collect_text_fields,     # payload 에서 본문 키를 (라벨, 값) 으로 수집
    detect_format,           # 값이 html/markdown/평문 중 무엇인지
)

# ── 표 직렬화 ───────────────────────────────────────────────────────────────
from genon.preprocessor.facade.chunking.table_html import (
    render_plain_text,       # 표를 표기 없는 평문으로
    render_table,            # 표를 HTML 로
    sanitize_table_html,     # 표 HTML 에서 불필요한 마크업 제거
)

# ── 청크 텍스트 정제 ────────────────────────────────────────────────────────
from genon.preprocessor.facade.chunking.text_norm import (
    sanitize,   # 문자 위생(제어문자·전각 등)
    tidy,       # 표현 정리(연속 공백·빈 줄)
)

# ── 마크다운·HTML 원문 손질 ─────────────────────────────────────────────────
from genon.preprocessor.converters.md_marker_headings import (
    promote_markdown_marker_headings,   # 도형 마커 줄을 섹션 헤더로 승격
)
from genon.preprocessor.converters.md_text_fence import transform as _unfence
from genon.preprocessor.converters.html_flatten import (
    marker_heading_match,   # 한 줄이 마커 소제목인지
    precheck_html,          # docling 이 놓칠 구조 결함 사전검사
)


def unfence_text(text: str, **kw) -> str:
    """레이아웃 보존용 ```text 펜스를 마크다운 단락으로 되돌린다.

    안 하면 펜스 본문 전체가 CodeItem 하나가 되어 chunk_size 가 무의미해진다.
    원본 함수는 (텍스트, 변환 수) 를 돌려주는데 훅에서는 텍스트만 쓰면 된다.
    """
    return _unfence(text, **kw)[0]


# ── 판정 헬퍼 ───────────────────────────────────────────────────────────────
from genon.preprocessor.facade.enrichment.custom_fields_enricher import normalize_doc_type
from genon.preprocessor.facade.common.appendix import check_appendix_keywords
from genon.preprocessor.facade.common.file_probe import (
    is_encrypted_pdf,
    is_protected_hwp,
    read_text_with_fallback,   # utf-8-sig -> utf-8 -> cp949
)

__all__ = [
    "json_to_markdown", "strip_inline_html", "date_int", "date_int_flex", "html_text",
    "regex_extract", "regex_sub", "text", "text_norm", "to_int", "truncate",
    "load_sheets", "load_tables",
    "collect_text_fields", "detect_format",
    "render_plain_text", "render_table", "sanitize_table_html",
    "sanitize", "tidy",
    "promote_markdown_marker_headings", "unfence_text",
    "marker_heading_match", "precheck_html",
    "normalize_doc_type", "check_appendix_keywords",
    "is_encrypted_pdf", "is_protected_hwp", "read_text_with_fallback",
]
