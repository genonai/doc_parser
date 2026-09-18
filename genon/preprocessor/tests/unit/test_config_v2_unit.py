"""v2 스키마 — 정규화 단위 테스트.

v2 는 새 파이프라인이 아니라 **내부 형태로 번역하는 앞단**이다. 그래서 여기서 고정할
것은 "번역이 정확한가" 하나이고, 동작 동일성은 같은 매퍼를 타는 구조가 보장한다.
"""
import textwrap

import pytest
import yaml

from genon.preprocessor.processing.enrichment import config_schema as cs
from genon.preprocessor.processing.enrichment import config_v2 as cv2
from genon.preprocessor.processing.enrichment.custom_fields_enricher import (
    custom_fields_extractor,
)
from genon.preprocessor.processing.enrichment.tabular_custom_fields import (
    TabularCustomFieldsMapper,
)

pytestmark = pytest.mark.unit


def test_field_spec_must_be_a_dict(tmp_path):
    """`Q:` 처럼 값을 빠뜨리면 null 로 파싱된다 — 단축 표기를 받지 않는 이유다."""
    cfg = tmp_path / "custom_field_bad.yaml"
    cfg.write_text("schema: v2\nsource: {kind: rows}\nfields:\n  Q:\n", encoding="utf-8")
    with pytest.raises(ValueError, match="object 여야"):
        TabularCustomFieldsMapper(
            config_file=cfg.name, resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


def test_missing_schema_line_is_refused(tmp_path):
    """폐기된 v1 표기를 다른 해석 모드로 조용히 받지 않는다."""
    cfg = tmp_path / "custom_field_v1.yaml"
    cfg.write_text("column_map:\n  Q: [질문]\ntext_fields: [Q]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema: v2"):
        TabularCustomFieldsMapper(
            config_file=cfg.name, resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


def test_empty_config_is_not_refused():
    """`config_file` 을 쓰지 않는 등록 블록은 번역할 것이 없다 — 통과해야 한다."""
    assert cv2.load({}, label="t") == ({}, None)


@pytest.mark.parametrize(
    "body, expect",
    [
        ("source:\n  kind: bogus\n", "source.kind"),
        ("source:\n  kind: rows\n  records_at: x\n", "records_at"),
        ("source:\n  kind: rows\nfields:\n  Q: {alias: [질문], typo: 1}\n", "typo"),
        ("source:\n  kind: sections\nfilter:\n  - {field: X, in: [Y]}\n", "filter"),
        # document 도 alias/values/transform/template 을 쓴다(front matter 가 두 번째 원천).
        # kind 제약이 남은 필드 스펙은 records 전용인 collect 뿐이다.
        ("source:\n  kind: document\nfields:\n  Q: {collect: [a]}\n", "collect"),
    ],
)
def test_v2_rejects_malformed_config(body, expect):
    """v2 는 모르는 키·잘못된 자리를 조용히 무시하지 않는다."""
    cfg = yaml.safe_load(textwrap.dedent("schema: v2\n" + body))
    with pytest.raises(cv2.ConfigV2Error, match=expect):
        cv2.normalize(cfg)


def test_preprocess_blocks_reach_the_internal_form():
    """markdown/html 전처리는 enricher 가 아니라 parser 가 소비한다 — 최상위로 풀린다."""
    internal, extractor = cv2.normalize({
        "schema": "v2",
        "source": {"kind": "document",
                   "pre": {"markdown": {"text_fence": True},
                           "html": {"marker_headings": True}}},
        "llm": [{"endpoint": {"url": "u", "model": "m"}, "out": ["A"]}],
    })
    assert extractor == "llm"
    assert internal["markdown"] == {"text_fence": True}
    assert internal["html"] == {"marker_headings": True}


# ── 표기 ↔ 내부 형태 드리프트 가드 ─────────────────────────────────────────

def test_v2_covers_every_v1_key():
    """내부 키를 늘리고 v2 표기를 잊으면 여기서 깨진다.

    두 층이 갈리는 가장 흔한 경로다 — 매퍼가 읽는 키를 추가하고 config_v2 의 매핑 표를
    갱신하지 않으면, 그 키는 어떤 설정으로도 만들 수 없는데 아무도 모른다.
    """
    from genon.preprocessor.processing.enrichment import config_schema as cs

    v1_keys = set().union(*cs.EXTRACTOR_KEYS.values())
    missing = sorted(v1_keys - cv2.COVERED_V1_KEYS)
    assert not missing, (
        f"v2 가 표현하지 못하는 내부 키: {missing}. config_v2 의 매핑 표에 추가하세요."
    )


def test_covered_set_has_no_phantom_keys():
    """반대 방향 — 없어진 내부 키가 covered 에 남으면 위 검사가 헛돈다."""
    from genon.preprocessor.processing.enrichment import config_schema as cs

    v1_keys = set().union(*cs.EXTRACTOR_KEYS.values()) | set(cs.WIRING_KEYS)
    phantom = sorted(cv2.COVERED_V1_KEYS - v1_keys)
    assert not phantom, f"매퍼가 읽지 않는 키가 covered 에 남아 있습니다: {phantom}"


def test_v2_config_still_gets_extractor_level_validation(tmp_path):
    """v2 는 내부 형태로 번역된 뒤 **같은 검증기**를 탄다 — 검증이 두 벌이 되지 않는다.

    json_semantic 은 chunk_prefix_fields 를 읽지 않으므로, v2 의 body.repeat 로 그 키를
    만들면 번역 결과가 extractor 지원키 검사에서 걸려야 한다.
    """
    from genon.preprocessor.processing.enrichment.json_semantic import SemanticJsonMapper

    cfg = tmp_path / "custom_field_s.yaml"
    cfg.write_text(
        "schema: v2\n"
        "source:\n  kind: sections\n  sections: {ksp: {name: 혜택, include: true}}\n"
        "fields:\n  PRODUCT_NM: {alias: [prodNm]}\n"
        "body:\n  repeat: [PRODUCT_NM]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="chunk_prefix_fields"):
        SemanticJsonMapper(
            config_file=cfg.name, resource_path=str(tmp_path),
            doc_type="t", extractor="json_semantic",
        )


def test_mapping_tables_are_single_source():
    """표기 키와 내부 키의 대응표가 한 벌인지 — 한쪽을 지우면 함께 멈춰야 한다."""
    assert set(cv2._SPEC_TO_BLOCK) <= cv2.FIELD_SPEC_KEYS
    assert set(cv2._BODY_TO_V1) == cv2.BODY_KEYS
    assert set(cv2._SOURCE_TO_V1) | {"kind", "table_at", "pre"} == cv2.SOURCE_KEYS


# ── extractor 유도 ──────────────────────────────────────────────────────────

def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.mark.parametrize("kind, body, expected", [
    ("rows", "fields: {A: {alias: [a]}}\nbody: {fields: [A]}", "tabular_mapping"),
    ("records", "fields: {A: {alias: [a]}}\nbody: {fields: [A]}", "json_mapping"),
    ("sections", "fields: {A: {alias: [a]}}", "json_semantic"),
    ("document", "llm: [{out: [A]}]", "llm"),
])
def test_normalize_derives_extractor_from_kind(kind, body, expected):
    """extractor 는 source.kind 에서 정해진다 — 등록 블록에 다시 적을 값이 아니다."""
    cfg = yaml.safe_load(f"schema: v2\nsource: {{kind: {kind}}}\n{body}\n")
    _internal, extractor = cv2.normalize(cfg, label="t")
    assert extractor == expected


def test_normalize_derives_python_for_document_with_python_block():
    """문서형만 값을 만드는 주체가 둘이다. kind 로는 갈리지 않아 python 블록으로 정한다.

    이 갈래가 없으면 파생값이 항상 llm 이라 python 설정이 지원키 검증에서 막혔다 —
    설정에 적은 이름은 `python.file` 인데 메시지에는 `file` 만 나와 역추적이 안 됐다.
    """
    cfg = yaml.safe_load(
        "schema: v2\nsource: {kind: document}\npython: {file: f.py, out: [A]}\n"
    )
    internal, extractor = cv2.normalize(cfg, label="t")
    assert extractor == "python"
    cs.validate_known_keys(internal, label="t", extractor=extractor)


def test_registered_block_may_omit_extractor(tmp_path):
    """등록 블록에서 extractor 를 빼면 config_file 의 kind 에서 유도한다."""
    _write(tmp_path, "custom_field_x.yaml", """\
        schema: v2
        source: {kind: rows}
        fields: {A: {alias: [a]}}
        body: {fields: [A]}
        """)
    block = {"config_file": "custom_field_x.yaml", "resource_path": str(tmp_path)}
    assert custom_fields_extractor(block) == "tabular_mapping"


def test_registered_block_extractor_wins_when_written(tmp_path):
    """적어 둔 값이 있으면 그대로 쓴다 — 유도가 기존 설정의 판정을 바꾸지 않는다."""
    _write(tmp_path, "custom_field_x.yaml", """\
        schema: v2
        source: {kind: rows}
        fields: {A: {alias: [a]}}
        body: {fields: [A]}
        """)
    block = {"config_file": "custom_field_x.yaml", "resource_path": str(tmp_path),
             "extractor": "llm"}
    assert custom_fields_extractor(block) == "llm"


def test_omitted_extractor_reaches_the_enricher(tmp_path):
    """유도값은 필터가 아니라 **생성자까지** 닿아야 한다.

    필터에만 쓰면 생성자 기본값(llm)으로 지원키를 대조해, python 설정이 file/callable
    때문에 기동에서 막힌다 — 템플릿이 "extractor 를 적지 말라"고 안내하는 만큼 이 배선이
    끊기면 안내가 곧 기동 실패가 된다.
    """
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import (
        build_document_custom_fields_enrichers,
    )

    _write(tmp_path, "custom_field_x.yaml", """\
        schema: v2
        source: {kind: document}
        python: {file: custom_field_x.py, callable: extract, out: [TARGET_A]}
        """)
    _write(tmp_path, "custom_field_x.py",
           "def extract(**kwargs):\n    return {'TARGET_A': 'v'}\n")
    block = {"doc_type": "t", "config_file": "custom_field_x.yaml",
             "resource_path": str(tmp_path)}
    enrichers = build_document_custom_fields_enrichers([block])
    assert [e._extractor for e in enrichers] == ["python"]
    assert enrichers[0]._output_fields == ["TARGET_A"]


def test_omitted_extractor_keeps_row_configs_out_of_document_builder(tmp_path):
    """유도가 문서형 빌더의 필터 판정을 넓히지 않는다(rows 설정은 계속 제외)."""
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import (
        build_document_custom_fields_enrichers,
    )

    _write(tmp_path, "custom_field_r.yaml", """\
        schema: v2
        source: {kind: rows}
        fields: {A: {alias: [a]}}
        body: {fields: [A]}
        """)
    block = {"doc_type": "r", "config_file": "custom_field_r.yaml",
             "resource_path": str(tmp_path)}
    assert build_document_custom_fields_enrichers([block]) == []


def test_extractor_falls_back_to_llm_when_underivable(tmp_path):
    """유도가 실패해도 여기서 기동을 막지 않는다 — 빌더 필터라 남의 오류로 죽으면 안 된다.

    설정 오류는 매퍼·enricher 생성 시점에 제대로 보고된다.
    """
    assert custom_fields_extractor({}) == "llm"
    assert custom_fields_extractor({"config_file": "없는파일.yaml",
                                    "resource_path": str(tmp_path)}) == "llm"


# ── source.pre 공통 스위치 ──────────────────────────────────────────────────

def _pre(text):
    internal, _ = cv2.normalize(yaml.safe_load(textwrap.dedent(text)), label="t")
    return {k: internal.get(k) for k in ("markdown", "html") if k in internal}


def test_pre_shared_marker_headings_fans_out():
    """md 와 html 이 판정 규칙을 공유하므로 한 번만 적게 한다."""
    assert _pre("""\
        schema: v2
        source: {kind: document, pre: {marker_headings: true}}
        llm: [{out: [A]}]
        """) == {"markdown": {"marker_headings": True},
                 "html": {"marker_headings": True}}


def test_pre_block_overrides_shared_switch():
    """세밀한 지정이 뭉뚱그린 지정을 덮는다 — 그 반대는 예측하기 어렵다."""
    assert _pre("""\
        schema: v2
        source:
          kind: document
          pre: {marker_headings: true, html: {marker_headings: false}}
        llm: [{out: [A]}]
        """) == {"markdown": {"marker_headings": True},
                 "html": {"marker_headings": False}}


def test_pre_shared_switch_keeps_other_block_keys():
    """공통 스위치를 펼치면서 블록의 다른 키를 지우지 않는다."""
    assert _pre("""\
        schema: v2
        source:
          kind: document
          pre: {marker_headings: true, markdown: {text_fence: true}}
        llm: [{out: [A]}]
        """) == {"markdown": {"marker_headings": True, "text_fence": True},
                 "html": {"marker_headings": True}}


def test_pre_shared_switch_preserves_disabled_block():
    """`markdown: false` 는 명시적 비활성이므로 dict 로 바꿔치지 않는다."""
    assert _pre("""\
        schema: v2
        source: {kind: document, pre: {marker_headings: true, markdown: false}}
        llm: [{out: [A]}]
        """) == {"markdown": False, "html": {"marker_headings": True}}


def test_pre_typo_is_still_refused():
    """공통 스위치를 추가해도 오타는 계속 막는다."""
    with pytest.raises(cv2.ConfigV2Error, match="marker_headings"):
        _pre("""\
            schema: v2
            source: {kind: document, pre: {marker_heading: true}}
            llm: [{out: [A]}]
            """)


def test_shipped_blocks_derive_their_extractor():
    """출고 등록 블록은 `extractor` 를 적지 않는다 — 유도가 반드시 성공해야 한다.

    유도는 config_file 을 읽어야 하므로, 그 파일이 `schema: v2` 를 잃거나 읽히지 않으면
    None 이 되고 하위호환 폴백으로 `llm` 이 된다. rows/records 설정이 그렇게 되면 기동이
    실패하는데, 원인이 "설정 파일을 못 읽었다" 로 드러나지 않아 짚기 어렵다.
    """
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import (
        _derive_extractor,
    )
    from shipped_config import PREPROCESSOR_DIR

    configs = (
        "parser_processor_config.yaml", "parser_processor_config_simple.yaml",
        "intelligent_processor_config.yaml", "convert_processor_config.yaml",
        "chunking_processor_config.yaml", "chunking_processor_config_simple.yaml",
        "attachment_processor_config.yaml",
    )
    checked = 0
    for resource_dir in ("resource", "resource_dev"):
        root = PREPROCESSOR_DIR / resource_dir
        for name in configs:
            path = root / name
            if not path.exists():
                continue
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for item in (loaded.get("enrichment") or []):
                block = (item or {}).get("custom_fields")
                if not isinstance(block, dict):
                    continue
                assert "extractor" not in block, (
                    f"{resource_dir}/{name} [doc_type={block.get('doc_type')}]: "
                    f"extractor 는 source.kind 에서 유도된다 — 등록 블록에 적지 않는다"
                )
                config_file = str(block.get("config_file") or "")
                if not config_file:
                    continue  # 인라인 설정(문서형 데모). 유도할 원천이 없다
                assert _derive_extractor(config_file, str(root)) is not None, (
                    f"{resource_dir}/{name} [doc_type={block.get('doc_type')}]: "
                    f"{config_file} 에서 extractor 를 유도할 수 없다"
                )
                checked += 1
    assert checked >= 50, f"검사한 블록이 너무 적다({checked}) — 경로가 틀렸을 수 있다"


# ── source.pre.json ────────────────────────────────────────────────────────

def test_pre_json_translates_to_internal_names():
    """`source.pre.json` 은 JsonTextSpec 이 읽는 내부 이름으로 번역된다.

    안쪽 이름을 v2 어휘로 바꾼 이유 — `text_fields` 는 이미 `body.fields` 의 내부 이름이라
    그대로 두면 같은 파일에서 같은 단어가 두 뜻이 되고, `missing_policy` 의 사용자 이름은
    다른 자리에서 이미 `on_missing` 이다.
    """
    internal, extractor = cv2.normalize(yaml.safe_load(textwrap.dedent("""\
        schema: v2
        source:
          kind: document
          pre:
            json:
              body_from: [html, summary_md]
              format: auto
              on_missing: skip
        llm: [{out: [A]}]
        """)), label="t")
    assert internal["json"] == {
        "text_fields": ["html", "summary_md"],
        "missing_policy": "skip",
        "format": "auto",
    }
    cs.validate_known_keys(internal, label="t", extractor=extractor)


def test_pre_json_requires_body_from():
    """소비 지점도 같은 검사를 하지만 그쪽은 내부 이름으로 알린다."""
    with pytest.raises(cv2.ConfigV2Error, match="source.pre.json.body_from"):
        cv2.normalize(yaml.safe_load(
            "schema: v2\nsource: {kind: document, pre: {json: {format: auto}}}\n"
            "llm: [{out: [A]}]\n"), label="t")


@pytest.mark.parametrize("old_key", ["text_fields", "missing_policy"])
def test_pre_json_refuses_internal_names(old_key):
    """설정 파일에는 내부 이름을 받지 않는다 — 한 자리에 이름이 둘이면 안 된다."""
    with pytest.raises(cv2.ConfigV2Error, match=old_key):
        cv2.normalize(yaml.safe_load(
            f"schema: v2\nsource: {{kind: document, pre: {{json: {{body_from: [h], "
            f"{old_key}: skip}}}}}}\nllm: [{{out: [A]}}]\n"), label="t")


def test_pre_json_reaches_the_spec(tmp_path):
    """설정 파일에 적은 json 이 파싱 라우팅이 쓰는 스펙까지 닿는다."""
    from genon.preprocessor.processing.common.parser_config import build_json_text_specs

    _write(tmp_path, "custom_field_x.yaml", """\
        schema: v2
        source:
          kind: document
          pre:
            json:
              body_from: [html, summary_md]
        llm: [{out: [A]}]
        """)
    block = {"doc_type": "card", "config_file": "custom_field_x.yaml",
             "resource_path": str(tmp_path)}
    specs = build_json_text_specs([block])
    assert [s.text_fields for s in specs] == [["html", "summary_md"]]
    assert specs[0].doc_types == ("card",)


def test_registered_block_json_is_refused(tmp_path):
    """등록 블록의 옛 자리는 막는다.

    등록 블록은 기동 시 키 검증을 받지 않아(설정 파일만 받는다) 그대로 두면 오류가 아니라
    조용히 무시되고, 본문이 캐치올로 빠져 표·heading 구조가 소실된다.
    """
    from genon.preprocessor.processing.common.parser_config import build_json_text_specs

    _write(tmp_path, "custom_field_x.yaml", """\
        schema: v2
        source: {kind: document}
        llm: [{out: [A]}]
        """)
    block = {"doc_type": "card", "config_file": "custom_field_x.yaml",
             "resource_path": str(tmp_path), "json": {"text_fields": ["html"]}}
    with pytest.raises(ValueError, match="source.pre.json"):
        build_json_text_specs([block])


# product_hpp 는 빠졌다 — 사이트 설정이 `kind: sections` 로 바뀌어 JSON 을 섹션 매퍼가
# 직접 읽는다. 문서형 본문 키(`source.pre.json`)를 쓰지 않으므로 검사 대상이 아니다.
@pytest.mark.parametrize("resource_dir, name, doc_types", [
    ("resource", "custom_field_card.yaml", ("card",)),
    ("resource", "custom_field_research_report.yaml", ("research_report",)),
    ("resource_dev", "custom_field_card.yaml", ("card",)),
])
def test_shipped_configs_keep_json_body_keys(resource_dir, name, doc_types):
    """등록 블록에서 옮겨 온 본문 키가 출고 설정에 그대로 남아 있어야 한다.

    이 블록이 사라지면 .json 입력이 캐치올로 빠져 표·heading 구조가 소실되는데,
    파싱은 성공하므로 티가 나지 않는다.
    """
    from shipped_config import load_shipped_named

    internal = load_shipped_named(name, resource_dir)
    assert internal.get("json") == {
        "text_fields": ["html", "summary_md"],
        "missing_policy": "skip",
        "format": "auto",
    }, f"{resource_dir}/{name}"


def test_pre_json_is_document_only(tmp_path):
    """공용 해석기로 옮긴 덕에 문서형 게이트가 붙는다(전에는 rows 에 붙여도 통과했다)."""
    from genon.preprocessor.processing.common.parser_config import build_json_text_specs

    _write(tmp_path, "custom_field_r.yaml", """\
        schema: v2
        source:
          kind: rows
          pre:
            json:
              body_from: [html]
        fields: {A: {alias: [a]}}
        body: {fields: [A]}
        """)
    block = {"doc_type": "r", "config_file": "custom_field_r.yaml",
             "resource_path": str(tmp_path)}
    assert build_json_text_specs([block]) == []


# ── 배포 전 점검 ────────────────────────────────────────────────────────────

def _load_script(name: str):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "examples" / "config_precheck" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    import sys as _sys

    _sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_precheck_understands_v2_configs(tmp_path):
    """배포 전 점검이 v2 설정을 내부 키로 검사해 전건 실패로 보면 안 된다."""
    precheck = _load_script("precheck_custom_fields.py")
    (tmp_path / "custom_field_x.yaml").write_text(
        "schema: v2\nsource: {kind: rows}\nfields:\n  Q: {alias: [질문]}\n"
        "body:\n  fields: [Q]\n",
        encoding="utf-8",
    )
    block = {"doc_type": "t", "extractor": "tabular_mapping",
             "config_file": "custom_field_x.yaml"}
    assert precheck.check_block("cfg.yaml", block, tmp_path, set()) == []


def test_precheck_accepts_python_extractor(tmp_path):
    """값을 고객 파이썬 함수로 만드는 설정을 거짓 기동실패로 보고하면 안 된다.

    점검이 등록 블록의 extractor 를 kind 파생값으로 덮어써서, 파생값이 항상 llm 인
    문서형 설정이 file/callable 때문에 실패로 잡혔다.
    """
    precheck = _load_script("precheck_custom_fields.py")
    (tmp_path / "custom_field_x.yaml").write_text(
        "schema: v2\nsource: {kind: document}\n"
        "python: {file: custom_field_x.py, callable: extract, out: [A]}\n",
        encoding="utf-8",
    )
    block = {"doc_type": "t", "extractor": "python",
             "config_file": "custom_field_x.yaml"}
    assert precheck.check_block("cfg.yaml", block, tmp_path, set()) == []


def test_precheck_derives_omitted_extractor(tmp_path):
    """등록 블록이 extractor 를 빼면 점검도 기동과 같은 순서로 유도해야 한다."""
    precheck = _load_script("precheck_custom_fields.py")
    (tmp_path / "custom_field_x.yaml").write_text(
        "schema: v2\nsource: {kind: rows}\nfields:\n  Q: {alias: [질문]}\n"
        "body:\n  fields: [Q]\n",
        encoding="utf-8",
    )
    block = {"doc_type": "t", "config_file": "custom_field_x.yaml"}
    assert precheck.check_block("cfg.yaml", block, tmp_path, set()) == []


def test_precheck_refuses_registered_block_json(tmp_path):
    """옛 자리를 배포 전에 잡는다 — 기동은 파서 경로에서만 막으므로 점검이 더 넓다."""
    precheck = _load_script("precheck_custom_fields.py")
    (tmp_path / "custom_field_x.yaml").write_text(
        "schema: v2\nsource: {kind: document}\nllm: [{out: [A]}]\n", encoding="utf-8")
    block = {"doc_type": "card", "extractor": "llm",
             "config_file": "custom_field_x.yaml",
             "json": {"text_fields": ["html"]}}
    problems = precheck.check_block("cfg.yaml", block, tmp_path, set())
    assert any("source.pre.json" in p and p.startswith("[기동실패]") for p in problems), problems


def test_precheck_refuses_v1_notation(tmp_path):
    """기동에서 막히는 설정은 배포 전 점검에서도 막혀야 한다."""
    precheck = _load_script("precheck_custom_fields.py")
    (tmp_path / "custom_field_x.yaml").write_text(
        "column_map:\n  Q: [질문]\ntext_fields: [Q]\n", encoding="utf-8"
    )
    block = {"doc_type": "t", "extractor": "tabular_mapping",
             "config_file": "custom_field_x.yaml"}
    problems = precheck.check_block("cfg.yaml", block, tmp_path, set())
    assert any("schema: v2" in p and p.startswith("[기동실패]") for p in problems), problems
