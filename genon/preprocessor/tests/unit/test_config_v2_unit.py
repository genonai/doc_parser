"""v2 스키마 — 정규화·역변환·병행 검증 단위 테스트.

v2 는 새 파이프라인이 아니라 **내부(v1) 형태로 번역하는 앞단**이다. 그래서 여기서 고정할
것은 "번역이 정확한가" 하나이고, 동작 동일성은 같은 매퍼를 타는 구조가 보장한다.
"""
import textwrap

import pytest
import yaml

from genon.preprocessor.facade.enrichment import config_v2 as cv2
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    TabularCustomFieldsMapper,
)

pytestmark = pytest.mark.unit

_V1 = {
    "column_map": {"QUESTION": ["질문"], "ANSWER": ["답변"]},
    "value_map": {"SEARCHABLE_YN": {"Y": ["노출"]}},
    "defaults": {"SEARCHABLE_YN": "N"},
    "constants": {"GROUP_C": "HPP"},
    "transforms": {"MOD_DT": "date_int_flex"},
    "html_text_fields": {"DETAIL_TEXT": "DETAIL_HTML"},
    "required": ["QUESTION"],
    "text_fields": ["QUESTION", "ANSWER"],
    "field_labels": {"QUESTION": "질문"},
    "split": True,
    "chunk_prefix_fields": ["QUESTION"],
}


def test_round_trip_preserves_every_key():
    """v1 → v2 → v1 왕복이 원본과 같아야 v2 가 그 설정을 온전히 표현한다는 뜻이다."""
    as_v2 = cv2.to_v2(_V1, "tabular_mapping")
    back, extractor = cv2.normalize(as_v2)
    assert extractor == "tabular_mapping"
    assert back == _V1


def test_field_rules_collapse_into_one_spec():
    """한 필드의 규칙이 6개 블록에 흩어지던 것이 한 dict 로 모인다(v2 의 존재 이유)."""
    as_v2 = cv2.to_v2(_V1, "tabular_mapping")
    assert as_v2["fields"]["SEARCHABLE_YN"] == {"values": {"Y": ["노출"]}, "default": "N"}
    assert as_v2["fields"]["DETAIL_TEXT"] == {"from": "DETAIL_HTML", "as": "html"}
    assert set(as_v2) <= cv2.TOP_LEVEL_KEYS


def test_to_v2_refuses_to_drop_unknown_keys():
    """옮기지 못한 키를 조용히 버리면 왕복 검증이 통과해 버린다 — 반드시 알려야 한다."""
    with pytest.raises(cv2.ConfigV2Error, match="옮기지 못한"):
        cv2.to_v2({**_V1, "someday_key": 1}, "tabular_mapping")


def test_v2_config_produces_same_fields_as_v1(tmp_path):
    """같은 입력에 같은 결과 — v2 는 같은 매퍼 코드를 탄다."""
    (tmp_path / "custom_field_v1.yaml").write_text(
        yaml.safe_dump(_V1, allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "custom_field_v2.yaml").write_text(
        yaml.safe_dump(cv2.to_v2(_V1, "tabular_mapping"), allow_unicode=True), encoding="utf-8"
    )
    payload = {"data": [{"sheet_name": "S", "data_rows": [
        {"질문": "가입 방법은?", "답변": "앱에서"}]}]}

    def rows(name):
        mapper = TabularCustomFieldsMapper(
            config_file=name, resource_path=str(tmp_path),
            doc_type="faq", extractor="tabular_mapping",
        )
        return mapper.to_parse_format_from_fields(mapper.build_fields(payload, "faq"), "faq")

    assert rows("custom_field_v1.yaml") == rows("custom_field_v2.yaml")


def test_field_spec_must_be_a_dict(tmp_path):
    """`Q:` 처럼 값을 빠뜨리면 null 로 파싱된다 — v1 에서는 조용히 통과했다."""
    cfg = tmp_path / "custom_field_bad.yaml"
    cfg.write_text("schema: v2\nsource: {kind: rows}\nfields:\n  Q:\n", encoding="utf-8")
    with pytest.raises(ValueError, match="object 여야"):
        TabularCustomFieldsMapper(
            config_file=cfg.name, resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


@pytest.mark.parametrize(
    "body, expect",
    [
        ("source:\n  kind: bogus\n", "source.kind"),
        ("source:\n  kind: rows\n  records_at: x\n", "records_at"),
        ("source:\n  kind: rows\nfields:\n  Q: {alias: [질문], typo: 1}\n", "typo"),
        ("source:\n  kind: rows\nfilter:\n  - {field: X, in: [Y]}\n", "filter"),
        ("source:\n  kind: document\nfields:\n  Q: {alias: [질문]}\n", "alias"),
    ],
)
def test_v2_rejects_malformed_config(body, expect):
    """v2 는 모르는 키·잘못된 자리를 조용히 무시하지 않는다."""
    cfg = yaml.safe_load(textwrap.dedent("schema: v2\n" + body))
    with pytest.raises(cv2.ConfigV2Error, match=expect):
        cv2.normalize(cfg)


def test_preprocess_blocks_survive_round_trip():
    """markdown/html 전처리는 parser 가 소비한다 — v2 는 source.pre 에 담고 그대로 되돌린다."""
    v1 = {"url": "u", "model": "m", "output_fields": ["A"],
          "markdown": {"text_fence": True}, "html": {"marker_headings": True}}
    as_v2 = cv2.to_v2(v1, "llm")
    assert as_v2["source"]["pre"] == {"markdown": {"text_fence": True},
                                      "html": {"marker_headings": True}}
    back, _ = cv2.normalize(as_v2)
    assert back == v1


# ── v1 ↔ v2 드리프트 가드 ───────────────────────────────────────────────────

def test_v2_covers_every_v1_key():
    """v1 에 새 키를 넣고 v2 를 잊으면 여기서 깨진다.

    두 스키마가 갈리는 가장 흔한 경로다 — v1 에 키를 추가하고 config_v2 의 매핑 표를
    갱신하지 않으면, 그 키를 쓴 설정은 v2 로 옮길 수 없는데 아무도 모른다.
    """
    from genon.preprocessor.facade.enrichment import config_schema as cs

    v1_keys = set().union(*cs.EXTRACTOR_KEYS.values())
    missing = sorted(v1_keys - cv2.COVERED_V1_KEYS)
    assert not missing, (
        f"v2 가 표현하지 못하는 v1 키: {missing}. config_v2 의 매핑 표에 추가하세요."
    )


def test_covered_set_has_no_phantom_keys():
    """반대 방향 — 없어진 v1 키가 covered 에 남으면 왕복 검증이 헛돈다."""
    from genon.preprocessor.facade.enrichment import config_schema as cs

    v1_keys = set().union(*cs.EXTRACTOR_KEYS.values()) | set(cs.WIRING_KEYS)
    phantom = sorted(cv2.COVERED_V1_KEYS - v1_keys)
    assert not phantom, f"v1 에 없는 키가 covered 에 남아 있습니다: {phantom}"


def test_v2_config_still_gets_extractor_level_validation(tmp_path):
    """v2 는 내부 형태로 번역된 뒤 **같은 검증기**를 탄다 — 검증이 두 벌이 되지 않는다.

    json_semantic 은 text_fields 를 읽지 않으므로, v2 의 body.fields 로 그 키를 만들면
    번역 결과가 extractor 지원키 검사에서 걸려야 한다.
    """
    from genon.preprocessor.facade.enrichment.json_semantic import SemanticJsonMapper

    cfg = tmp_path / "custom_field_s.yaml"
    cfg.write_text(
        "schema: v2\n"
        "source:\n  kind: sections\n  sections: {ksp: {name: 혜택, include: true}}\n"
        "fields:\n  PRODUCT_NM: {alias: [prodNm]}\n"
        "body:\n  fields: [PRODUCT_NM]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="text_fields"):
        SemanticJsonMapper(
            config_file=cfg.name, resource_path=str(tmp_path),
            doc_type="t", extractor="json_semantic",
        )


def test_mapping_tables_are_single_source(tmp_path):
    """양방향이 같은 표를 쓰는지 — 한쪽 표를 지우면 반대 방향도 함께 멈춰야 한다."""
    assert set(cv2._BLOCK_TO_SPEC.values()) <= cv2.FIELD_SPEC_KEYS
    assert set(cv2._BODY_TO_V1) == cv2.BODY_KEYS
    assert set(cv2._SOURCE_TO_V1) | {"kind", "table_at", "pre"} == cv2.SOURCE_KEYS


# ── 변환 스크립트 (C1 3단계) ────────────────────────────────────────────────

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


def test_migration_preview_does_not_touch_files(tmp_path):
    """기본이 미리보기여야 한다 — 실수로 돌려도 파일이 안 바뀐다."""
    migrate = _load_script("migrate_to_v2.py")
    path = tmp_path / "custom_field_x.yaml"
    original = "column_map:\n  Q: [질문]\ntext_fields: [Q]\n"
    path.write_text(original, encoding="utf-8")

    status, _note = migrate.migrate_one(path, "tabular_mapping", tmp_path, write=False)
    assert status == "OK"
    assert path.read_text(encoding="utf-8") == original


def test_migration_writes_readable_v2(tmp_path):
    """기록한 v2 가 다시 읽혀 같은 내부 형태가 되어야 한다."""
    migrate = _load_script("migrate_to_v2.py")
    path = tmp_path / "custom_field_x.yaml"
    path.write_text(
        "# 설명 주석\ncolumn_map:\n  Q: [질문, 대표질문]\ntext_fields: [Q]\n"
        "defaults:\n  S: \"N\"\n",
        encoding="utf-8",
    )
    before = cv2.normalize(cv2.to_v2(yaml.safe_load(path.read_text(encoding="utf-8")),
                                     "tabular_mapping"))[0]

    status, _note = migrate.migrate_one(path, "tabular_mapping", tmp_path, write=True)
    assert status == "WRITE"

    text = path.read_text(encoding="utf-8")
    assert "schema: v2" in text
    assert "alias: [질문, 대표질문]" in text   # 짧은 목록은 한 줄로
    assert "# 설명 주석" in text               # 원본 주석을 머리말로 보존
    assert cv2.normalize(yaml.safe_load(text))[0] == before


def test_migration_refuses_when_equivalence_fails(tmp_path, monkeypatch):
    """왕복이 어긋나면 기록하지 않는다 — 검증을 통과한 것만 고친다."""
    migrate = _load_script("migrate_to_v2.py")
    path = tmp_path / "custom_field_x.yaml"
    original = "column_map:\n  Q: [질문]\ntext_fields: [Q]\n"
    path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(migrate, "compare_configs",
                        lambda *a, **k: ["[왕복불일치] 일부러 만든 실패"])
    status, note = migrate.migrate_one(path, "tabular_mapping", tmp_path, write=True)
    assert status == "FAIL"
    assert "왕복불일치" in note
    assert path.read_text(encoding="utf-8") == original


def test_precheck_understands_v2_configs(tmp_path):
    """배포 전 점검이 v2 설정을 v1 키로 검사해 전건 실패로 보면 안 된다."""
    precheck = _load_script("precheck_custom_fields.py")
    (tmp_path / "custom_field_x.yaml").write_text(
        "schema: v2\nsource: {kind: rows}\nfields:\n  Q: {alias: [질문]}\n"
        "body:\n  fields: [Q]\n",
        encoding="utf-8",
    )
    block = {"doc_type": "t", "extractor": "tabular_mapping",
             "config_file": "custom_field_x.yaml"}
    assert precheck.check_block("cfg.yaml", block, tmp_path, set()) == []


# ── 해석된 상태 비교 (C1 4단계) ─────────────────────────────────────────────

def _verify_module():
    return _load_script("verify_v2_equivalence.py")


_LLM_CFG = {
    "url": "u", "model": "m", "max_tokens": 4000, "temperature": 0.0, "timeout": 300,
    "output_fields": ["A", "B"], "constants": {"G": "HPP"},
    "system_prompt": "시스템 프롬프트", "user_prompt": "{{raw_text}}",
    "body_fields": ["CONTENT"], "chunk_prefix_fields": ["A"], "field_labels": {"A": "제목"},
}


def test_resolved_state_matches_for_document_extractor(tmp_path):
    """문서형(llm)은 매핑 산출이 없어 해석된 상태 비교가 유일한 결정적 검증이다."""
    verify = _verify_module()
    assert verify.compare_resolved_state("t", _LLM_CFG, "llm", tmp_path) == []


@pytest.mark.parametrize(
    "break_it, expect",
    [
        (lambda m: m._LLM_PROMPT_KEYS.pop("system_prompt"), "_system_prompt"),
        (lambda m: m._BODY_TO_V1.pop("mirror_to"), "_body_fields"),
    ],
)
def test_resolved_state_detects_dropped_translation(tmp_path, monkeypatch, break_it, expect):
    """번역이 값을 흘리면 잡아야 한다 — 왕복 검증만으로는 안 잡히는 종류다."""
    verify = _verify_module()
    monkeypatch.setattr(cv2, "_LLM_PROMPT_KEYS", dict(cv2._LLM_PROMPT_KEYS))
    monkeypatch.setattr(cv2, "_BODY_TO_V1", dict(cv2._BODY_TO_V1))
    break_it(cv2)
    problems = verify.compare_resolved_state("t", _LLM_CFG, "llm", tmp_path)
    assert any(expect in p for p in problems), problems


def test_state_comparison_ignores_representation_noise(tmp_path):
    """dict 키 순서와 객체 메모리 주소는 값이 아니다 — 헛경보를 내면 게이트가 무시된다."""
    verify = _verify_module()

    class Holder:
        def __init__(self, mapping):
            self.mapping = mapping

    left = verify._describe({"a": 1, "b": Holder({"x": 1, "y": 2})})
    right = verify._describe({"b": Holder({"y": 2, "x": 1}), "a": 1})
    assert left == right
    # 값이 실제로 다르면 달라야 한다.
    assert left != verify._describe({"a": 1, "b": Holder({"x": 9, "y": 2})})
