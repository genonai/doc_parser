"""HTML/Markdown 표를 행 경계에서 나누는 공통 유틸리티.

이 모듈은 Docling 타입을 직접 import하지 않는다. processor가 가진 TableItem의
``data.grid``를 넘기면 셀의 공통 속성만 duck typing으로 읽는다. 덕분에 facade별
설정/annotation 처리와 분리된 순수 청킹 로직으로 재사용할 수 있다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from genon.preprocessor.facade.chunking.table_html import render_row as _render_html_row
from genon.preprocessor.facade.chunking.table_shape import (
    TableFormat,
    cell_at as _cell_at,
    cell_text as _cell_text,
    flatten_header_rows,
    leading_header_row_count,
    normalize_row_spans,
    serialize_rows,
)

CountText = Callable[[str], int]

# 행 문장 블록의 머리말. 표 격자와 시각적으로 구분되어야 LLM 이 중복으로 읽지 않는다.
ROW_LINES_LABEL = "[표 행 요약]"


@dataclass(frozen=True)
class TableSplitResult:
    """행 단위 표 분할 결과와 fallback 사유."""

    pieces: list[str]
    did_split: bool
    reason: str | None = None
    oversized_piece_indexes: tuple[int, ...] = ()
    # 데이터 행의 rowspan 을 풀고 분할했는가. 조각 안에서 병합 값이 행마다 복제된다.
    normalized_spans: bool = False
    # ``extra_formats`` 로 요청한 형식별 조각. pieces 와 길이가 같고 행 경계도 같다.
    # 렌더할 수 없는 형식(markdown 헤더 행 부재 등)은 키가 없다.
    format_pieces: dict[str, list[str]] = field(default_factory=dict)
    # 조각별 데이터 행 인덱스 묶음(정규화된 grid 기준). 형식 간 경계 동일성 검증용.
    buckets: tuple[tuple[int, ...], ...] = ()


def _escape_markdown_cell(value: str) -> str:
    """파이프와 줄바꿈은 표 구조를 깨므로 셀 안에서 무해하게 만든다."""
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _render_markdown_row(row: Sequence[Any], num_cols: int) -> str:
    cells = [
        _escape_markdown_cell(_cell_text(_cell_at(row, column)))
        for column in range(num_cols)
    ]
    return "| " + " | ".join(cells) + " |"


@dataclass(frozen=True)
class _RowRenderer:
    """한 표기형태의 행 렌더러. render_row 와 wrap 이 짝이어야 조각이 완전한 표가 된다."""

    render_row: Callable[[Sequence[Any], int], str]
    wrap: Callable[[Sequence[str], str], str]


def _build_renderer(
    *,
    table_format: TableFormat,
    header_rows: Sequence[Sequence[Any]],
    header_labels: Sequence[str],
    num_cols: int,
    prefix: str,
) -> _RowRenderer | None:
    """형식별 렌더러. markdown 인데 헤더 행이 없으면 None(호출부가 폴백한다)."""

    if table_format == "markdown":
        # Markdown 표는 헤더가 정확히 한 행이고 그 다음 줄이 구분선이어야 한다. 헤더가
        # 여러 행이면 컬럼마다 `상위 > 하위` 로 합쳐 한 행으로 만든다 - 예전처럼 여러 행을
        # 그대로 늘어놓고 뒤에 구분선을 붙이면 표로 파싱되지 않는 텍스트가 나왔다.
        # header_row_count=0 은 첫 데이터 행을 헤더로 승격하지 않고 안전하게 원문 유지한다.
        if not header_rows:
            return None
        header_lines = [
            "| " + " | ".join(_escape_markdown_cell(label) for label in header_labels) + " |",
            "| " + " | ".join(["---"] * num_cols) + " |",
        ]

        def wrap_markdown(rows: Sequence[str], trailing: str = "") -> str:
            body = "\n".join(header_lines + list(rows))
            return prefix + body + trailing

        return _RowRenderer(_render_markdown_row, wrap_markdown)

    header_html = "".join(_render_html_row(row, num_cols) for row in header_rows)

    def wrap_html(rows: Sequence[str], trailing: str = "") -> str:
        return prefix + "<table><tbody>" + header_html + "".join(rows) + "</tbody></table>" + trailing

    return _RowRenderer(_render_html_row, wrap_html)


def _compose_with(
    renderer: _RowRenderer,
    rendered_rows: Sequence[str],
    row_lines: Sequence[str],
    indexes: Sequence[int],
    trailing: str = "",
) -> str:
    """조각 하나의 최종 텍스트. 예산 판정과 실제 출력이 같은 함수를 쓴다."""
    text = renderer.wrap([rendered_rows[i] for i in indexes], trailing)
    if not row_lines:
        return text
    lines = [row_lines[i] for i in indexes if i < len(row_lines) and row_lines[i]]
    return text + "\n" + ROW_LINES_LABEL + "\n" + "\n".join(lines) if lines else text


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
    row_serialization: bool = False,
    extra_formats: Sequence[TableFormat] = (),
) -> TableSplitResult:
    """표를 행 경계에서 분할하고 각 조각을 독립적인 완전한 표로 만든다.

    데이터 행에 rowspan 이 걸려 있으면 그 값을 각 행에 복제한 뒤 분할한다(원본 grid 는
    건드리지 않는다). 예전에는 여기서 분할을 포기해 chunk_size 를 크게 넘는 청크가
    그대로 나갔고, 임베딩 입력 한도에서 잘려 검색이 실패했다.

    분할할 수 없는 구조(데이터 행 없음, 빈 grid)는 ``single_text``를 그대로 반환한다.
    단일 데이터 행 자체가 한도를 넘으면 행/태그를 깨지 않고 초과 조각으로 유지하며
    ``oversized_piece_indexes``로 이를 알린다.

    ``row_serialization``이 참이면 각 조각의 표 뒤에 그 조각의 행을 ``컬럼=값`` 문장으로
    덧붙인다. 병합 셀 표에서 어느 헤더에 걸린 값인지 임베딩에 드러내기 위한 것이다.

    ``extra_formats``를 주면 같은 행 경계로 그 형식들의 조각도 만들어 ``format_pieces``에
    담는다. 행 묶음(buckets)은 ``table_format``으로 한 번만 계산하고 형식마다 재사용한다 -
    형식별로 이 함수를 따로 부르면 텍스트 길이가 달라 조각 경계와 개수가 어긋난다.
    그래서 추가 형식의 조각은 ``limit``을 넘을 수 있고, 재분할하지 않는다.
    """

    if limit <= 0 or count_text(single_text) <= limit:
        return TableSplitResult([single_text], False)
    if not grid or num_cols <= 0:
        return TableSplitResult([single_text], False, "empty-grid")

    header_row_count = max(int(header_row_count), 0)
    if header_row_count >= len(grid):
        return TableSplitResult([single_text], False, "no-data-rows")

    normalized_spans = any(
        int(getattr(cell, "row_span", 1) or 1) > 1
        for row in grid[header_row_count:]
        for cell in row
    )
    if normalized_spans:
        grid = normalize_row_spans(grid)
    header_rows = list(grid[:header_row_count])
    data_rows = list(grid[header_row_count:])

    header_labels = flatten_header_rows(grid, header_row_count, num_cols)

    renderer = _build_renderer(
        table_format=table_format, header_rows=header_rows,
        header_labels=header_labels, num_cols=num_cols, prefix=prefix,
    )
    if renderer is None:
        return TableSplitResult([single_text], False, "no-markdown-header")

    rendered_rows = [renderer.render_row(row, num_cols) for row in data_rows]
    # 행 문장은 표기형태와 무관하다. 한 번 만들어 모든 형식이 공유한다.
    row_lines = (
        serialize_rows(data_rows, header_labels, num_cols)
        if row_serialization else []
    )

    def compose(indexes: Sequence[int], trailing: str = "") -> str:
        return _compose_with(renderer, rendered_rows, row_lines, indexes, trailing)

    buckets: list[list[int]] = []
    current: list[int] = []
    last_index = len(rendered_rows) - 1
    for index in range(len(rendered_rows)):
        trailing = suffix if index == last_index else ""
        candidate = [*current, index]
        if current and count_text(compose(candidate, trailing)) > limit:
            buckets.append(current)
            current = [index]
        else:
            current = candidate
    if current:
        buckets.append(current)

    pieces = [
        compose(bucket, suffix if index == len(buckets) - 1 else "")
        for index, bucket in enumerate(buckets)
    ]
    if not pieces:
        return TableSplitResult([single_text], False, "no-pieces")
    oversized = tuple(index for index, piece in enumerate(pieces) if count_text(piece) > limit)

    # 추가 형식은 같은 buckets 를 그대로 쓴다. 예산 재판정도 재분할도 하지 않는다.
    format_pieces: dict[str, list[str]] = {}
    for fmt in dict.fromkeys(extra_formats):
        if fmt == table_format:
            format_pieces[fmt] = list(pieces)
            continue
        alt = _build_renderer(
            table_format=fmt, header_rows=header_rows,
            header_labels=header_labels, num_cols=num_cols, prefix=prefix,
        )
        if alt is None:
            continue
        alt_rows = [alt.render_row(row, num_cols) for row in data_rows]
        format_pieces[fmt] = [
            _compose_with(alt, alt_rows, row_lines, bucket,
                          suffix if index == len(buckets) - 1 else "")
            for index, bucket in enumerate(buckets)
        ]

    return TableSplitResult(
        pieces, len(pieces) > 1, None, oversized, normalized_spans=normalized_spans,
        format_pieces=format_pieces,
        buckets=tuple(tuple(bucket) for bucket in buckets))


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
