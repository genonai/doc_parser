"""검증 정책 스위치와 배포 전 점검 스크립트 단위 테스트.

두 기능 모두 "고객사이트에 올리기 전에 문제를 드러낸다"는 한 가지 목적을 갖는다.
정책은 기동 실패를 경고로 낮추는 안전판이고, 점검 스크립트는 그 안전판이 필요한지
배포 전에 판단하게 해 준다.
"""
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from genon.preprocessor.facade.enrichment import config_schema as cs
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    TabularCustomFieldsMapper,
)

pytestmark = pytest.mark.unit

_PREPROC = Path(__file__).resolve().parents[2]
_SCRIPT = _PREPROC / "examples" / "config_precheck" / "precheck_custom_fields.py"


def _load_precheck():
    spec = importlib.util.spec_from_file_location("precheck_custom_fields", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bad_mapper(tmp_path):
    cfg = tmp_path / "custom_field_x.yaml"
    cfg.write_text(
        "column_maps:\n  Q: [질문]\ncolumn_map:\n  Q: [질문]\ntext_fields: [Q]\n",
        encoding="utf-8",
    )
    return TabularCustomFieldsMapper(
        config_file=cfg.name, resource_path=str(tmp_path),
        doc_type="t", extractor="tabular_mapping",
    )


# ── 검증 정책 스위치 ────────────────────────────────────────────────────────

def test_default_policy_blocks_startup(tmp_path, monkeypatch):
    monkeypatch.delenv(cs.VALIDATION_POLICY_ENV, raising=False)
    with pytest.raises(ValueError, match="column_maps"):
        _bad_mapper(tmp_path)


def test_warn_policy_starts_and_keeps_working(tmp_path, monkeypatch, caplog):
    """운영 반영 첫 릴리스용 안전판 — 기동은 시키되 로그로 알린다."""
    monkeypatch.setenv(cs.VALIDATION_POLICY_ENV, "warn")
    with caplog.at_level("WARNING"):
        mapper = _bad_mapper(tmp_path)
    assert "column_maps" in caplog.text
    rows = mapper.build_fields(
        {"data": [{"sheet_name": "S", "data_rows": [{"질문": "Q1"}]}]}, "t"
    )
    assert rows[0]["Q"] == "Q1"


@pytest.mark.parametrize("value", ["", "typo", "ERROR"])
def test_unknown_policy_falls_back_to_error(tmp_path, monkeypatch, value):
    """모르는 값으로 검증이 조용히 꺼지면 안 된다."""
    monkeypatch.setenv(cs.VALIDATION_POLICY_ENV, value)
    with pytest.raises(ValueError):
        _bad_mapper(tmp_path)


# ── 배포 전 점검 스크립트 ───────────────────────────────────────────────────

def test_precheck_passes_on_shipped_resource():
    """출고 설정은 그대로 통과해야 한다(스크립트가 오탐을 내면 쓸모가 없다)."""
    precheck = _load_precheck()
    root = _PREPROC / "resource"
    blocks = precheck.registered_blocks(root)
    assert len(blocks) >= 15
    seen: set = set()
    problems = [p for source, block in blocks
                for p in precheck.check_block(source, block, root, seen)]
    assert not problems, problems


@pytest.mark.parametrize(
    "mutate, expect",
    [
        # 오타 — 예전에는 완전 무증상이었다
        (lambda c: c.update({"column_maps": {"Q": ["질문"]}}), "column_maps"),
        # 제거된 키
        (lambda c: c.update({"nulls": ["BIZ_ID"]}), "nulls"),
        (lambda c: c.update({"json_text_fields": {"D": "SRC"}}), "json_text_fields"),
    ],
)
def test_precheck_detects_blocking_problems(tmp_path, mutate, expect):
    precheck = _load_precheck()
    cfg = {"column_map": {"Q": ["질문"]}, "text_fields": ["Q"]}
    mutate(cfg)
    (tmp_path / "custom_field_x.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8"
    )
    block = {"doc_type": "t", "extractor": "tabular_mapping",
             "config_file": "custom_field_x.yaml"}
    problems = precheck.check_block("cfg.yaml", block, tmp_path, set())
    assert any(expect in p and p.startswith("[기동실패]") for p in problems), problems


def test_precheck_detects_removed_extractor_alias(tmp_path):
    precheck = _load_precheck()
    (tmp_path / "custom_field_x.yaml").write_text(
        "column_map:\n  Q: [질문]\ntext_fields: [Q]\n", encoding="utf-8"
    )
    block = {"doc_type": "t", "extractor": "column_mapping",
             "config_file": "custom_field_x.yaml"}
    problems = precheck.check_block("cfg.yaml", block, tmp_path, set())
    assert any("column_mapping" in p and "tabular_mapping" in p for p in problems), problems


def test_precheck_flags_chunk_body_change(tmp_path):
    """라벨 폴백 제거로 본문이 바뀌는 필드를 배포 전에 알려야 한다(재색인 판단)."""
    precheck = _load_precheck()
    problems = precheck.check_body_label_change("x.yaml", {
        "column_map": {"Q": ["질문"]},
        "text_fields": ["Q", "SUMMARY_TEXT"],
    })
    assert problems and "SUMMARY_TEXT" in problems[0]
    # field_labels 로 이름을 주면 변화가 없다.
    assert not precheck.check_body_label_change("x.yaml", {
        "column_map": {"Q": ["질문"]},
        "text_fields": ["Q", "SUMMARY_TEXT"],
        "field_labels": {"SUMMARY_TEXT": "안내요약"},
    })
