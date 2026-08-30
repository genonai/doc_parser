"""청크 메타데이터의 표 필드(has_table/table_refs/분할 순서) 단위 테스트."""

import json

import pytest

from genon.preprocessor.facade.common.vector_meta import VectorMetaBuilderBase


class _Builder(VectorMetaBuilderBase):
    def build(self):
        return self.core_payload()


def _table(ref):
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    item = core.TableItem(
        self_ref=ref, label="table",
        data=core.TableData(num_rows=0, num_cols=0, table_cells=[]),
    )
    return item


def _text(ref="#/texts/0"):
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    return core.TextItem(self_ref=ref, label="text", text="본문", orig="본문")


@pytest.mark.unit
def test_chunk_without_table_is_marked_false():
    builder = _Builder().set_table_info([_text()])
    assert builder.has_table is False
    assert builder.table_refs is None
    assert builder.table_split_index is None


@pytest.mark.unit
def test_table_refs_are_recorded_for_join_with_chunk_bboxes():
    builder = _Builder().set_table_info([_text(), _table("#/tables/0")])
    assert builder.has_table is True
    assert json.loads(builder.table_refs) == ["#/tables/0"]


@pytest.mark.unit
def test_split_pieces_get_increasing_index_within_one_document():
    """같은 표의 조각이 연속해 나오는 순서가 곧 조각 번호다."""
    totals = {"#/tables/0": 3}
    seen: dict = {}
    indexes = []
    for _ in range(3):
        builder = _Builder().set_table_info([_table("#/tables/0")], totals, seen)
        indexes.append((builder.table_split_index, builder.table_split_total))
    assert indexes == [(0, 3), (1, 3), (2, 3)]


@pytest.mark.unit
def test_unsplit_table_has_no_piece_index():
    builder = _Builder().set_table_info([_table("#/tables/0")], {"#/tables/0": 1}, {})
    assert builder.has_table is True
    assert (builder.table_split_index, builder.table_split_total) == (None, None)


@pytest.mark.unit
def test_multiple_tables_in_one_chunk_have_no_piece_index():
    """표가 둘 이상이면 조각 순서라는 개념이 성립하지 않는다."""
    builder = _Builder().set_table_info(
        [_table("#/tables/0"), _table("#/tables/1")], {"#/tables/0": 2}, {})
    assert json.loads(builder.table_refs) == ["#/tables/0", "#/tables/1"]
    assert builder.table_split_index is None


@pytest.mark.unit
def test_missing_split_totals_still_fills_refs():
    """청커가 기록을 남기지 않는 경로(첨부 등)에서도 표 식별은 된다."""
    builder = _Builder().set_table_info([_table("#/tables/0")])
    assert builder.has_table is True
    assert builder.table_split_total is None


# ─── 설정 해석 ────────────────────────────────────────────────────────────────

from genon.preprocessor.facade.common import config_parse as cp


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    ("html", "html"), ("markdown", "markdown"), ("auto", "auto"),
    ("AUTO", "auto"), (" Markdown ", "markdown"),
])
def test_table_format_setting_keeps_auto_for_the_chunker_to_decide(value, expected):
    """auto 를 설정 단계에서 깎으면 표 구조를 볼 기회가 사라진다."""
    assert cp.resolve_table_format_setting({"table_format": value}) == expected


@pytest.mark.unit
def test_unknown_table_format_falls_back_with_warning():
    assert cp.resolve_table_format_setting({"table_format": "otsl"}) == "html"


@pytest.mark.unit
@pytest.mark.parametrize("source,expected", [
    ({}, "html"),
    ({"export_to_html": 1}, "html"),
    ({"export_to_html": 0}, "markdown"),
])
def test_legacy_export_to_html_flag_still_works(source, expected):
    assert cp.resolve_table_format_setting(source) == expected


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    (None, False), (True, True), ("true", True), ("false", False), ("아무말", False),
])
def test_row_serialization_switch_defaults_off(value, expected):
    source = {} if value is None else {"table_row_serialization": value}
    assert cp.resolve_table_row_serialization(source) is expected
