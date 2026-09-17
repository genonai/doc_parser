"""model_presets 프리셋 해석 단위 테스트.

최상위 `model_presets:` 에 모델 한 벌을 정의하고 블록에서 `model_preset:` 으로 참조하는
경로를 고정한다. 핵심은 세 가지다 — 프리셋을 쓴 설정과 직접 적은 설정이 같은 결과를
만드는가, 직접 적은 값이 프리셋을 이기는가, 기존 설정이 그대로인가.

stdlib 와 yaml 만 의존하므로 GitHub CI(내부망 미접근)에서도 돈다.
"""

from pathlib import Path

import pytest

from processing.common import config_parse as cp
from processing.common import model_preset
from processing.enrichment.enrichment_config import EnrichmentConfig


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "processor_config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ── 병합 규칙 ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_preset_fills_only_missing_keys(tmp_path):
    """프리셋은 블록에 없는 키만 채우고, 블록이 직접 적은 값은 그대로 남는다."""
    cfg = cp.load_config(_write(tmp_path, """
model_presets:
  기본:
    url: "http://model/v1/chat/completions"
    api_key: "secret"
    model: "model"
    temperature: 0.0
enrichment:
  - toc:
      enable: true
      model_preset: 기본
      temperature: 0.2
"""))
    toc = cfg["enrichment"][0]["toc"]
    assert toc["url"] == "http://model/v1/chat/completions"
    assert toc["api_key"] == "secret"
    assert toc["temperature"] == 0.2      # 블록이 이긴다
    assert "model_preset" not in toc      # 참조 키는 소비되고 사라진다


@pytest.mark.unit
def test_preset_and_inline_produce_same_enrichment_config(tmp_path):
    """프리셋으로 쓴 설정과 직접 적은 설정이 같은 EnrichmentConfig 를 만든다."""
    body = """
enrichment:
  - toc:
      enable: true
      {toc_conn}
      max_tokens: 4096
  - metadata:
      enable: true
      {meta_conn}
"""
    conn = 'url: "http://m/v1"\n      api_key: "k"\n      model: "mm"'
    inline = cp.load_config(_write(
        tmp_path, body.format(toc_conn=conn, meta_conn=conn)
    ))
    preset = cp.load_config(_write(
        tmp_path,
        'model_presets:\n  공용:\n    url: "http://m/v1"\n    api_key: "k"\n    model: "mm"\n'
        + body.format(toc_conn="model_preset: 공용", meta_conn="model_preset: 공용"),
    ))

    ec_inline = EnrichmentConfig.from_raw(inline["enrichment"], tmp_path, parent_cfg=inline)
    ec_preset = EnrichmentConfig.from_raw(preset["enrichment"], tmp_path, parent_cfg=preset)

    assert ec_preset.toc == ec_inline.toc
    assert ec_preset.metadata == ec_inline.metadata


@pytest.mark.unit
def test_dict_values_merge_one_level(tmp_path):
    """params 같은 dict 키는 통째로 덮이지 않고 한 겹 병합된다(블록 우선)."""
    cfg = cp.load_config(_write(tmp_path, """
model_presets:
  이미지:
    url: "http://vlm/v1"
    params:
      temperature: 0.1
      top_p: 0.9
formats:
  ppt:
    page_description:
      model_preset: 이미지
      params:
        top_p: 0.5
        seed: 7
"""))
    params = cfg["formats"]["ppt"]["page_description"]["params"]
    assert params == {"temperature": 0.1, "top_p": 0.5, "seed": 7}


@pytest.mark.unit
def test_preset_works_outside_enrichment(tmp_path):
    """enrichment 밖(whisper 같은 최상위 섹션)에서도 같은 규칙으로 동작한다."""
    cfg = cp.load_config(_write(tmp_path, """
model_presets:
  음성:
    url: "http://whisper/v1/audio/transcriptions"
    model: "whisper"
whisper:
  model_preset: 음성
  language: "ko"
"""))
    assert cfg["whisper"]["url"] == "http://whisper/v1/audio/transcriptions"
    assert cfg["whisper"]["language"] == "ko"


# ── 배선 키 차단 ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_wiring_keys_are_not_injected(tmp_path):
    """프리셋의 enable 이 새어 나가 참조 블록을 함께 켜면 안 된다."""
    cfg = cp.load_config(_write(tmp_path, """
model_presets:
  나쁨:
    url: "http://m/v1"
    enable: true
    doc_type: card
    config_file: custom_field_card.yaml
enrichment:
  - toc:
      model_preset: 나쁨
"""))
    toc = cfg["enrichment"][0]["toc"]
    assert toc["url"] == "http://m/v1"
    assert "enable" not in toc
    assert "doc_type" not in toc
    assert "config_file" not in toc


# ── 오기입 ────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_undefined_preset_fails_startup(tmp_path):
    """없는 프리셋을 참조하면 기동을 막는다. 이름을 조용히 흘리면 연결 없이 호출이 나간다."""
    path = _write(tmp_path, """
model_presets:
  기본:
    url: "http://m/v1"
enrichment:
  - toc:
      model_preset: 기븐
""")
    with pytest.raises(model_preset.ModelPresetError) as excinfo:
        cp.load_config(path)
    assert "기븐" in str(excinfo.value)
    assert "기본" in str(excinfo.value)   # 정의된 이름을 알려준다


@pytest.mark.unit
def test_undefined_preset_warns_when_not_strict(tmp_path):
    """strict=False(첨부 프로세서)는 기동을 막지 않고 블록을 그대로 둔다."""
    cfg = cp.load_config(_write(tmp_path, """
enrichment:
  - toc:
      model_preset: 없음
      url: "http://m/v1"
"""), strict=False)
    toc = cfg["enrichment"][0]["toc"]
    assert toc["url"] == "http://m/v1"
    assert "model_preset" not in toc


@pytest.mark.unit
def test_malformed_presets_block_fails_startup(tmp_path):
    """model_presets 가 매핑이 아니면 기동을 막는다."""
    with pytest.raises(model_preset.ModelPresetError):
        cp.load_config(_write(tmp_path, "model_presets:\n  - url: http://m/v1\n"))


# ── 하위 호환 ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_config_without_presets_is_untouched(tmp_path):
    """프리셋을 안 쓰는 설정은 지금까지와 똑같은 dict 로 읽힌다."""
    text = """
enrichment:
  - toc:
      enable: true
      url: "http://m/v1"
      api_key: ""
      model: "model"
whisper:
  url: "http://w/v1"
"""
    path = _write(tmp_path, text)
    import yaml

    assert cp.load_config(path) == yaml.safe_load(text)


# ── 2단계: custom_fields 자식 yaml 에서의 프리셋 ────────────────────────────────
#
# 프로세서 설정과 달리 custom_field_*.yaml 은 config_parse.load_config 를 거치지 않고
# 별도로 읽힌다(CustomFieldsEnricher._load_config → config_v2.load). 여기서는 그 경로에서도
# `model_preset:` 참조가 같은 규칙으로 풀리는지, 그리고 extractor 가 읽지 않는 프리셋 키가
# 기동을 막지 않는지를 고정한다.

_PRESETS = {
    "기본": {
        "url": "http://model/v1/chat/completions",
        "api_key": "secret",
        "model": "my-model",
        "top_p": 0.9,  # llm extractor 가 읽지 않는 키 — 그대로 주입되면 기동이 실패한다.
    }
}


def _write_child_yaml(tmp_path: Path, name: str, endpoint_lines: str) -> str:
    """document/llm custom_fields 자식 yaml 을 써서 파일명을 돌려준다."""
    path = tmp_path / name
    path.write_text(f"""
schema: v2
source:
  kind: document
llm:
- out: [issuer_name]
  endpoint:
{endpoint_lines}
""", encoding="utf-8")
    return name


@pytest.mark.unit
def test_child_yaml_preset_fills_llm_connection(tmp_path):
    """자식 yaml 의 llm.endpoint 에 model_preset 만 적어도 프리셋의 연결정보로 채워진다."""
    pytest.importorskip("httpx")
    pytest.importorskip("docling_core")
    cf = pytest.importorskip("processing.enrichment.custom_fields_enricher")

    name = _write_child_yaml(tmp_path, "custom_field_preset.yaml", "    model_preset: 기본")
    enricher = cf.CustomFieldsEnricher(
        config_file=name, resource_path=str(tmp_path), model_presets=_PRESETS, extractor="llm",
    )
    assert enricher._url == "http://model/v1/chat/completions"
    assert enricher._model == "my-model"
    assert enricher._headers["Authorization"] == "Bearer secret"


@pytest.mark.unit
def test_child_yaml_preset_extra_key_is_filtered_not_startup_failure(tmp_path):
    """프리셋에 이 extractor 가 안 읽는 키(top_p)가 있어도 기동은 실패하지 않는다."""
    pytest.importorskip("httpx")
    pytest.importorskip("docling_core")
    cf = pytest.importorskip("processing.enrichment.custom_fields_enricher")

    name = _write_child_yaml(tmp_path, "custom_field_preset2.yaml", "    model_preset: 기본")
    # 생성만으로 검증이 끝난다 — validate_known_keys 가 여기서 예외를 던지면 top_p 가
    # 걸러지지 않고 새어 들어간 것이다.
    cf.CustomFieldsEnricher(
        config_file=name, resource_path=str(tmp_path), model_presets=_PRESETS, extractor="llm",
    )


@pytest.mark.unit
def test_child_yaml_direct_value_overrides_preset(tmp_path):
    """자식 yaml 이 직접 적은 url 이 프리셋을 이긴다."""
    pytest.importorskip("httpx")
    pytest.importorskip("docling_core")
    cf = pytest.importorskip("processing.enrichment.custom_fields_enricher")

    name = _write_child_yaml(
        tmp_path, "custom_field_preset3.yaml",
        "    model_preset: 기본\n    url: http://direct/v1",
    )
    enricher = cf.CustomFieldsEnricher(
        config_file=name, resource_path=str(tmp_path), model_presets=_PRESETS, extractor="llm",
    )
    assert enricher._url == "http://direct/v1"
    assert enricher._model == "my-model"   # 나머지 키는 여전히 프리셋에서 채워진다


@pytest.mark.unit
def test_child_yaml_undefined_preset_fails_startup(tmp_path):
    """자식 yaml 에서 없는 프리셋 이름을 참조하면 ModelPresetError 로 기동이 막힌다."""
    pytest.importorskip("httpx")
    pytest.importorskip("docling_core")
    cf = pytest.importorskip("processing.enrichment.custom_fields_enricher")

    name = _write_child_yaml(tmp_path, "custom_field_preset4.yaml", "    model_preset: 없음")
    with pytest.raises(model_preset.ModelPresetError):
        cf.CustomFieldsEnricher(
            config_file=name, resource_path=str(tmp_path), model_presets=_PRESETS,
            extractor="llm",
        )
