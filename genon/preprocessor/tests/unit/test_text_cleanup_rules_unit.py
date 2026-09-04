"""chunking.text_cleanup.rules — 패턴 기반 청크 텍스트 후처리.

문자 위생(sanitize/tidy)이 "어느 문서에나 안전한 것" 이라면, 규칙은 **그 사이트의 원천에만
있는 노이즈**를 설정으로 지우는 수단이다. 여기서 고정하는 것은 세 가지다.

  1. 액션 3종(line/find/chunk)이 각각 무엇을 지우는가
  2. **지우면 안 되는 것이 남는가** — 코드 블록·마크다운 표 행·표 셀
  3. 잘못된 설정이 요청 때가 아니라 **기동 시** 실패하는가

2번이 이 기능의 위험이다. 삭제가 되는지보다 대조군이 살아남는지가 중요하다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from genon.preprocessor.facade.chunking import text_norm as tn

pytestmark = pytest.mark.unit

NOISE_SAMPLE = Path(__file__).resolve().parents[2] / "sample_files" / "text_cleanup_noise_sample.md"

RULES_CFG = {
    "mode": "safe",
    "rules": [
        {"line": r"^\s*목차\s*$"},
        {"find": r"\[이미지[^\]]*\]", "replace": ""},
        {"chunk": "^본 문서는 참고용"},
    ],
}


def _rules(cfg=None):
    return tn.rules_from_cfg({"text_cleanup": cfg if cfg is not None else RULES_CFG})


# ── 액션 3종 ────────────────────────────────────────────────────────────────

def test_line_rule_removes_the_matching_line():
    assert tn.apply_rules("앞\n목차\n뒤", _rules()) == "앞\n뒤"


def test_find_rule_replaces_in_place():
    assert tn.apply_rules("본문 [이미지1] 끝", _rules()) == "본문  끝"


def test_find_rule_uses_the_given_replacement():
    rules = _rules({"rules": [{"find": r"\[이미지[^\]]*\]", "replace": "(그림)"}]})
    assert tn.apply_rules("본문 [이미지1] 끝", rules) == "본문 (그림) 끝"


def test_chunk_rule_is_a_predicate_not_a_substitution():
    """`chunk` 는 본문을 건드리지 않는다 — 청크를 통째로 버릴지만 판정한다."""
    rules = _rules()
    text = "본 문서는 참고용이며 실제 적용은 인사규정을 따른다."

    assert tn.apply_rules(text, rules) == text
    assert tn.chunk_dropped(text, rules) is True
    assert tn.chunk_dropped("일반 본문", rules) is False


def test_rules_apply_in_declared_order():
    """앞 규칙이 만든 결과를 뒤 규칙이 본다."""
    rules = _rules({"rules": [
        {"find": "가", "replace": "나"},
        {"line": "^나$"},
    ]})
    assert tn.apply_rules("가\n다", rules) == "다"


# ── 대조군: 지우면 안 되는 것 ────────────────────────────────────────────────

def test_code_fence_is_untouched():
    """코드 블록 안은 어떤 규칙도 타지 않는다 — 예제 코드가 훼손된다."""
    text = "목차\n```python\n# 목차\nx = \"[이미지1]\"\n```\n[이미지2]"
    out = tn.apply_rules(text, _rules())

    assert "# 목차" in out and '"[이미지1]"' in out
    assert not out.startswith("목차")
    assert "[이미지2]" not in out


def test_inline_backtick_is_untouched():
    assert tn.apply_rules("설명 `[이미지1]` 참고", _rules()) == "설명 `[이미지1]` 참고"


def test_markdown_table_row_survives_a_line_rule():
    """표 행을 지우면 표가 통째로 깨진다 — `line` 은 표 행을 건너뛴다."""
    text = "| 구분 | 설명 |\n| --- | --- |\n| 목차 | 앞머리 항목 |\n목차"
    out = tn.apply_rules(text, _rules())

    assert out == "| 구분 | 설명 |\n| --- | --- |\n| 목차 | 앞머리 항목 |"


def test_table_cell_rules_drop_line_actions():
    """표 셀은 "줄" 이 아니라 값이라 `line` 을 적용하지 않는다(`find` 는 적용한다)."""
    cell_rules = tn._cell_rules(_rules())

    assert tn.apply_rules("목차", cell_rules) == "목차"
    assert tn.apply_rules("[이미지1]값", cell_rules) == "값"


def test_line_rule_only_matches_whole_lines_as_written():
    """정규식이 `^…$` 로 묶여 있으면 문장 안의 같은 단어는 남는다(설정이 정한다)."""
    assert tn.apply_rules("목차를 참고하세요", _rules()) == "목차를 참고하세요"


# ── 설정 검증 (기동 시) ─────────────────────────────────────────────────────

def test_no_rules_is_the_default_and_costs_nothing():
    assert tn.rules_from_cfg({}) == ()
    assert tn.rules_from_cfg({"text_cleanup": "safe"}) == ()
    assert tn.rules_from_cfg({"text_cleanup": {"mode": "safe"}}) == ()


def test_scalar_notation_still_resolves_the_mode():
    """스칼라 표기는 그대로 유효하다(하위호환)."""
    assert tn.mode_from_cfg({"text_cleanup": "safe"}) == tn.MODE_SAFE
    assert tn.mode_from_cfg({"text_cleanup": "off"}) == tn.MODE_OFF
    assert tn.mode_from_cfg({"text_cleanup": RULES_CFG}) == tn.MODE_SAFE


def test_block_without_mode_is_safe_only_when_it_has_rules():
    """`mode` 를 빠뜨린 블록이 오타 하나로 off 를 safe 로 뒤집지 않게 한다.

    규칙이 있으면 켤 의도가 분명하지만, `{mdoe: safe}` 처럼 아무것도 안 읽히는 블록까지
    켜 주면 규칙 없이 문자 위생만 돌아 청크 본문이 조용히 바뀐다(재색인 영향).
    """
    assert tn.mode_from_cfg({"text_cleanup": {"rules": [{"line": "^x$"}]}}) == tn.MODE_SAFE
    assert tn.mode_from_cfg({"text_cleanup": {"mdoe": "safe"}}) == tn.MODE_OFF
    assert tn.mode_from_cfg({"text_cleanup": {"mode": "off", "rules": [{"line": "^x$"}]}}) == tn.MODE_OFF


def test_block_notation_is_not_accepted_from_request_kwargs():
    """블록 표기는 yaml 에만 있다 — kwargs 로 오면 규칙을 읽는 곳이 없어 반쪽 계약이 된다."""
    assert tn.mode_for({"text_cleanup": RULES_CFG}, "off") == tn.MODE_OFF


def test_bad_regex_fails_at_startup():
    with pytest.raises(ValueError, match="정규식"):
        _rules({"rules": [{"line": "["}]})


def test_unknown_action_key_fails_at_startup():
    with pytest.raises(ValueError, match="lines"):
        _rules({"rules": [{"lines": "x"}]})


def test_exactly_one_action_per_rule():
    with pytest.raises(ValueError, match="액션 키"):
        _rules({"rules": [{"line": "a", "find": "b"}]})
    with pytest.raises(ValueError, match="액션 키"):
        _rules({"rules": [{"replace": "b"}]})


def test_replace_only_goes_with_find():
    with pytest.raises(ValueError, match="replace"):
        _rules({"rules": [{"line": "a", "replace": "b"}]})


def test_rules_must_be_a_list():
    with pytest.raises(ValueError, match="목록"):
        _rules({"rules": {"line": "a"}})


def test_rules_of_tolerates_an_instance_without_the_attribute():
    """`object.__new__` 로 __init__ 을 우회해 만든 스텁 인스턴스를 쓰는 테스트가 있다."""
    stub = object.__new__(type("Stub", (), {}))

    assert tn.rules_of(stub) == ()
    assert tn.rules_of(None) == ()


# ── 실제 문서 ───────────────────────────────────────────────────────────────

def test_noise_sample_keeps_every_control_case():
    """실 샘플 파일로 지울 것과 남길 것을 함께 확인한다."""
    text = NOISE_SAMPLE.read_text(encoding="utf-8")
    out = tn.apply_rules(tn.sanitize(text), _rules())

    # 지워진다
    assert "\n목차\n" not in out
    assert "[이미지1] 휴가" not in out and "[이미지2]" not in out
    # 남는다 — 표 셀, 코드 펜스, 문장 안의 같은 단어
    assert "| 목차 | 문서 앞머리의 항목 목록 |" in out
    assert '# 목차' in out and '"[이미지1]"' in out
    assert "목차를 참고하세요" in out


# ── 청크 경계 반영 (입력 단계 적용이 목적이다) ───────────────────────────────

_CONFIG_NAME = "chunking_processor_config.yaml"


def _make_processor(tmp_path: Path, text_cleanup):
    mod = pytest.importorskip("facade.chunking_processor")
    resource_dir = Path(__file__).resolve().parents[2] / "resource"
    shutil.copytree(resource_dir, tmp_path, dirs_exist_ok=True)
    cfg = yaml.safe_load((resource_dir / _CONFIG_NAME).read_text(encoding="utf-8"))
    cfg.setdefault("chunking", {})["text_cleanup"] = text_cleanup
    cfg["chunking"]["chunk_size"] = 1000
    out = tmp_path / _CONFIG_NAME
    out.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    try:
        return mod.DocumentProcessor(config_path=str(out))
    except Exception as e:  # noqa: BLE001 - 모델/네트워크 등 환경 의존
        pytest.skip(f"DocumentProcessor init unavailable: {e}")


def test_rules_are_reflected_in_chunk_text_and_stats(tmp_path):
    """규칙은 청킹 **입력**에 걸린다 — 삭제가 청크 본문과 n_char 에 반영돼야 한다.

    출력 직전에 걸면 경계와 통계가 이미 확정된 뒤라 삭제량만큼 청크가 작아지고
    통계가 본문과 어긋난다.
    """
    proc = _make_processor(tmp_path, RULES_CFG)
    vectors = proc._chunk_parse_format(
        [{"content": "목차\n본문 [이미지1] 끝", "page": 1, "category": "text"}]
    )

    assert len(vectors) == 1
    text = vectors[0].text
    assert text.startswith("본문") and "[이미지1]" not in text
    assert vectors[0].n_char == len(text)


def test_chunk_rule_drops_the_whole_chunk(tmp_path):
    """`chunk` 규칙에 걸린 청크는 벡터가 되지 않고, 남은 인덱스도 연속이다."""
    proc = _make_processor(tmp_path, RULES_CFG)
    vectors = proc._chunk_parse_format([
        {"content": "첫 청크", "page": 1, "category": "text"},
        {"content": "본 문서는 참고용이며 실제 적용은 인사규정을 따른다.", "page": 2, "category": "text"},
        {"content": "셋째 청크", "page": 3, "category": "text"},
    ])

    assert [v.text for v in vectors] == ["첫 청크", "셋째 청크"]
    assert [v.i_chunk_on_doc for v in vectors] == [0, 1]
    assert all(v.n_chunk_of_doc == 2 for v in vectors)


def test_no_rules_leaves_output_unchanged(tmp_path):
    """기본(규칙 없음)이면 산출이 바뀌지 않는다 — 재색인 영향 없음의 근거다."""
    proc = _make_processor(tmp_path, "safe")
    vectors = proc._chunk_parse_format(
        [{"content": "목차\n본문 [이미지1] 끝", "page": 1, "category": "text"}]
    )

    assert vectors[0].text == "목차\n본문 [이미지1] 끝"
