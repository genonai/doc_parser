"""HTML/Markdown 표를 행 경계에서 나누는 공통 유틸리티.

이 모듈은 Docling 타입을 직접 import하지 않는다. processor가 가진 TableItem의
``data.grid``를 넘기면 셀의 공통 속성만 duck typing으로 읽는다. 덕분에 facade별
설정/annotation 처리와 분리된 순수 청킹 로직으로 재사용할 수 있다.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

TableFormat = Literal["html", "markdown"]
CountText = Callable[[str], int]


@dataclass(frozen=True)
class TableSplitResult:
    """행 단위 표 분할 결과와 fallback 사유."""

    pieces: list[str]
    did_split: bool
    reason: str | None = None
    oversized_piece_indexes: tuple[int, ...] = ()


def _is_header_cell(cell: Any) -> bool:
    return bool(
        getattr(cell, "column_header", False)
        or getattr(cell, "row_header", False)
        or getattr(cell, "row_section", False)
    )


def leading_header_row_count(grid: Sequence[Sequence[Any]]) -> int:
    """선두에서 연속되는 Docling 헤더 플래그 행 수를 반환한다."""

    count = 0
    for row in grid:
        if any(_is_header_cell(cell) for cell in row):
            count += 1
        else:
            break
    return count


def _cell_at(row: Sequence[Any], column: int) -> Any | None:
    return row[column] if column < len(row) else None


def _render_html_row(row: Sequence[Any], num_cols: int) -> str:
    cells: list[str] = []
    for column in range(num_cols):
        cell = _cell_at(row, column)
        if cell is None:
            cells.append("<td></td>")
            continue
        # colspan으로 복제된 grid cell은 시작 컬럼에서만 렌더한다.
        if getattr(cell, "start_col_offset_idx", column) != column:
            continue
        tag = "th" if _is_header_cell(cell) else "td"
        col_span = max(int(getattr(cell, "col_span", 1) or 1), 1)
        attrs = f' colspan="{col_span}"' if col_span > 1 else ""
        value = html.escape(str(getattr(cell, "text", "") or "").strip())
        cells.append(f"<{tag}{attrs}>{value}</{tag}>")
    return "<tr>" + "".join(cells) + "</tr>"


def _render_markdown_row(row: Sequence[Any], num_cols: int) -> str:
    cells: list[str] = []
    for column in range(num_cols):
        cell = _cell_at(row, column)
        value = str(getattr(cell, "text", "") or "").strip()
        value = value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
        cells.append(value)
    return "| " + " | ".join(cells) + " |"


def split_table_rows(
    *,
    grid: Sequence[Sequence[Any]],
    num_cols: int,
    single_text: str,
    limit: int,
    count_text: CountText,
    table_format: TableFormat = "html",
    header_row_count: int = 1,
    prefix: str = "",
    suffix: str = "",
) -> TableSplitResult:
    """표를 행 경계에서 분할하고 각 조각을 독립적인 완전한 표로 만든다.

    분할할 수 없는 구조(rowspan, 데이터 행 없음)는 ``single_text``를 그대로
    반환한다. 단일 데이터 행 자체가 한도를 넘으면 행/태그를 깨지 않고 초과
    조각으로 유지하며 ``oversized_piece_indexes``로 이를 알린다.
    """

    if limit <= 0 or count_text(single_text) <= limit:
        return TableSplitResult([single_text], False)
    if not grid or num_cols <= 0:
        return TableSplitResult([single_text], False, "empty-grid")

    header_row_count = max(int(header_row_count), 0)
    if header_row_count >= len(grid):
        return TableSplitResult([single_text], False, "no-data-rows")

    header_rows = list(grid[:header_row_count])
    data_rows = list(grid[header_row_count:])
    if any(
        int(getattr(cell, "row_span", 1) or 1) > 1
        for row in data_rows
        for cell in row
    ):
        return TableSplitResult([single_text], False, "rowspan")

    if table_format == "markdown":
        render_row = _render_markdown_row
        header_lines = [render_row(row, num_cols) for row in header_rows]
        # Markdown 표는 헤더가 최소 한 행 필요하다. header_row_count=0은 첫 데이터
        # 행을 헤더로 승격하지 않고 안전하게 원문 유지한다.
        if not header_lines:
            return TableSplitResult([single_text], False, "no-markdown-header")
        header_lines.append("| " + " | ".join(["---"] * num_cols) + " |")

        def wrap(rows: Sequence[str], trailing: str = "") -> str:
            body = "\n".join(header_lines + list(rows))
            return prefix + body + trailing

    else:
        render_row = _render_html_row
        header_html = "".join(render_row(row, num_cols) for row in header_rows)

        def wrap(rows: Sequence[str], trailing: str = "") -> str:
            return prefix + "<table><tbody>" + header_html + "".join(rows) + "</tbody></table>" + trailing

    rendered_rows = [render_row(row, num_cols) for row in data_rows]
    buckets: list[list[str]] = []
    current: list[str] = []
    last_index = len(rendered_rows) - 1
    for index, rendered in enumerate(rendered_rows):
        trailing = suffix if index == last_index else ""
        candidate = [*current, rendered]
        if current and count_text(wrap(candidate, trailing)) > limit:
            buckets.append(current)
            current = [rendered]
        else:
            current = candidate
    if current:
        buckets.append(current)

    pieces = [
        wrap(bucket, suffix if index == len(buckets) - 1 else "")
        for index, bucket in enumerate(buckets)
    ]
    if not pieces:
        return TableSplitResult([single_text], False, "no-pieces")
    oversized = tuple(index for index, piece in enumerate(pieces) if count_text(piece) > limit)
    return TableSplitResult(pieces, len(pieces) > 1, None, oversized)


def split_entries_preserving_tables(
    *,
    item_groups: Iterable[Iterable[Any]],
    budget: int,
    is_table_entry: Callable[[Any], bool],
    render_entries: Callable[[Sequence[Any]], str],
    count_text: CountText,
    split_plain_text: Callable[[str, int], Sequence[str]],
    split_table_entry: Callable[[Any, int], Sequence[str]],
) -> list[tuple[str, list[Any]]] | None:
    """atomic item 그룹을 분할하되 표 entry의 직렬화 중간은 자르지 않는다.

    표가 하나도 없으면 ``None``을 반환하여 호출부가 기존 분할 정책을 유지할 수
    있게 한다. 반환 entry 목록은 processor가 metadata/doc_items를 복원하는 데 쓴다.
    """

    groups = [list(group) for group in item_groups]
    if not any(is_table_entry(entry) for group in groups for entry in group):
        return None

    result: list[tuple[str, list[Any]]] = []
    pending: list[Any] = []

    def flush_pending() -> None:
        if not pending:
            return
        entries = list(pending)
        text = render_entries(entries)
        result.extend((piece, entries) for piece in split_plain_text(text, budget) if piece)
        pending.clear()

    for group in groups:
        for entry in group:
            if is_table_entry(entry):
                table_text = render_entries([entry])
                if count_text(table_text) > budget:
                    flush_pending()
                    result.extend((piece, [entry]) for piece in split_table_entry(entry, budget) if piece)
                    continue

            candidate = [*pending, entry]
            if pending and count_text(render_entries(candidate)) > budget:
                flush_pending()
            pending.append(entry)
            if count_text(render_entries(pending)) > budget:
                flush_pending()

    flush_pending()
    return result
