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


# ─── /chunker 설정 경로 ────────────────────────────────────────────────────────

import yaml
from pathlib import Path

_RESOURCE = Path(__file__).resolve().parents[2] / "resource"
_RESOURCE_DEV = Path(__file__).resolve().parents[2] / "resource_dev"


@pytest.mark.unit
@pytest.mark.parametrize("name", [
    "chunking_processor_config.yaml",
    "chunking_processor_config_simple.yaml",
])
def test_chunker_config_exposes_table_switches(name):
    """파서와 청커는 별개 호출이라 파서 설정이 넘어오지 않는다.

    이 블록이 없으면 /chunker 로 들어온 문서는 표 형식을 지정할 방법이 없다.
    """
    output = yaml.safe_load((_RESOURCE / name).read_text(encoding="utf-8")).get("output")
    assert output is not None, f"{name} 에 output 블록이 없다"
    assert cp.resolve_table_format_setting(output) in {"html", "markdown", "auto"}
    assert cp.resolve_table_row_serialization(output) is False  # 기본은 off


@pytest.mark.unit
def test_chunker_dev_config_matches_operational_keys():
    ops = yaml.safe_load(
        (_RESOURCE / "chunking_processor_config.yaml").read_text(encoding="utf-8"))["output"]
    dev = yaml.safe_load(
        (_RESOURCE_DEV / "chunking_processor_config.yaml").read_text(encoding="utf-8"))["output"]
    assert set(ops) == set(dev)


class _Processor:
    """object.__new__ 로 만든 인스턴스처럼 속성이 일부만 있는 상황도 흉내낸다."""

    def __init__(self, **attrs):
        for key, value in attrs.items():
            setattr(self, key, value)


@pytest.mark.unit
def test_config_fills_table_format_when_request_is_silent():
    kwargs = cp.apply_table_output_defaults({}, _Processor(_table_format="auto"))
    assert kwargs["table_format"] == "auto"
    assert kwargs["compact_tables"] is True
    assert kwargs["table_row_serialization"] is False


@pytest.mark.unit
def test_request_value_wins_over_config():
    kwargs = cp.apply_table_output_defaults(
        {"table_format": "markdown"}, _Processor(_table_format="auto"))
    assert kwargs["table_format"] == "markdown"


@pytest.mark.unit
def test_legacy_export_to_html_flag_is_not_overridden_by_config():
    """레거시 플래그만 보낸 요청에 table_format 을 채우면 그 플래그가 조용히 무시된다."""
    kwargs = cp.apply_table_output_defaults(
        {"export_to_html": 0}, _Processor(_table_format="html"))
    assert "table_format" not in kwargs

    module = pytest.importorskip(
        "genon.preprocessor.facade.chunking_processor", exc_type=ImportError)
    chunker = object.__new__(module.GenosSmartChunker)
    assert chunker._resolve_table_format(kwargs) == "markdown"


@pytest.mark.unit
def test_missing_processor_attributes_fall_back_to_defaults():
    """object.__new__ 로 만든 인스턴스를 쓰는 테스트가 있어 속성 부재를 견뎌야 한다."""
    kwargs = cp.apply_table_output_defaults({}, _Processor())
    assert kwargs == {
        "table_format": "html", "compact_tables": True,
        "table_row_serialization": False, "table_text_formats": (),
    }


# ─── 표 표기형태별 추가 텍스트(#360 text_table_html / text_table_md) ───────────


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    (None, ()),
    ([], ()),
    (["html", "markdown"], ("html", "markdown")),
    (["markdown", "html"], ("markdown", "html")),   # 순서를 보존한다
    ("html", ("html",)),
    ("html, markdown", ("html", "markdown")),
    (["md"], ("markdown",)),                        # 필드명(md)으로 써도 받는다
    (["html", "html"], ("html",)),
    (["html", "아무말"], ("html",)),                 # 모르는 형식만 버린다
    (["아무말"], ()),
    (17, ()),
])
def test_table_text_formats_parsing(value, expected):
    source = {} if value is None else {"table_text_formats": value}
    assert cp.resolve_table_text_formats(source) == expected


@pytest.mark.unit
def test_table_text_formats_defaults_to_off_in_operational_config():
    """켜면 본문이 형식 수만큼 복제된다. 운영 기본은 off 여야 한다."""
    for name in ("chunking_processor_config.yaml", "chunking_processor_config_simple.yaml",
                 "intelligent_processor_config.yaml", "convert_processor_config.yaml"):
        output = yaml.safe_load((_RESOURCE / name).read_text(encoding="utf-8"))["output"]
        assert cp.resolve_table_text_formats(output) == (), name


@pytest.mark.unit
def test_table_text_formats_reaches_chunker_through_kwargs():
    kwargs = cp.apply_table_output_defaults(
        {}, _Processor(_table_text_formats=("html", "markdown")))
    assert kwargs["table_text_formats"] == ("html", "markdown")

    module = pytest.importorskip(
        "genon.preprocessor.facade.chunking_processor", exc_type=ImportError)
    chunker = object.__new__(module.GenosSmartChunker)
    assert chunker._variant_formats(kwargs) == ("html", "markdown")
    # 요청이 명시한 값이 설정을 이긴다.
    assert cp.apply_table_output_defaults(
        {"table_text_formats": ["html"]},
        _Processor(_table_text_formats=("markdown",)))["table_text_formats"] == ["html"]
