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

from genon.preprocessor.facade.common.markdown_export import (
    MD_PLAIN_TEXT_OPTS as _MD_PLAIN_TEXT_OPTS,
)
from genon.preprocessor.facade.chunking.table_shape import (
    cell_at as _cell_at,
    cell_text as _cell_text,
    degenerate_reason as _degenerate_reason,
    is_header_cell as _is_header_cell,
)

# markdown 표 직렬화 공용 옵션. 본문 텍스트 쪽과 같은 값을 써야 하므로 공용 모듈을 가리키는
# 별칭만 남긴다(`facade/common/markdown_export.MD_PLAIN_TEXT_OPTS`).
# 셀 안 링크는 표시 문구만 남긴다 - 정제 HTML 경로(render_table)도 URL 을 버린다.
MD_TABLE_PARAMS = _MD_PLAIN_TEXT_OPTS

# 정제 후 남기는 태그와 속성. 그 밖은 벗겨 내되 내용은 보존한다.
ALLOWED_TAGS = ("table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td")
ALLOWED_ATTRS = ("colspan", "rowspan")

_WS = re.compile(r"\s+")
_EMPTY_ROW = re.compile(r"<(?:th|td)[^>]*></(?:th|td)>")
# 셀이 전부 빈 markdown 표 행. 구분선(`| - | - |`)은 `-` 가 있어 걸리지 않는다.
_BLANK_MD_ROW = re.compile(r"^\|(?:\s*\|)+\s*$")
# markdown 표 구분선. `| - | - |` 과 `| --- | :--: |` 을 모두 받는다.
_MD_DELIM_ROW = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$")
# 표 런의 한 줄. 앞 공백을 허용한다(들여쓴 표).
_MD_ANY_ROW = re.compile(r"^\s*\|")


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


def render_plain_text(
    grid: Sequence[Sequence[Any]] | None,
    num_cols: Any = 0,
    *,
    caption: str = "",
) -> str:
    """표기 없는 평문으로 렌더한다. 낼 값이 없으면 빈 문자열.

    ``render_table`` 과 시그니처를 맞췄다 - 호출부가 같은 자리에서 두 렌더러를 갈아 끼운다.
    레이아웃용 표(`table_shape.degenerate_reason`)에만 쓴다.

    행마다 셀 값을 순서대로 이어 한 줄로 만들고, 연속 중복은 버린다. colspan 셀은 grid 에서
    피복 열마다 복제돼 있어 그대로 내면 산문이 열 수만큼 중복된다(실측 - `colspan="4"` 안내
    배너가 4번 반복). 같은 이유로 rowspan 이 만든 복제 행도 버린다.

    산출물에 ``|`` 도 ``<table`` 도 없으므로 ``table_blocks`` 가 다시 표로 잡지 않는다. 즉
    같은 텍스트에 두 번 걸어도 결과가 같다.
    """
    if not grid:
        return caption.strip() if caption else ""

    lines: list[str] = []
    for row in grid:
        values: list[str] = []
        for text in map(_cell_text, row):
            if text and (not values or values[-1] != text):
                values.append(text)
        line = " ".join(values).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)

    head = str(caption or "").strip()
    if head and (not lines or lines[0] != head):
        lines.insert(0, head)
    elif head and not lines:
        lines = [head]
    return "\n".join(lines)


def render_degenerate(table_data: Any, *, caption: str = "") -> str:
    """표 데이터가 레이아웃용이면 평문, 아니면 빈 문자열.

    `data.grid` / `data.num_cols` 만 duck typing 으로 읽는다. 청킹 경로 두 곳
    (`smart_chunker`, `hybrid_chunker`)이 같은 한 벌을 쓰게 하려고 여기에 둔다.
    """
    grid = getattr(table_data, "grid", None)
    num_cols = getattr(table_data, "num_cols", 0)
    if not _degenerate_reason(grid, num_cols):
        return ""
    return render_plain_text(grid, num_cols, caption=caption)


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


def unpipe_markdown_row(line: str) -> str:
    """markdown 표 행 한 줄을 파이프 없는 평문으로 만든다.

    셀 구분자를 없애고 셀 값만 공백으로 잇는다. 이스케이프된 파이프는 되돌린다.
    구분자는 ``render_plain_text`` 와 같게 맞춘다 - 두 경로의 산출이 갈리지 않게 한다.
    """
    values: list[str] = []
    for cell in str(line or "").strip().strip("|").split("|"):
        cell = cell.strip()
        # 연속 중복은 버린다 - colspan 셀이 열마다 복제된 것이다(render_plain_text 와 같은 규칙).
        if cell and (not values or values[-1] != cell):
            values.append(cell)
    return " ".join(values).replace("\\|", "|").replace("&#124;", "|").strip()


def _tidy_markdown_run(run: list[str]) -> list[str]:
    """표 런(파이프 줄 연속 구간) 하나를 정리한다.

    1. 값이 하나도 없는 행을 버린다. 이미지만 든 셀은 평문이 비어 ``|  |  |`` 한 줄로 남는데,
       정보가 없는데 표를 읽는 쪽에는 행이 하나 더 있는 것으로 보인다.
    2. 그 결과 구분선이 맨 앞에 오면(딸려 있던 헤더 행을 버린 경우) 한 줄 아래로 내린다.
       구분선이 첫 줄인 표는 markdown 으로 읽히지 않는다(실측 - md_sample2 의 1열 표).
       내릴 자리가 없으면 구분선을 버린다.
    3. 1·2 를 거치고 내용 행이 하나만 남았으면 표로 표현하는 관계가 없다. 구분선을 버리고
       남은 행을 평문 한 줄로 낸다.

    3 은 **빈 행을 실제로 버린 런에만** 적용한다. 원래부터 한 행짜리인 표는 여기서 손대지
    않는다 - 문자열만 봐서는 "헤더만 있는 표"와 "헤더 없는 데이터 한 줄"을 가를 수 없고,
    그 판정은 격자 구조를 가진 쪽(`table_shape.degenerate_reason`)의 몫이다.
    """
    kept = [line for line in run if not _BLANK_MD_ROW.match(line.strip())]
    if not kept:
        return []
    dropped_blank = len(kept) != len(run)
    if _MD_DELIM_ROW.match(kept[0].strip()):
        delim = kept.pop(0)
        if kept:
            kept.insert(1, delim)
    content = [line for line in kept if not _MD_DELIM_ROW.match(line.strip())]
    if dropped_blank and len(content) <= 1:
        return [unpipe_markdown_row(line) for line in content if unpipe_markdown_row(line)]
    return kept


def drop_blank_markdown_rows(text: str) -> str:
    """markdown 표에서 값 없는 행을 버리고, 그 자리에 구분선이 고아로 남지 않게 한다.

    표 런 단위로 본다 - 줄 하나만 보면 "구분선이 딸린 헤더 행을 버렸다"를 알 수 없다.
    표 밖 줄은 손대지 않는다.
    """
    if not text or "|" not in text:
        return text
    out: list[str] = []
    run: list[str] = []
    for line in text.split("\n"):
        if _MD_ANY_ROW.match(line):
            run.append(line)
            continue
        if run:
            out.extend(_tidy_markdown_run(run))
            run = []
        out.append(line)
    if run:
        out.extend(_tidy_markdown_run(run))
    return "\n".join(out)
