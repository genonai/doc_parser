"""청크 텍스트 안의 표 블록을 찾고 표기형태를 바꾼다.

docling document 청킹 경로는 `TableItem` 을 직접 다루므로 표 경계도, 형식별 재직렬화도
구조 정보로 해결한다(`table_variants.TableTextVariants`, `smart_chunker.table_as_chunk`).
그러나 청크 텍스트를 문자열로 조립하는 경로(행 기반 custom_fields, parse-format 텍스트)에는
`TableItem` 이 없다. 그 경로에서도 같은 기능이 성립해야 하므로, 여기서는 **확정된 청크
텍스트만 보고** 표 블록을 찾아 다룬다.

두 기능이 이 모듈의 같은 검출기를 쓴다.

1. 표기형태 필드(`text_table_html`/`text_table_md`) — 블록을 다른 표기형태로 다시 렌더한다.
2. 표 독립 청크 — 블록 경계를 청크 조각 경계로 쓴다.

변환은 셀 그리드를 중간표현으로 왕복한다. docling 재파싱은 하지 않는다 — 표기형태 변환에
필요한 것은 격자 구조뿐이고, 재파싱은 비용이 클 뿐 아니라 이 경로에는 원본 HTML 이 이미
없다. 변환할 수 없으면 그 블록을 원문 표기 그대로 둔다(내용이 사라지는 일은 없다).

docling 타입을 import 하지 않는다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from genon.preprocessor.facade.chunking import header_path as hp
from genon.preprocessor.facade.chunking import table_html as th
from genon.preprocessor.facade.chunking import table_shape as ts

_log = logging.getLogger(__name__)

# 표기형태 이름. `config_parse.TABLE_TEXT_FORMATS` 와 같은 어휘를 쓴다.
HTML = "html"
MARKDOWN = "markdown"

# markdown 표 구분선. `| --- | --- |` 과 compact_tables 의 `| - | - |` 을 모두 받는다.
_MD_DELIM = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
# 파이프로 시작하는 표 행. 구분선 없이 이 줄만 있으면 표로 보지 않는다.
_MD_ROW = re.compile(r"^\s*\|.*\|\s*$")
# 코드펜스 경계(``` 또는 ~~~). 펜스 안의 파이프는 표가 아니다.
_FENCE = re.compile(r"^\s*(?:```|~~~)")
# `<table ...>` ~ `</table>`. 중첩 표는 바깥 것만 잡는다(_find_html_blocks 가 깊이를 센다).
_TABLE_OPEN = re.compile(r"<table\b[^>]*>", re.I)
_TABLE_CLOSE = re.compile(r"</table\s*>", re.I)


@dataclass
class Block:
    """청크 텍스트 안의 표 한 덩어리."""

    start: int
    end: int
    kind: str          # HTML | MARKDOWN
    text: str


@dataclass
class _Cell:
    """`table_html.render_row` 가 duck typing 으로 읽는 최소 셀.

    docling grid cell 과 같은 속성 이름을 쓴다 — 렌더러를 그대로 재사용하기 위해서다.
    """

    text: str = ""
    start_row_offset_idx: int = 0
    start_col_offset_idx: int = 0
    row_span: int = 1
    col_span: int = 1
    column_header: bool = False


@dataclass
class _Grid:
    """표의 중간표현. 병합은 이미 풀린 상태(피복 위치마다 셀이 복제돼 있다)."""

    rows: list = field(default_factory=list)
    num_cols: int = 0
    caption: str = ""
    header_rows: int = 0


def find_blocks(text: str) -> list:
    """텍스트 안의 표 블록 목록(등장 순서).

    HTML 표와 markdown 파이프 표를 모두 찾는다. `table_format: auto` 는 표마다 표기형태가
    갈리므로 한 청크에 두 종류가 섞여 있을 수 있다.
    """
    if not text:
        return []
    blocks = _find_html_blocks(text)
    if blocks:
        # HTML 표 안의 파이프 줄을 markdown 표로 오인하지 않도록 구간을 비워 두고 훑는다.
        masked = list(text)
        for block in blocks:
            for pos in range(block.start, block.end):
                if masked[pos] != "\n":
                    masked[pos] = " "
        blocks.extend(_find_markdown_blocks("".join(masked), source=text))
    else:
        blocks.extend(_find_markdown_blocks(text, source=text))
    blocks.sort(key=lambda block: block.start)
    return blocks


def _find_html_blocks(text: str) -> list:
    """`<table>` 블록. 중첩 표는 가장 바깥 경계만 낸다."""
    if "<table" not in text.lower():
        return []
    blocks: list = []
    pos = 0
    while True:
        opened = _TABLE_OPEN.search(text, pos)
        if not opened:
            break
        depth = 1
        cursor = opened.end()
        end = -1
        while depth:
            nxt_open = _TABLE_OPEN.search(text, cursor)
            nxt_close = _TABLE_CLOSE.search(text, cursor)
            if not nxt_close:
                break
            if nxt_open and nxt_open.start() < nxt_close.start():
                depth += 1
                cursor = nxt_open.end()
                continue
            depth -= 1
            cursor = nxt_close.end()
            end = cursor
        if end < 0:
            # 닫는 태그가 없다. 표로 다루지 않는다(잘린 HTML 을 통째로 표로 보면 본문을 삼킨다).
            break
        blocks.append(Block(opened.start(), end, HTML, text[opened.start():end]))
        pos = end
    return blocks


def _find_markdown_blocks(text: str, *, source: str) -> list:
    """파이프 표 블록. 구분선이 있는 연속 파이프 행 런만 표로 본다.

    `source` 는 블록 텍스트를 떠올 원문이다(HTML 구간을 가린 사본으로 위치를 찾더라도
    실제 텍스트는 원문에서 잘라야 한다).
    """
    if "|" not in text:
        return []
    blocks: list = []
    offset = 0
    run_start: int | None = None
    run_end = 0
    has_delim = False
    in_fence = False
    for line in text.split("\n"):
        line_start = offset
        offset += len(line) + 1
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _MD_ROW.match(line):
            if run_start is None:
                run_start = line_start
            run_end = line_start + len(line)
            has_delim = has_delim or bool(_MD_DELIM.match(line))
            continue
        if run_start is not None and has_delim:
            blocks.append(Block(run_start, run_end, MARKDOWN, source[run_start:run_end]))
        run_start = None
        has_delim = False
    if run_start is not None and has_delim:
        blocks.append(Block(run_start, run_end, MARKDOWN, source[run_start:run_end]))
    return blocks


def _looks_degenerate(kind: str, source: str) -> bool:
    """grid 를 만들기 전에 값싸게 걸러 낸다. 재현율만 보장하면 된다.

    `renotate` 는 청크마다 표기형태마다 돌므로, 표기가 이미 맞는 블록까지 bs4 로 파싱하면
    비용이 눈에 보이게 늘어난다. 여기서 통과한 것만 grid 로 확정 판정한다
    (정밀도는 `table_shape.degenerate_reason` 이 담당).
    """
    if kind == HTML:
        return source.lower().count("<tr") <= 1 or (
            source.lower().count("<td") + source.lower().count("<th")) <= 1
    if kind == MARKDOWN:
        return len([line for line in source.split("\n") if _MD_ROW.match(line)]) <= 2
    return False


def convert(block: Any, fmt: str, *, compact_tables: bool = True) -> str:
    """표 블록을 `fmt` 표기형태로 렌더한다. 바꿀 필요·수단이 없으면 원문 그대로.

    레이아웃용 표(`table_shape.degenerate_reason`)는 요청 표기와 무관하게 평문으로 낸다.
    표기가 이미 `fmt` 인 블록도 이 판정을 받아야 하므로, 조기 반환보다 판정이 앞에 온다.
    """
    kind = getattr(block, "kind", "")
    source = getattr(block, "text", "") or ""
    if not source or fmt not in (HTML, MARKDOWN):
        return source
    if kind == fmt and not _looks_degenerate(kind, source):
        return source
    grid = _to_grid(block)
    if grid is None or not grid.rows:
        return source
    reason = ts.degenerate_reason(
        grid.rows, grid.num_cols, header_rows=getattr(grid, "header_rows", None))
    if reason:
        prose = th.render_plain_text(grid.rows, grid.num_cols, caption=grid.caption)
        if prose:
            _log.debug(
                "[table_blocks] 레이아웃용 표를 평문으로 냈습니다: reason=%s", reason)
            return prose
        return source
    if kind == fmt:
        return source
    rendered = (
        _render_html(grid) if fmt == HTML
        else _render_markdown(grid, compact_tables=compact_tables)
    )
    return rendered or source


def renotate(text: str, fmt: str, *, compact_tables: bool = True) -> str:
    """텍스트 안의 표를 모두 `fmt` 표기형태로 바꾼 텍스트.

    표가 없으면 원문을 그대로 돌려준다 — 호출부가 분기하지 않게 한다.
    """
    blocks = find_blocks(text)
    if not blocks:
        return text
    pieces: list = []
    cursor = 0
    unchanged = 0
    for block in blocks:
        rendered = convert(block, fmt, compact_tables=compact_tables)
        if rendered == block.text and block.kind != fmt:
            unchanged += 1
        pieces.append(text[cursor:block.start])
        pieces.append(rendered)
        cursor = block.end
    pieces.append(text[cursor:])
    if unchanged:
        _log.debug(
            "[table_blocks] 표기형태를 바꾸지 못해 원문 표기로 남긴 표가 있습니다: "
            "format=%s count=%d", fmt, unchanged)
    return "".join(pieces)


def _degenerate_prose(block: Any) -> str:
    """블록이 레이아웃용 표면 평문, 아니면 빈 문자열."""
    if not _looks_degenerate(getattr(block, "kind", ""), getattr(block, "text", "") or ""):
        return ""
    grid = _to_grid(block)
    if grid is None or not grid.rows:
        return ""
    if not ts.degenerate_reason(
            grid.rows, grid.num_cols, header_rows=getattr(grid, "header_rows", None)):
        return ""
    return th.render_plain_text(grid.rows, grid.num_cols, caption=grid.caption)


def normalize_degenerate(text: str) -> str:
    """텍스트 안의 레이아웃용 표를 평문으로 바꾼 텍스트. 정형 표는 그대로 둔다.

    `renotate` 는 표기형태 필드(`text_table_html`/`text_table_md`)만 만들므로, 청크 본문
    자체에 남는 배너 표는 여기서 푼다. 표가 없거나 전부 정형 표면 원문을 그대로 돌려준다.
    """
    blocks = find_blocks(text)
    if not blocks:
        return text
    pieces: list = []
    cursor = 0
    changed = 0
    for block in blocks:
        prose = _degenerate_prose(block)
        pieces.append(text[cursor:block.start])
        pieces.append(prose or block.text)
        cursor = block.end
        changed += 1 if prose else 0
    pieces.append(text[cursor:])
    if changed:
        _log.debug("[table_blocks] 레이아웃용 표를 평문으로 냈습니다: count=%d", changed)
    return "".join(pieces)


def split_at_tables(text: str) -> list:
    """표 블록마다 자기 조각을 갖도록 텍스트를 나눈다.

    반환 조각을 순서대로 이으면 (조각 사이 공백 정리를 빼고) 원문과 같은 내용이다.
    표가 없으면 원문 한 조각만 돌려준다 — 호출부가 분기하지 않게 한다.

    표 바로 앞의 `[표 검색 설명]` 블록은 그 표의 것이므로 표 조각에 함께 둔다. docling
    경로도 설명을 표 청크 선두에 싣는다 — 떼어 놓으면 표만 담긴 청크가 무슨 표인지
    설명하는 문장을 잃고, 설명 청크는 근거가 될 표를 잃는다.
    """
    blocks = find_blocks(text)
    if not blocks:
        return [text] if text else []
    label = _retrieval_label()
    pieces: list = []
    cursor = 0
    for block in blocks:
        before = text[cursor:block.start]
        description = ""
        if label:
            index = before.rfind(label)
            # 줄 선두에 있는 라벨만 설명 블록으로 본다(본문에 인용된 라벨과 구분).
            if index >= 0 and (index == 0 or before[index - 1] == "\n"):
                before, description = before[:index], before[index:].strip()
        before = before.strip()
        if before:
            pieces.append(before)
        table = text[block.start:block.end].strip()
        if description:
            table = f"{description}\n{table}" if table else description
        if table:
            pieces.append(table)
        cursor = block.end
    tail = text[cursor:].strip()
    if tail:
        pieces.append(tail)
    return pieces or [text]


def _retrieval_label() -> str:
    """`[표 검색 설명]` 블록 라벨. 원본은 enrichment 쪽 한 곳뿐이다."""
    try:
        from genon.preprocessor.facade.enrichment.table_description import (
            TABLE_RETRIEVAL_LABEL,
        )
    except Exception:      # 라벨을 못 읽으면 설명 이동만 생략한다(분리 자체는 유효하다)
        return ""
    return TABLE_RETRIEVAL_LABEL


def expand_elements(elements) -> list:
    """표를 담은 parse-format element 를 표 조각과 본문 조각으로 나눈다.

    docling 경로는 `TableItem` 을 만나면 섹션을 끊어 표만 담은 청크를 만든다
    (`smart_chunker` 의 table_as_chunk). 행 경로에는 그 구조가 없으므로 element 내용의
    표 블록을 경계로 삼아 같은 결과를 만든다 — 기능이 경로나 doc_type 에 따라 갈리지
    않게 한다.

    `chunk_prefix` 가 있으면 조각마다 다시 붙인다. 안 그러면 표 조각이 어느 카드·섹션의
    표인지 모르는 채로 검색에 노출된다(docling 경로의 `HEADER:` 라인과 같은 몫).
    `metadata` 등 나머지 키는 조각마다 그대로 복제한다 — 같은 레코드의 조각임을 적재
    측에서 metadata 로 식별하는 기존 규칙(`_expand_splittable_rows`)과 같다.
    """
    expanded: list = []
    split_rows = 0
    for element in elements:
        content = str((element or {}).get("content", "") or "")
        prefix = str((element or {}).get("chunk_prefix") or "")
        body = content
        if prefix and content.startswith(prefix):
            body = content[len(prefix):].lstrip("\n")
        else:
            prefix = ""      # 접두와 본문이 어긋나면 재부착을 포기하고 전체를 나눈다
        # 배너 표를 먼저 평문으로 풀어 둔다. 안 그러면 안내 산문이 표로 인식돼 자기 청크로
        # 격리되고, 정작 표로서 검색될 것은 없다.
        body = normalize_degenerate(body)
        pieces = split_at_tables(body)
        if len(pieces) <= 1:
            expanded.append(element)
            continue
        split_rows += 1
        # 표만 담긴 조각도 어느 섹션의 표인지 알아야 한다. 크기 기준 분할과 같은 규칙을 쓴다.
        pieces = hp.carry_over_section_headings(pieces)
        for piece in pieces:
            expanded.append({
                **element, "content": f"{prefix}\n{piece}" if prefix else piece,
            })

    if split_rows:
        _log.info(
            "[table_blocks] 표를 담은 행 %d건을 표 단위로 분리했습니다: %d → %d 청크",
            split_rows, len(elements), len(expanded))
    return expanded


def has_table(text: str) -> bool:
    """텍스트가 표를 담고 있는가. 행 경로 청크의 `has_table` 판정에 쓴다.

    레이아웃용 표는 세지 않는다 - `expand_elements` 가 그 블록을 평문으로 풀어 내보내므로,
    표로 세면 청크 본문에는 없는 표를 있다고 표시하게 된다.
    """
    return any(not _degenerate_prose(block) for block in find_blocks(text))


def _to_grid(block: Any):
    kind = getattr(block, "kind", "")
    text = getattr(block, "text", "") or ""
    if kind == HTML:
        return _html_to_grid(text)
    if kind == MARKDOWN:
        return _markdown_to_grid(text)
    return None


def _html_to_grid(html_text: str):
    """`<table>` 문자열 → 셀 그리드. bs4 가 없거나 파싱이 깨지면 None."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        table = soup.find("table")
        if table is None:
            return None
        caption_tag = table.find("caption")
        caption = caption_tag.get_text(" ", strip=True) if caption_tag else ""
        if caption_tag is not None:
            caption_tag.extract()

        rows: list = []
        header_rows = 0
        # rowspan 으로 아래 행에 이어지는 셀. {컬럼: (남은 행 수, 원본 셀)}
        carry: dict = {}
        # 중첩 표의 행은 그 표의 것이다(가장 가까운 조상 table 로 가른다).
        own_rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
        for row_index, tr in enumerate(own_rows):
            cells = tr.find_all(["td", "th"], recursive=False)
            row: list = []
            column = 0
            header_cells = 0
            data_cells = 0
            index = 0

            def place(cell, at: int) -> None:
                while len(row) <= at:
                    row.append(None)
                row[at] = cell

            while index < len(cells) or column in carry:
                if column in carry:
                    remaining, template = carry[column]
                    place(_Cell(
                        text=template.text,
                        start_row_offset_idx=template.start_row_offset_idx,
                        start_col_offset_idx=template.start_col_offset_idx,
                        row_span=template.row_span,
                        col_span=template.col_span,
                        column_header=template.column_header,
                    ), column)
                    if remaining <= 1:
                        carry.pop(column, None)
                    else:
                        carry[column] = (remaining - 1, template)
                    column += 1
                    continue
                cell_tag = cells[index]
                index += 1
                is_header = cell_tag.name.lower() == "th"
                if is_header:
                    header_cells += 1
                else:
                    data_cells += 1
                col_span = _int_attr(cell_tag, "colspan")
                row_span = _int_attr(cell_tag, "rowspan")
                value = cell_tag.get_text(" ", strip=True)
                start_col = column
                for offset in range(col_span):
                    cell = _Cell(
                        text=value,
                        start_row_offset_idx=row_index,
                        start_col_offset_idx=start_col,
                        row_span=row_span,
                        col_span=col_span,
                        column_header=is_header,
                    )
                    place(cell, start_col + offset)
                    if row_span > 1:
                        carry[start_col + offset] = (row_span - 1, cell)
                column = start_col + col_span
            if not row:
                continue
            # 헤더 행: 데이터 셀 없이 헤더 셀만 있고, 아직 선두 헤더 구간인 행. 행 라벨 표
            # (`<th scope="row">`)의 데이터 행이 헤더로 오인되지 않게 한다
            # (table_shape._is_header_row 와 같은 판정 취지).
            if header_cells and not data_cells and len(rows) == header_rows:
                header_rows += 1
            rows.append(row)

        if not rows:
            return None
        return _Grid(
            rows=rows,
            num_cols=max((len(row) for row in rows), default=0),
            caption=caption,
            header_rows=header_rows,
        )
    except Exception:
        _log.debug("[table_blocks] HTML 표 파싱 실패 - 원문 표기를 유지합니다", exc_info=True)
        return None


def _markdown_to_grid(md_text: str):
    """파이프 표 문자열 → 셀 그리드. 구분선 위쪽은 헤더 행이다."""
    rows: list = []
    header_rows = 0
    seen_delim = False
    for line in md_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if _MD_DELIM.match(stripped):
            seen_delim = True
            header_rows = len(rows)
            continue
        values = _split_md_row(stripped)
        if not values:
            continue
        rows.append([
            _Cell(
                text=value,
                start_row_offset_idx=len(rows),
                start_col_offset_idx=column,
                column_header=not seen_delim,
            )
            for column, value in enumerate(values)
        ])
    if not rows:
        return None
    return _Grid(
        rows=rows,
        num_cols=max((len(row) for row in rows), default=0),
        header_rows=header_rows,
    )


def _split_md_row(line: str) -> list:
    """`| a | b |` → `["a", "b"]`. 이스케이프한 파이프(`\\|`)는 셀 값으로 남긴다."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells: list = []
    current: list = []
    escaped = False
    for char in body:
        if escaped:
            current.append(char if char == "|" else f"\\{char}")
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def _render_html(grid) -> str:
    """그리드 → 정제 HTML 표. 청킹 경로와 같은 렌더러를 쓴다."""
    try:
        return th.render_table(grid.rows, grid.num_cols, caption=grid.caption)
    except Exception:
        _log.debug("[table_blocks] HTML 표 렌더 실패 - 원문 표기를 유지합니다", exc_info=True)
        return ""


def _render_markdown(grid, *, compact_tables: bool = True) -> str:
    """그리드 → 파이프 표.

    첫 행을 헤더로 둔다(구분선이 없으면 markdown 표로 읽히지 않는다). 헤더 행이 여러
    줄인 계층 헤더 표는 첫 줄만 헤더로 쓰고 나머지는 데이터 행으로 남긴다 — markdown 에
    계층 헤더를 표현할 방법이 없고, 값을 버리는 것보다 행으로 남기는 것이 낫다.
    병합 셀은 피복 위치마다 값이 복제된 채로 나간다(markdown 에 span 이 없다).
    """
    lines: list = []
    if grid.caption:
        lines.append(grid.caption)
    body: list = []
    for row_index, row in enumerate(grid.rows):
        values = [
            th.escape_cell(getattr(th_cell, "text", "")) if th_cell is not None else ""
            for th_cell in (_cell_or_none(row, column) for column in range(grid.num_cols))
        ]
        # 파이프는 셀 구분자라 값에 그대로 두면 표가 어긋난다.
        values = [value.replace("|", "\\|") for value in values]
        body.append("| " + " | ".join(values) + " |")
        if row_index == 0:
            delim = "-" if compact_tables else "---"
            body.append("|" + "|".join(f" {delim} " for _ in range(grid.num_cols)) + "|")
    if not body:
        return ""
    text = "\n".join(lines + body)
    return th.drop_blank_markdown_rows(text)


def _cell_or_none(row, column: int):
    return row[column] if column < len(row) else None


def _int_attr(tag: Any, name: str) -> int:
    try:
        return max(int(str(tag.get(name, 1) or 1).strip()), 1)
    except (TypeError, ValueError):
        return 1
