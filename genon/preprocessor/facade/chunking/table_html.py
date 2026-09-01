"""청크 텍스트에 실을 표를 최소 HTML 로 렌더한다.

docling_core 의 HTML serializer 는 화면 렌더링이 목적이라, 셀 내용이 문서 트리의 별도
서브트리로 존재하는 rich cell 을 만나면 그 서브트리를 통째로 직렬화한다. 그래서
``TableItem.export_to_html()`` 결과에 ``<p>``, ``<ul><li>``, ``<span class='inline-group'>``,
``<a href>`` 가 섞여 나오고 그대로 청크 텍스트에 실렸다(실측 — 상품/고객센터 카드).

검색 색인에 필요한 것은 표의 격자 구조뿐이다. 여기서는 ``table``/``caption``/``tbody``/
``tr``/``th``/``td`` 와 ``colspan``/``rowspan`` 만 남기고, 셀 값은 docling 백엔드가 이미
만들어 둔 평문(``cell.text``)을 쓴다. 백엔드의 ``get_text()`` 가 ``p``/``li``/``th``/``td``
뒤에 공백을 넣으므로 셀 안 여러 항목은 공백 한 칸으로 이어진다.

이 모듈은 docling 타입을 import 하지 않는다. grid 셀에서 공통 속성만 duck typing 으로 읽는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from genon.preprocessor.facade.chunking.table_shape import (
    cell_at as _cell_at,
    is_header_cell as _is_header_cell,
)

# markdown 표 직렬화 공용 옵션. 청크 텍스트도 LLM/임베딩 입력이라 markdown 이스케이프와
# 이미지 자리표시자는 노이즈다(`facade/enrichment/json_records._MD_EXPORT_OPTS` 와 같은 값).
MD_TABLE_PARAMS = {
    "image_placeholder": "",
    "escape_html": False,
    "escape_underscores": False,
    # 셀 안 링크는 표시 문구만 남긴다. `[예약하기](http://...)` 의 URL 은 검색에 기여하지
    # 않으면서 청크 예산만 먹는다 - 정제 HTML 경로(render_table)도 URL 을 버린다.
    "include_hyperlinks": False,
}

# 정제 후 남기는 태그와 속성. 그 밖은 벗겨 내되 내용은 보존한다.
ALLOWED_TAGS = ("table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td")
ALLOWED_ATTRS = ("colspan", "rowspan")

_WS = re.compile(r"\s+")
_EMPTY_ROW = re.compile(r"<(?:th|td)[^>]*></(?:th|td)>")
# 셀이 전부 빈 markdown 표 행. 구분선(`| - | - |`)은 `-` 가 있어 걸리지 않는다.
_BLANK_MD_ROW = re.compile(r"^\|(?:\s*\|)+\s*$")


def escape_cell(value: Any) -> str:
    """셀 값을 한 줄 평문으로 만들고 HTML 텍스트 노드로 안전하게 만든다.

    ``&`` 와 ``<`` 만 이스케이프한다. ``>`` 는 HTML5 텍스트 노드에서 이스케이프할 의무가
    없는데 ``html.escape`` 는 그것까지 바꿔 청크 텍스트에 ``&gt;`` 노이즈를 남겼다
    (실측 — `일반결제 &gt; 삼성페이`). 따옴표도 같은 이유로 건드리지 않는다.
    """
    text = _WS.sub(" ", str(value or "")).strip()
    return text.replace("&", "&amp;").replace("<", "&lt;")


def render_row(row: Sequence[Any], num_cols: int, *, row_index: int | None = None) -> str:
    """grid 한 행을 ``<tr>`` 로 렌더한다.

    ``row_index`` 를 주면 rowspan 을 인식한다 — docling grid 는 병합 셀을 피복 위치마다
    복제해 두므로, 시작 행에서만 ``rowspan`` 속성과 함께 내고 이어지는 행에서는 건너뛴다.
    주지 않으면(표 분할 경로처럼 이미 rowspan 을 푼 grid) 모든 셀을 그대로 낸다.
    """
    cells: list[str] = []
    for column in range(num_cols):
        cell = _cell_at(row, column)
        if cell is None:
            cells.append("<td></td>")
            continue
        # colspan 으로 복제된 grid cell 은 시작 컬럼에서만 렌더한다.
        if getattr(cell, "start_col_offset_idx", column) != column:
            continue
        col_span = _span(cell, "col_span")
        row_span = _span(cell, "row_span")
        attrs = f' colspan="{col_span}"' if col_span > 1 else ""
        if row_index is not None:
            # rowspan 으로 복제된 행은 시작 행에서만 렌더한다.
            if getattr(cell, "start_row_offset_idx", row_index) != row_index:
                continue
            if row_span > 1:
                attrs += f' rowspan="{row_span}"'
        tag = "th" if _is_header_cell(cell) else "td"
        cells.append(f"<{tag}{attrs}>{escape_cell(getattr(cell, 'text', ''))}</{tag}>")
    return "<tr>" + "".join(cells) + "</tr>"


def render_table(
    grid: Sequence[Sequence[Any]] | None,
    num_cols: Any,
    *,
    caption: str = "",
) -> str:
    """grid 전체를 정제 HTML 표 한 덩어리로 렌더한다. 렌더할 수 없으면 빈 문자열."""
    try:
        cols = int(num_cols or 0)
    except (TypeError, ValueError):
        cols = 0
    if not grid:
        return ""
    if cols <= 0:
        cols = max((len(row) for row in grid), default=0)
    if cols <= 0:
        return ""

    rendered = [render_row(row, cols, row_index=index) for index, row in enumerate(grid)]
    # 값이 하나도 없는 행은 버린다. 이미지만 든 셀처럼 평문이 비는 셀이 모인 행이 그렇다.
    rows = "".join(row for row in rendered if _EMPTY_ROW.sub("", row) != "<tr></tr>")
    if not rows:
        return ""
    caption_html = f"<caption>{escape_cell(caption)}</caption>" if caption else ""
    return f"<table>{caption_html}<tbody>{rows}</tbody></table>"


def sanitize_table_html(html_text: str) -> str:
    """이미 만들어진 표 HTML 에서 허용 태그·속성만 남긴다.

    grid 를 거치지 않고 HTML 문자열로 들어오는 경로(표 refine 재구성 결과)를 위한 것이다.
    허용되지 않은 태그는 벗기되 내용은 남기고, 태그 경계는 공백 한 칸으로 대신한다.
    파싱할 수 없으면 원문을 그대로 돌려준다 — 내용 손실보다 노이즈가 낫다.
    """
    if not html_text or "<" not in html_text:
        return html_text
    try:
        from bs4 import BeautifulSoup, Comment
        from bs4.element import Tag
    except ImportError:
        return html_text
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()
        for tag in soup.find_all(True):
            if not isinstance(tag, Tag):
                continue
            if tag.name not in ALLOWED_TAGS:
                tag.insert_before(" ")
                tag.insert_after(" ")
                tag.unwrap()
                continue
            tag.attrs = {
                key: value for key, value in tag.attrs.items() if key in ALLOWED_ATTRS
            }
        return _WS.sub(" ", str(soup)).replace("> <", "><").strip()
    except Exception:
        return html_text


def _span(cell: Any, name: str) -> int:
    try:
        return max(int(getattr(cell, name, 1) or 1), 1)
    except (TypeError, ValueError):
        return 1


def drop_blank_markdown_rows(text: str) -> str:
    """markdown 표에서 값이 하나도 없는 행을 버린다.

    이미지만 든 셀은 평문이 비어 ``|  |  |`` 한 줄로 남는다. 정보가 없는데 표를 읽는 쪽에는
    행이 하나 더 있는 것으로 보인다.
    """
    if not text or "|" not in text:
        return text
    kept = [line for line in text.split("\n") if not _BLANK_MD_ROW.match(line)]
    return "\n".join(kept)
