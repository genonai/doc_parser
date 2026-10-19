"""HTML/Markdown 표를 행 경계에서 나누는 공통 유틸리티.

이 모듈은 Docling 타입을 직접 import하지 않는다. processor가 가진 TableItem의
``data.grid``를 넘기면 셀의 공통 속성만 duck typing으로 읽는다. 덕분에 facade별
설정/annotation 처리와 분리된 순수 청킹 로직으로 재사용할 수 있다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from genon.preprocessor.processing.chunking.table_html import render_row as _render_html_row
from genon.preprocessor.processing.chunking.table_shape import (
    TableFormat,
    cell_at as _cell_at,
    cell_text as _cell_text,
    cell_with_text,
    flatten_header_rows,
    is_colspan_copy,
    leading_header_row_count,
    normalize_row_spans,
    row_has_label,
    serialize_rows,
)

CountText = Callable[[str], int]

# 행 문장 블록의 머리말. 표 격자와 시각적으로 구분되어야 LLM 이 중복으로 읽지 않는다.
ROW_LINES_LABEL = "[표 행 요약]"

# 긴 셀을 나눠 앞 조각의 행을 이어 쓰는 조각의 머리말. 완전한 원본 행으로 읽히지 않게 한다.
ROW_CONTINUED_LABEL = "[앞 조각에서 이어지는 행]"

# 긴 셀을 자를 경계. 앞쪽이 우선하며, 어느 경계도 쓸 수 없으면 문자 위치에서 자른다.
_CUT_BOUNDARIES = (
    re.compile(r"\n+"),
    # 문장 끝. `1.` 같은 항목 번호 뒤에서는 자르지 않는다.
    re.compile(r"(?<=[^\d\s][.!?。])\s+"),
    re.compile(r"\s+"),
)


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
    # 긴 셀을 나눠 이어 쓴 행은 이어지는 조각마다 같은 인덱스로 반복된다.
    buckets: tuple[tuple[int, ...], ...] = ()


def _escape_markdown_cell(value: str) -> str:
    """파이프와 줄바꿈은 표 구조를 깨므로 셀 안에서 무해하게 만든다."""
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _render_markdown_row(row: Sequence[Any], num_cols: int) -> str:
    # colspan 셀은 시작 컬럼에만 쓰고 피복 컬럼은 비운다. 열 수는 헤더와 맞아야 한다.
    cells: list[str] = []
    for column in range(num_cols):
        cell = _cell_at(row, column)
        cells.append("" if is_colspan_copy(cell, column) else _escape_markdown_cell(_cell_text(cell)))
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
            return "\n".join(header_lines + list(rows)) + trailing

        return _RowRenderer(_render_markdown_row, wrap_markdown)

    header_html = "".join(_render_html_row(row, num_cols) for row in header_rows)

    def wrap_html(rows: Sequence[str], trailing: str = "") -> str:
        return "<table><tbody>" + header_html + "".join(rows) + "</tbody></table>" + trailing

    return _RowRenderer(_render_html_row, wrap_html)


def _compose_with(
    renderer: _RowRenderer,
    head: str,
    rendered_rows: Sequence[str],
    row_lines: Sequence[str],
    trailing: str = "",
) -> str:
    """조각 하나의 최종 텍스트. 예산 판정과 실제 출력이 같은 함수를 쓴다.

    ``head`` 는 표 앞 접두, ``row_lines`` 는 그 조각 행들의 요약 줄(빈 문자열은 생략)이다.
    """
    text = head + renderer.wrap(rendered_rows, trailing)
    lines = [line for line in row_lines if line]
    return text + "\n" + ROW_LINES_LABEL + "\n" + "\n".join(lines) if lines else text


@dataclass(frozen=True)
class _Piece:
    """형식과 무관한 조각 계획. 모든 형식이 같은 계획을 렌더해야 조각 경계가 같다."""

    # 이 조각이 담는 데이터 행 인덱스. 긴 셀을 나눈 행은 이어지는 조각마다 같은 인덱스다.
    indexes: tuple[int, ...]
    # 렌더할 데이터 행. 긴 셀을 나눈 조각에서는 셀 일부만 담은 부분 행이다.
    rows: tuple[Any, ...]
    # 행별 요약 줄. 빈 문자열은 그 행의 요약을 싣지 않는다(row_serialization 이 꺼지면 빈 튜플).
    lines: tuple[str, ...]
    trailing: str = ""
    # 반복 검색 설명을 뺀 접두를 쓴다.
    compact: bool = False
    # 앞 조각의 행을 이어 쓴다.
    continued: bool = False


def _best_cut(text: str, fits: Callable[[int], bool]) -> int:
    """``text[:cut]`` 이 예산에 드는 자를 위치. 한 글자도 들지 않으면 0.

    완성 문자열 길이는 자르는 위치에 대해 단조 증가하므로, 문자 단위 최대 위치를 이분
    탐색으로 구한 뒤 그 안에서 문단·문장·공백 경계를 차례로 찾는다. 경계가 최대 위치의
    절반보다 앞에만 있으면 조각이 지나치게 잘게 나뉘므로 다음 경계 종류로 넘어간다.
    """
    if len(text) < 2 or not fits(1):
        return 0
    low, high = 1, len(text) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    for pattern in _CUT_BOUNDARIES:
        ends = [match.end() for match in pattern.finditer(text) if match.end() <= low]
        if ends and ends[-1] * 2 >= low:
            return ends[-1]
    return low


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
    compact_prefix: str | None = None,
    split_long_cells: bool = False,
) -> TableSplitResult:
    """표를 행 경계에서 분할하고 각 조각을 독립적인 완전한 표로 만든다.

    데이터 행에 rowspan 이 걸려 있으면 그 값을 각 행에 복제한 뒤 분할한다(원본 grid 는
    건드리지 않는다). 예전에는 여기서 분할을 포기해 chunk_size 를 크게 넘는 청크가
    그대로 나갔고, 임베딩 입력 한도에서 잘려 검색이 실패했다.

    분할할 수 없는 구조(데이터 행 없음, 빈 grid)는 ``single_text``를 그대로 반환한다.

    행 하나만 담은 조각이 한도를 넘으면 그 조각에만 아래를 차례로 적용하고, 한도 안에
    드는 순간 멈춘다. 한도 안에 든 조각은 바꾸지 않는다.

    1. 라벨이 하나도 없어 표 행의 값을 되풀이할 뿐인 행 요약 줄을 뺀다.
    2. ``compact_prefix`` 를 주면 접두를 그것으로 바꾼다(조각마다 반복되는 검색 설명을
       뺀 접두). 표의 첫 조각은 바꾸지 않아 설명이 표에 한 번은 남는다.
    3. ``split_long_cells`` 가 참이면 긴 셀을 경계에서 나눠 그 행을 여러 조각에 이어 쓴다.
       조각마다 표 헤더를 다시 붙이고, 이어지는 조각 앞에 ``ROW_CONTINUED_LABEL`` 을 둔다.
       조각들의 셀 값을 이어 붙이면 원래 셀 값이 된다(경계의 공백은 렌더에서 접힌다).

    표 헤더와 접두만으로 한도를 넘어 셀을 나눠도 진전이 없으면 행 구조를 보존한 초과
    조각으로 유지하고 ``oversized_piece_indexes``로 이를 알린다. 긴 헤더 조항은 자르지 않는다.

    ``row_serialization``이 참이면 각 조각의 표 뒤에 그 조각의 행을 ``컬럼=값`` 문장으로
    덧붙인다. 병합 셀 표에서 어느 헤더에 걸린 값인지 임베딩에 드러내기 위한 것이다.

    ``extra_formats``를 주면 같은 조각 계획으로 그 형식들의 조각도 만들어 ``format_pieces``에
    담는다. 조각 계획은 ``table_format``으로 한 번만 세우고 형식마다 재사용한다 -
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
    # 값이 하나도 없는 데이터 행은 분할 단위에서 뺀다. 단일 청크 경로는 같은 행을
    # drop_blank_markdown_rows 로 지우는데 여기서 남기면, 표 끝의 빈 행이 홀로 조각이 되어
    # 표 설명 접두와 빈 표만 담긴 청크가 나온다. 또 serialize_rows 는 빈 행을 건너뛰므로
    # 남겨 두면 행 문장 목록과 행 인덱스가 어긋난다.
    data_rows = [
        row for row in grid[header_row_count:]
        if any(_cell_text(cell) for cell in row)
    ]

    header_labels = flatten_header_rows(grid, header_row_count, num_cols)

    renderer = _build_renderer(
        table_format=table_format, header_rows=header_rows,
        header_labels=header_labels, num_cols=num_cols,
    )
    if renderer is None:
        return TableSplitResult([single_text], False, "no-markdown-header")

    rendered_rows = [renderer.render_row(row, num_cols) for row in data_rows]
    # 행 문장은 표기형태와 무관하다. 한 번 만들어 모든 형식이 공유한다. 하위 헤더 없는
    # 병합 헤더 셀은 표 헤더에 한 번 남으므로 행 요약 라벨에서는 뺀다.
    line_labels = flatten_header_rows(grid, header_row_count, num_cols, keep_merged_leaf=False)
    row_lines = serialize_rows(data_rows, line_labels, num_cols) if row_serialization else []

    def compose(indexes: Sequence[int], trailing: str = "") -> str:
        return _compose_with(
            renderer, prefix, [rendered_rows[i] for i in indexes],
            [row_lines[i] for i in indexes if i < len(row_lines)], trailing)

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
    if not buckets:
        return TableSplitResult([single_text], False, "no-pieces")

    if compact_prefix is None:
        compact_prefix = prefix

    def render(target: _RowRenderer, piece: _Piece) -> str:
        head = compact_prefix if piece.compact else prefix
        if piece.continued:
            head += ROW_CONTINUED_LABEL + "\n"
        return _compose_with(
            target, head, [target.render_row(row, num_cols) for row in piece.rows],
            piece.lines, piece.trailing)

    def fits(piece: _Piece) -> bool:
        return count_text(render(renderer, piece)) <= limit

    def line_of(row: Sequence[Any]) -> str:
        # 라벨 없는 요약 줄은 표 행의 값을 되풀이할 뿐이다. 넘치는 조각에서는 싣지 않는다.
        if not row_has_label(row, line_labels, num_cols):
            return ""
        return "\n".join(serialize_rows([row], line_labels, num_cols))

    def split_row(piece: _Piece) -> list[_Piece] | None:
        """행 하나짜리 조각의 긴 셀을 나눠 여러 조각으로 이어 쓴다. 진전이 없으면 None."""
        row = piece.rows[0]
        remaining: dict[int, str] = {}
        for column in range(num_cols):
            cell = _cell_at(row, column)
            if cell is not None and not is_colspan_copy(cell, column) and _cell_text(cell):
                remaining[column] = _cell_text(cell)

        def sub_piece(segments: dict[int, str], compact: bool, continued: bool) -> _Piece:
            # colspan 피복 위치도 시작 컬럼과 같은 사본을 가리켜야 렌더러가 한 번만 쓴다.
            copies: dict[int, Any] = {}
            cells = []
            for column, cell in enumerate(row):
                owner = getattr(cell, "start_col_offset_idx", column) if cell is not None else column
                if owner not in remaining:
                    cells.append(cell)
                    continue
                if owner not in copies:
                    copies[owner] = cell_with_text(cell, segments.get(owner, ""), owner)
                cells.append(copies[owner])
            lines = (line_of(cells),) if piece.lines else ()
            # 마지막 조각이 어느 것일지 미리 알 수 없으므로 접미 몫은 모든 조각에서 예약한다.
            return _Piece(piece.indexes, (cells,), lines, piece.trailing, compact, continued)

        pieces: list[_Piece] = []
        compact = piece.compact
        while any(text for text in remaining.values()):
            continued = bool(pieces)
            compact = compact or continued
            segments: dict[int, str] = {}
            for column, text in remaining.items():
                if not text:
                    continue
                if fits(sub_piece({**segments, column: text}, compact, continued)):
                    segments[column] = text
                    continue
                cut = _best_cut(text, lambda end: fits(
                    sub_piece({**segments, column: text[:end]}, compact, continued)))
                if cut:
                    segments[column] = text[:cut]
                break
            if not segments:
                # 표의 첫 조각이 검색 설명 때문에 한 글자도 담지 못하면 설명을 빼고 다시 해 본다.
                if not compact and compact_prefix != prefix:
                    compact = True
                    continue
                return None
            for column, segment in segments.items():
                remaining[column] = remaining[column][len(segment):].lstrip()
            pieces.append(replace(sub_piece(segments, compact, continued), trailing=""))
        pieces[-1] = replace(pieces[-1], trailing=piece.trailing)
        return pieces

    def fit(piece: _Piece, first: bool) -> list[_Piece]:
        """한도를 넘는 조각을 정해진 순서로 줄인다. 한도 안에 드는 순간 멈춘다."""
        if piece.lines:
            piece = replace(piece, lines=tuple(
                line if row_has_label(data_rows[i], line_labels, num_cols) else ""
                for i, line in zip(piece.indexes, piece.lines)))
            if fits(piece):
                return [piece]
        if not first and compact_prefix != prefix:
            piece = replace(piece, compact=True)
            if fits(piece):
                return [piece]
        if split_long_cells and len(piece.rows) == 1:
            return split_row(piece) or [piece]
        return [piece]

    plan: list[_Piece] = []
    for position, bucket in enumerate(buckets):
        piece = _Piece(
            tuple(bucket), tuple(data_rows[i] for i in bucket),
            tuple(row_lines[i] for i in bucket if i < len(row_lines)),
            suffix if position == len(buckets) - 1 else "")
        plan.extend([piece] if fits(piece) else fit(piece, position == 0))

    pieces = [render(renderer, piece) for piece in plan]
    oversized = tuple(index for index, piece in enumerate(pieces) if count_text(piece) > limit)

    # 추가 형식은 같은 조각 계획을 그대로 쓴다. 예산 재판정도 재분할도 하지 않는다.
    format_pieces: dict[str, list[str]] = {}
    for fmt in dict.fromkeys(extra_formats):
        if fmt == table_format:
            format_pieces[fmt] = list(pieces)
            continue
        alt = _build_renderer(
            table_format=fmt, header_rows=header_rows,
            header_labels=header_labels, num_cols=num_cols,
        )
        if alt is None:
            continue
        format_pieces[fmt] = [render(alt, piece) for piece in plan]

    return TableSplitResult(
        pieces, len(pieces) > 1, None, oversized, normalized_spans=normalized_spans,
        format_pieces=format_pieces,
        buckets=tuple(piece.indexes for piece in plan))


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
