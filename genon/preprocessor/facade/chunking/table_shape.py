"""표 grid 의 구조를 읽고 그에 맞는 표현을 고르는 공통 유틸리티.

이 모듈은 Docling 타입을 직접 import 하지 않는다. processor 가 가진 TableItem 의
``data.grid`` 를 넘기면 셀의 공통 속성만 duck typing 으로 읽는다. 배포본에서 docling
버전에 묶이지 않게 하려는 것이며, 계약은 ``table_splitter`` 와 같다.

셀 판별 primitive(헤더 셀 여부, 선두 헤더 행 수)도 여기에 모았다. ``table_splitter`` 가
이것들을 가져다 쓰므로 의존 방향은 ``table_splitter -> table_shape`` 한 쪽뿐이다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Optional

_log = logging.getLogger(__name__)

TableFormat = Literal["html", "markdown"]

# output.table_format 이 받는 값. auto 는 표 구조를 보고 html/markdown 을 고른다.
TABLE_FORMAT_VALUES = ("html", "markdown", "auto")

# 계층 헤더를 한 라벨로 합칠 때 쓰는 구분자. 청크 선두 HEADER: 라인과 같은 모양이라
# 검색 질의에서 두 표기가 따로 놀지 않는다.
HEADER_LEVEL_SEP = " > "


def is_header_cell(cell: Any) -> bool:
    return bool(
        getattr(cell, "column_header", False)
        or getattr(cell, "row_header", False)
        or getattr(cell, "row_section", False)
    )


def _is_header_row(row: Sequence[Any]) -> bool:
    """이 행 전체가 컬럼 헤더 행인가.

    ``any(is_header_cell)`` 로는 셀 수 없다 - 행 라벨 표(`<th>구분</th><td>값</td>`)의
    데이터 행마다 첫 셀에 ``row_header`` 가 붙어 있어서 모든 행이 헤더로 집계되고,
    그 결과 단순 격자 표가 계층 헤더 표로 오인돼 auto 가 html 을 고르고 행 분할도
    포기하게 된다. 컬럼 헤더 셀이 있거나 행 전체가 헤더 셀일 때만 헤더 행으로 본다.
    """
    cells = list(row)
    if not cells:
        return False
    if any(getattr(cell, "column_header", False) for cell in cells):
        return True
    return all(is_header_cell(cell) for cell in cells)


def leading_header_row_count(grid: Sequence[Sequence[Any]]) -> int:
    """선두에서 연속되는 Docling 헤더 플래그 행 수를 반환한다."""
    count = 0
    for row in grid:
        if _is_header_row(row):
            count += 1
        else:
            break
    return count


def cell_at(row: Sequence[Any], column: int) -> Optional[Any]:
    return row[column] if column < len(row) else None


def cell_text(cell: Any) -> str:
    return str(getattr(cell, "text", "") or "").strip()


def _span(cell: Any, name: str) -> int:
    try:
        return max(int(getattr(cell, name, 1) or 1), 1)
    except (TypeError, ValueError):
        return 1


# degenerate 사유. 표 표기(파이프·태그)가 정보를 하나도 더하지 않는 블록의 종류다.
DEGENERATE_SINGLE_VALUE = "single-value"
DEGENERATE_NO_DATA_ROWS = "no-data-rows"


def degenerate_reason(
    grid: Optional[Sequence[Sequence[Any]]],
    num_cols: Optional[int] = None,
    *,
    header_rows: Optional[int] = None,
) -> Optional[str]:
    """표 표기가 정보를 더하지 않는 표인가. 그러면 사유 문자열, 아니면 None.

    HTML 문서에는 레이아웃·배너 용도로만 쓴 표가 흔하다(`<td colspan="4">안내 산문</td>`
    하나뿐인 표). 이런 블록을 markdown 으로 내면 헤더 행 + 구분선만 남고 데이터 행이 0이
    되며, colspan 은 grid 에서 피복 열마다 복제돼 있어 산문이 열 수만큼 중복된다. 검색
    임베딩에는 잡음만 더한다.

    ``analyze_grid().header_row_count`` 를 쓰지 않는다. 그쪽은 비-HTML 유래 표에서
    `flag_n + 1`(컬럼명 추정 행)을 더하므로, 여기에 쓰면 2행 정형 표가 데이터 행 0으로
    오판된다. 헤더 판정은 플래그 기반 카운트만 쓴다.

    ``header_rows`` 를 주면 그 값을 그대로 믿는다. 셀 플래그만으로 헤더 행을 세면 grid 를
    만든 쪽에 따라 답이 갈리기 때문이다 — `table_blocks._html_to_grid` 는 `<th>` 를 모두
    `column_header` 로 표시하므로 행 라벨 표(`<th scope="row">총 연회비</th><td>18,000</td>`)
    의 데이터 행까지 헤더로 세어 정형 표가 데이터 행 0으로 오판된다. 그 쪽은 자기 grid 를
    만들 때 이미 올바른 헤더 행 수를 알고 있으니 그 값을 넘긴다.

    판정에 넣지 않은 것: 헤더 없는 1행 표(헤더가 떨어져 나간 진짜 데이터 행일 수 있다),
    1열 N행 목록형 표(행 경계가 정보다), 셀 개수·길이 임계값(문서 종류마다 경계가 갈려
    설정 키를 부른다).
    """
    if not grid:
        return None
    cols = int(num_cols or 0) or max((len(row) for row in grid), default=0)
    if cols <= 0:
        return None

    # span 은 grid 에서 피복 위치마다 복제돼 있으므로 집합 크기가 곧 "전개 후 서로 다른 값" 수다.
    distinct = {text for row in grid for text in map(cell_text, row) if text}
    if len(distinct) <= 1:
        return DEGENERATE_SINGLE_VALUE

    header_n = leading_header_row_count(grid) if header_rows is None else int(header_rows)
    header_n = min(max(header_n, 0), len(grid))
    if len(grid) - header_n <= 0:
        return DEGENERATE_NO_DATA_ROWS
    return None


@dataclass(frozen=True)
class TableShape:
    """표 구조의 요약. 포맷 선택과 분할 전략이 이것 하나만 보고 결정된다."""

    num_rows: int
    num_cols: int
    header_row_count: int
    has_row_span: bool
    has_col_span: bool
    # 데이터 행에만 걸린 rowspan. 행 단위 분할 전에 정규화가 필요한지를 가른다.
    has_data_row_span: bool

    @property
    def is_complex(self) -> bool:
        """markdown 으로 내면 구조 정보를 잃는 표인가.

        markdown 파이프 표는 헤더가 한 행뿐이고 병합 셀을 표현하지 못한다. 값 자체는
        각 위치에 복제되어 남지만 `금리 > 기본` 같은 헤더 계층은 사라진다.
        """
        return self.has_row_span or self.has_col_span or self.header_row_count > 1


def analyze_grid(
    grid: Optional[Sequence[Sequence[Any]]],
    num_cols: Optional[int] = None,
    *,
    is_html_origin: bool = False,
) -> Optional[TableShape]:
    """grid 에서 TableShape 를 만든다. 읽을 수 없으면 None.

    ``header_row_count`` 규칙은 원본 문서 종류에 따라 다르다. HTML 유래 문서는 thead 가
    그대로 보존되므로 플래그 행을 믿고(없으면 첫 행), PDF 등은 플래그 행 다음의 컬럼명
    추정 행까지 한 행 더 포함한다. 이 규칙이 호출부 세 곳에 흩어져 있던 것을 여기로 모았다.
    """
    if not grid:
        return None
    cols = int(num_cols or 0) or max((len(row) for row in grid), default=0)
    if cols <= 0:
        return None

    flag_n = leading_header_row_count(grid)
    header_n = max(flag_n, 1) if is_html_origin else flag_n + 1
    header_n = min(header_n, len(grid))

    has_row_span = has_col_span = has_data_row_span = False
    for index, row in enumerate(grid):
        for cell in row:
            if _span(cell, "col_span") > 1:
                has_col_span = True
            if _span(cell, "row_span") > 1:
                has_row_span = True
                if index >= header_n:
                    has_data_row_span = True

    return TableShape(
        num_rows=len(grid),
        num_cols=cols,
        header_row_count=header_n,
        has_row_span=has_row_span,
        has_col_span=has_col_span,
        has_data_row_span=has_data_row_span,
    )


def resolve_table_format(configured: Any, shape: Optional[TableShape] = None) -> str:
    """설정값과 표 구조로 최종 직렬화 형식을 정한다.

    ``auto`` 는 구조를 보고 고른다 — 병합 셀이나 계층 헤더가 있으면 html, 단순 격자면
    markdown. shape 를 못 읽었으면(=구조를 모르면) 정보를 잃지 않는 html 로 간다.
    """
    value = str(configured or "").strip().lower()
    if value not in TABLE_FORMAT_VALUES:
        if value:
            _log.warning(
                "[table_shape] Unknown table_format '%s', fallback to 'html'.", configured)
        return "html"
    if value != "auto":
        return value
    if shape is None:
        return "html"
    return "html" if shape.is_complex else "markdown"


@dataclass(frozen=True)
class _FlatCell:
    """rowspan 을 푼 자리에 놓는 값 객체.

    docling TableCell 을 변형하지 않으려고 새로 만든다 — 원본을 건드리면 뒤따르는 파서
    출력이나 표 이미지 경로까지 오염된다. table_splitter 가 읽는 속성만 갖는다.
    """

    text: str
    start_col_offset_idx: int
    col_span: int = 1
    row_span: int = 1
    column_header: bool = False
    row_header: bool = False
    row_section: bool = False


def normalize_row_spans(grid: Sequence[Sequence[Any]]) -> list[list[Any]]:
    """모든 셀의 row_span 을 1 로 만든 새 grid 를 반환한다.

    Docling 의 ``grid`` 는 병합 셀을 이미 각 피복 위치에 복제해 두므로 값을 옮길 필요는
    없다. 남은 문제는 ``row_span`` 값 자체뿐이라, 그 값만 1 로 바꾼 사본을 만든다.
    원본 grid 와 셀 객체는 그대로 둔다.
    """
    normalized: list[list[Any]] = []
    for row in grid:
        new_row: list[Any] = []
        for column, cell in enumerate(row):
            if _span(cell, "row_span") <= 1:
                new_row.append(cell)
                continue
            new_row.append(_FlatCell(
                text=cell_text(cell),
                start_col_offset_idx=int(getattr(cell, "start_col_offset_idx", column) or 0),
                col_span=_span(cell, "col_span"),
                column_header=bool(getattr(cell, "column_header", False)),
                row_header=bool(getattr(cell, "row_header", False)),
                row_section=bool(getattr(cell, "row_section", False)),
            ))
        normalized.append(new_row)
    return normalized


def flatten_header_rows(
    grid: Sequence[Sequence[Any]], header_row_count: int, num_cols: int
) -> list[str]:
    """다중 헤더 행을 컬럼마다 ``상위 > 하위`` 한 줄로 합친다.

    markdown 표는 헤더가 한 행뿐이라 두 행을 그대로 쓰면 구분선 위치가 어긋나 표로
    파싱되지 않는다. 합치면 유효한 표가 되고 계층 관계도 본문에 남는다.
    """
    labels: list[str] = []
    for column in range(num_cols):
        parts: list[str] = []
        for row in grid[:max(header_row_count, 0)]:
            text = cell_text(cell_at(row, column))
            # 상위 헤더는 colspan 으로 여러 컬럼에 복제되므로 같은 값이 이어지면 한 번만 쓴다.
            if text and text not in parts:
                parts.append(text)
        labels.append(HEADER_LEVEL_SEP.join(parts))
    return labels


def serialize_rows(
    rows: Sequence[Sequence[Any]], header_labels: Sequence[str], num_cols: int
) -> list[str]:
    """데이터 행을 ``컬럼=값 | 컬럼=값`` 한 줄씩으로 만든다.

    병합 셀이 많은 표는 격자 표현만으로는 어느 헤더에 걸린 값인지 임베딩에 드러나지
    않는다. 헤더 라벨을 값 옆에 붙인 문장을 함께 실어 그 관계를 명시한다.
    """
    lines: list[str] = []
    for row in rows:
        parts: list[str] = []
        for column in range(num_cols):
            value = cell_text(cell_at(row, column))
            if not value:
                continue
            label = header_labels[column] if column < len(header_labels) else ""
            parts.append(f"{label}={value}" if label else value)
        if parts:
            lines.append(" | ".join(parts))
    return lines
