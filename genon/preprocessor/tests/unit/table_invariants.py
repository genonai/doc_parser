"""표 분할 결과가 지켜야 할 불변식 모음.

baseline 으로 출력 전문을 굳히지 않고 이 단정문들로 고정한다 — 포맷이 의도적으로
바뀌는 작업에서 전문 baseline 은 유지비만 크고, 정작 "무엇이 깨지면 안 되는가" 를
말해 주지 않는다.
"""

from __future__ import annotations

import re

from genon.preprocessor.facade.chunking.table_shape import cell_text, flatten_header_rows

_ROW_MARKER = re.compile(r"ROW-(\d{2})-(START|END)")


def assert_values_preserved(grid, pieces, *, header_row_count=0):
    """원본 셀 값이 하나도 사라지지 않았는가."""
    joined = "\n".join(pieces)
    missing = sorted({
        text for row in grid[header_row_count:] for cell in row
        if (text := cell_text(cell)) and text not in joined
    })
    assert not missing, f"분할에서 사라진 셀 값: {missing[:5]}"


def assert_rows_atomic(pieces):
    """각 ROW-NN 의 START/END 가 같은 조각 안에 정확히 한 번씩 있는가."""
    seen: dict[str, set[int]] = {}
    for index, piece in enumerate(pieces):
        for number, side in _ROW_MARKER.findall(piece):
            seen.setdefault(f"{number}-{side}", set()).add(index)
    numbers = {key.split("-")[0] for key in seen}
    for number in sorted(numbers):
        start = seen.get(f"{number}-START", set())
        end = seen.get(f"{number}-END", set())
        assert start == end and len(start) == 1, (
            f"ROW-{number} 이 조각 경계에서 잘렸다: start={start} end={end}")


def assert_header_self_contained(grid, pieces, num_cols, header_row_count):
    """모든 조각이 헤더 라벨을 전부 담고 있는가."""
    labels = [l for l in flatten_header_rows(grid, header_row_count, num_cols) if l]
    for index, piece in enumerate(pieces):
        for label in labels:
            # 계층 라벨은 markdown 에서 합쳐지고 html 에서는 행별로 남으므로 마지막 조각만 본다.
            leaf = label.split(" > ")[-1]
            assert leaf in piece, f"조각 {index} 에 헤더 '{leaf}' 가 없다"


def looks_like_markdown_table(text: str) -> bool:
    """markdown 파이프 표가 들어 있는가.

    구분선 표기가 경로마다 다르다 - table_splitter 는 `| --- |`, docling 의 compact
    serializer 는 `| - |` 를 낸다. 어느 쪽이든 구분선으로 인정한다.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    return any(
        set(line.replace("|", "").replace(" ", "")) <= {"-", ":"} and line.count("|") > 1
        for line in lines
    )


def assert_markdown_table_valid(piece):
    """유효한 markdown 표인가 — 헤더 1행 다음 줄이 구분선이고 열 수가 일정한가."""
    lines = [line for line in piece.splitlines() if line.strip().startswith("|")]
    assert len(lines) >= 3, f"표 줄이 부족하다: {len(lines)}"
    separator = lines[1]
    assert set(separator.replace("|", "").replace(" ", "")) <= {"-", ":"}, (
        f"2번째 줄이 구분선이 아니다: {separator!r}")
    widths = {line.count("|") for line in lines}
    assert len(widths) == 1, f"줄마다 열 수가 다르다: {sorted(widths)}"


def assert_html_table_valid(piece):
    """완결된 html 표인가 — table 태그 한 쌍, tr 짝이 맞는가."""
    assert piece.count("<table>") == piece.count("</table>") == 1
    assert piece.count("<tr>") == piece.count("</tr>") > 0


def assert_table_invariants(grid, pieces, *, num_cols, header_row_count, table_format):
    """위 검사를 한 번에. 분할 결과를 받는 테스트는 이것만 부르면 된다."""
    assert_values_preserved(grid, pieces, header_row_count=header_row_count)
    assert_rows_atomic(pieces)
    assert_header_self_contained(grid, pieces, num_cols, header_row_count)
    for piece in pieces:
        if table_format == "markdown":
            assert_markdown_table_valid(piece)
        else:
            assert_html_table_valid(piece)
