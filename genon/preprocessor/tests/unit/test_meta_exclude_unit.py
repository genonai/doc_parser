"""`fields.<이름>.meta: false` — 필드를 청크 메타에서만 뺀다.

값 조립에만 쓰이는 중간 필드(template·pack 재료, require·filter 조건)가 적재 컬럼까지
나가는 것을 막는 스위치다. 선언을 지우는 것과는 다르다 — 값은 그대로 만들어져 파이프라인
전체가 쓰고, 빠지는 것은 청크 메타를 조립하는 마지막 지점 하나뿐이다.

지정하지 않으면 실린다(기본 true). 빼려는 필드만 `meta: false` 를 적는다.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from genon.preprocessor.facade.chunking_processor import DocumentProcessor as ChunkProcessor
from genon.preprocessor.processing.common import config_parse as cp
from genon.preprocessor.processing.enrichment import config_schema as cs
from genon.preprocessor.processing.enrichment import config_v2 as cv2
from genon.preprocessor.processing.enrichment.tabular_custom_fields import (
    TabularCustomFieldsMapper,
    compile_meta_exclude,
)


def _write(path, body: str):
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# ── 설정 표기 ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_meta_false_becomes_internal_block():
    cfg = {
        "schema": "v2",
        "source": {"kind": "rows"},
        "fields": {"KEEP": {"alias": ["a"]}, "DROP": {"alias": ["b"], "meta": False}},
    }
    internal, extractor = cv2.normalize(cfg)
    assert internal["meta_include"] == {"DROP": False}
    # 지원 키 표에도 올라 있어야 기동 검증을 통과한다.
    cs.validate_known_keys(internal, label="t", extractor=extractor)


@pytest.mark.unit
def test_unset_meta_means_the_field_is_emitted():
    """기본값은 true — 적지 않은 필드는 종전대로 메타에 실린다."""
    cfg = {"column_map": {"A": ["a"], "B": ["b"]}}
    assert compile_meta_exclude(cfg, label="t") == []


@pytest.mark.unit
@pytest.mark.parametrize("extractor", sorted(cs.EXTRACTOR_KEYS))
def test_every_extractor_accepts_meta_include(extractor):
    """kind 마다 다르게 동작하면 같은 yaml 이 경로에 따라 컬럼을 달리 낸다."""
    assert "meta_include" in cs.EXTRACTOR_KEYS[extractor]


# ── 기동 시 검증 ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_unknown_field_blocks_startup():
    """값이 만들어지지 않는 이름은 오타다. 조용히 무시하면 빼려던 필드가 그대로 나온다."""
    cfg = {"column_map": {"A": ["a"]}, "meta_include": {"ZZZ": False}}
    with pytest.raises(ValueError, match="ZZZ 를 만드는 설정이 없습니다"):
        compile_meta_exclude(cfg, label="t")


@pytest.mark.unit
def test_non_boolean_blocks_startup():
    cfg = {"column_map": {"A": ["a"]}, "meta_include": {"A": "no"}}
    with pytest.raises(ValueError, match="true 또는 false"):
        compile_meta_exclude(cfg, label="t")


@pytest.mark.unit
def test_field_used_only_as_template_material_is_not_warned(caplog):
    cfg = {
        "column_map": {"BRAND": ["b"], "NAME": ["n"]},
        "derive": {"DISPLAY": "{{BRAND}} {{NAME}}"},
        "meta_include": {"BRAND": False},
    }
    with caplog.at_level("WARNING"):
        assert compile_meta_exclude(cfg, label="t") == ["BRAND"]
    assert "쓰이지 않습니다" not in caplog.text


@pytest.mark.unit
def test_field_used_nowhere_warns_but_still_starts(caplog):
    """죽은 설정은 드러내되 기동은 막지 않는다 — 원천 점검용으로 잠시 빼 두는 쓰임이 있다."""
    cfg = {"column_map": {"A": ["a"]}, "meta_include": {"A": False}}
    with caplog.at_level("WARNING"):
        assert compile_meta_exclude(cfg, label="t") == ["A"]
    assert "쓰이지 않습니다" in caplog.text


# ── 제어키 왕복 ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_control_key_is_absent_when_nothing_is_excluded():
    assert cp.attach_meta_exclude({"A": 1}, []) == {"A": 1}


@pytest.mark.unit
def test_strip_removes_excluded_fields_and_the_control_key_itself():
    meta = cp.attach_meta_exclude({"A": 1, "B": 2}, ["B"])
    assert cp.strip_meta_excluded(meta) == {"A": 1}


# ── 매퍼 배선(행 경로) ───────────────────────────────────────────────────────

_ROWS_CONFIG = """
    schema: v2
    source: {kind: rows}
    fields:
      TITLE: {alias: [제목]}
      BRAND: {alias: [브랜드], meta: false}
      NAME: {alias: [상품명], meta: false}
      DISPLAY_NM:
        template: "{{BRAND}} {{NAME}}"
    body:
      fields: [TITLE, BRAND]
"""

_ROW = {"data": [{
    "sheet_name": "S",
    "data_rows": [{"제목": "공지", "브랜드": "삼성", "상품명": "iD ON"}],
}]}


def _rows_mapper(tmp_path):
    _write(tmp_path / "custom_field_probe.yaml", _ROWS_CONFIG)
    return TabularCustomFieldsMapper(
        doc_type="probe", extractor="tabular_mapping",
        config_file="custom_field_probe.yaml", resource_path=str(tmp_path),
    )


@pytest.mark.unit
def test_mapper_still_builds_the_excluded_values(tmp_path):
    """제외는 출력 단계의 일이다 — 파생값은 제외 필드를 재료로 정상적으로 만들어진다."""
    element = _rows_mapper(tmp_path).to_parse_format(_ROW, "probe")["elements"][0]
    assert element["metadata"]["DISPLAY_NM"] == "삼성 iD ON"
    assert element["metadata"][cp.META_EXCLUDE_KEY] == ["BRAND", "NAME"]


@pytest.mark.unit
def test_excluded_field_still_reaches_the_chunk_body(tmp_path):
    """본문 노출과 메타 적재는 독립이다 — body.fields 에 실린 값은 그대로 본문에 남는다."""
    element = _rows_mapper(tmp_path).to_parse_format(_ROW, "probe")["elements"][0]
    assert "삼성" in element["content"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_chunker_drops_excluded_fields_from_vector_meta(tmp_path):
    """소비자가 실제로 뺐는지를 단정한다 — 제어키가 실렸는지만으로는 배선이 증명되지 않는다."""
    element = _rows_mapper(tmp_path).to_parse_format(_ROW, "probe")["elements"][0]
    vectors = await object.__new__(ChunkProcessor)._chunk_parse_format([element])
    vector = vectors[0].model_dump()

    assert vector["DISPLAY_NM"] == "삼성 iD ON"   # 파생값은 남는다
    assert "BRAND" not in vector                  # 재료는 적재되지 않는다
    assert "NAME" not in vector
    assert cp.META_EXCLUDE_KEY not in vector      # 제어키 자체도 새지 않는다
    assert "삼성" in vector["text"]                # 본문은 그대로다


@pytest.mark.asyncio
@pytest.mark.unit
async def test_chunker_keeps_everything_when_nothing_is_excluded():
    """기본 동작이 바뀌지 않는다 — 제어키가 없는 parse 결과는 종전 그대로 실린다."""
    vectors = await object.__new__(ChunkProcessor)._chunk_parse_format([{
        "category": "custom_fields_row",
        "content": "질문\n답변",
        "page": 1,
        "metadata": {"question": "질문", "answer_text": "답변", "doc_type": "faq"},
    }])
    vector = vectors[0].model_dump()
    assert vector["question"] == "질문"
    assert vector["answer_text"] == "답변"


# ── 문서형 배선 ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_document_kind_normalizes_the_same_way():
    cfg = yaml.safe_load(textwrap.dedent("""
        schema: v2
        source: {kind: document}
        fields:
          SRC: {const: X, meta: false}
        llm:
          - out: [SUMMARY]
            endpoint: {url: "http://dummy/v1", model: m}
    """))
    internal, extractor = cv2.normalize(cfg)
    assert extractor == "llm"
    assert internal["meta_include"] == {"SRC": False}
    cs.validate_known_keys(internal, label="t", extractor=extractor)


@pytest.mark.unit
def test_document_kind_attaches_the_control_key_after_the_value_pipeline(tmp_path):
    """문서형도 값은 다 만든 뒤 제어키만 실어 보낸다 — 파생값은 재료를 그대로 쓴다."""
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import (
        CustomFieldsEnricher,
    )

    _write(tmp_path / "custom_field_doc.yaml", """
        schema: v2
        source: {kind: document}
        fields:
          BRAND: {meta: false}
          NAME: {}
          DISPLAY_NM: {template: "{{BRAND}} {{NAME}}"}
        llm:
          - out: [BRAND, NAME]
            endpoint: {url: "http://dummy/v1/chat/completions", model: m}
            prompt: {user: "{{raw_text}}"}
    """)
    enricher = CustomFieldsEnricher(
        config_file="custom_field_doc.yaml", resource_path=str(tmp_path),
    )
    normalized = enricher._normalize_output_fields({"BRAND": "삼성", "NAME": "iD ON"})

    assert normalized["DISPLAY_NM"] == "삼성 iD ON"
    assert normalized[cp.META_EXCLUDE_KEY] == ["BRAND"]
    # 제어키가 소비 지점을 지나면 재료도 제어키도 남지 않는다(문서 경로의 passthrough 와 같은 함수).
    assert cp.strip_meta_excluded(normalized) == {"NAME": "iD ON", "DISPLAY_NM": "삼성 iD ON"}


# ── intelligent/convert 의 동기 xlsx 경로 ────────────────────────────────────
#
# 이 경로는 청커를 거치지 않고 벡터를 바로 만든다. 같은 yaml 이 프로세서에 따라 컬럼을
# 달리 내지 않도록 여기에도 같은 제외를 건다(판정 규칙은 facade 공용 모듈에 있어
# converters 가 함수로 주입받는다).

@pytest.mark.unit
def test_sync_xlsx_path_applies_the_hook_to_row_metadata(tmp_path):
    xp = pytest.importorskip("genon.preprocessor.processing.converters.xlsx_processor")
    openpyxl = pytest.importorskip("openpyxl")

    path = tmp_path / "rows.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row_index, row in enumerate(
        [["제목", "브랜드", "상품명"], ["공지", "삼성", "iD ON"]], start=1
    ):
        for col_index, value in enumerate(row, start=1):
            sheet.cell(row=row_index, column=col_index, value=value)
    workbook.save(str(path))

    vectors = xp.build_tabular_custom_fields_vectors(
        str(path), _rows_mapper(tmp_path), "probe",
        row_meta_hook=cp.strip_meta_excluded,
    )
    vector = vectors[0].model_dump()
    assert vector["DISPLAY_NM"] == "삼성 iD ON"
    assert "BRAND" not in vector and "NAME" not in vector
    assert cp.META_EXCLUDE_KEY not in vector


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("module_name", ["intelligent_processor", "convert_processor"])
async def test_facades_pass_the_hook_into_the_sync_path(module_name, tmp_path, monkeypatch):
    """facade 가 훅을 실제로 넘기는지 — 인자를 빠뜨리면 그 프로세서에서만 컬럼이 샌다."""
    xp = pytest.importorskip("genon.preprocessor.processing.converters.xlsx_processor")
    module = pytest.importorskip(f"genon.preprocessor.facade.{module_name}")

    captured: dict = {}

    def _spy(*args, **kwargs):
        captured.update(kwargs)
        return ["vector"]

    monkeypatch.setattr(xp, "build_tabular_custom_fields_vectors", _spy)

    processor = object.__new__(module.DocumentProcessor)
    processor._tabular_custom_fields_mappers = [_rows_mapper(tmp_path)]
    processor._xlsx_cfg = {"header_row": 0, "multi_table": False}

    assert await processor._process_xlsx(None, "x.xlsx", doc_type="probe") == ["vector"]
    assert captured["row_meta_hook"] is cp.strip_meta_excluded
