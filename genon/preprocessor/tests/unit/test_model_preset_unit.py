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
@pytest.mark.parametrize("inline, env, expected", [
    # 파일만: 파일 값이 그대로 쓰인다
    ("", None, {"url": "http://file/v1", "model": "m", "timeout": 10}),
    # 메인 yaml 의 인라인이 파일 위에 키 단위로 얹힌다
    ("model_presets:\n  default: {timeout: 99}\n", None,
     {"url": "http://file/v1", "model": "m", "timeout": 99}),
    # 환경변수 파일이 가장 마지막 층이다 — url 만 바꿔도 나머지 키는 남는다
    ("model_presets:\n  default: {timeout: 99}\n", "default: {url: http://local/v1}",
     {"url": "http://local/v1", "model": "m", "timeout": 99}),
])
def test_preset_layers_file_inline_env(tmp_path, monkeypatch, inline, env, expected):
    """층 우선순위: model_presets_file → 메인 yaml 의 model_presets → GENOS_MODEL_PRESETS_FILE."""
    (tmp_path / "model_presets.yaml").write_text(
        "model_presets:\n  default: {url: http://file/v1, model: m, timeout: 10}\n", encoding="utf-8")
    monkeypatch.delenv(cp.MODEL_PRESETS_ENV, raising=False)
    if env is not None:
        local = tmp_path / "local" / "presets.yaml"
        local.parent.mkdir()
        local.write_text("model_presets:\n  " + env + "\n", encoding="utf-8")
        monkeypatch.setenv(cp.MODEL_PRESETS_ENV, str(local))
    cfg = cp.load_config(_write(tmp_path, "model_presets_file: model_presets.yaml\n" + inline
                                + "toc:\n  model_preset: default\n"))
    assert cfg["toc"] == expected


@pytest.mark.unit
@pytest.mark.parametrize("name, resolves", [("config.yaml", True), ("parse_config.yaml", False)],
                         ids=["액티비티 설정", "그 밖의 설정"])
def test_activity_config_falls_back_to_shared_presets(tmp_path, monkeypatch, name, resolves):
    """액티비티 폴더의 설정(파일명 config.yaml)은 model_presets_file 없이 설정 폴더(GENOS_RESOURCE_DIR)의
    model_presets.yaml 을 1층으로 받는다(#495 — 접속 정보를 폴더에 복사하지 않는다). 다른 이름의 설정은 종전대로다."""
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "model_presets.yaml").write_text(
        "model_presets:\n  default: {url: http://shared/v1}\n", encoding="utf-8")
    monkeypatch.setenv("GENOS_RESOURCE_DIR", str(tmp_path / "shared"))
    monkeypatch.delenv(cp.MODEL_PRESETS_ENV, raising=False)
    path = tmp_path / name
    path.write_text("toc:\n  model_preset: default\n", encoding="utf-8")
    if resolves:
        assert cp.load_config(str(path))["toc"] == {"url": "http://shared/v1"}
    else:
        with pytest.raises(model_preset.ModelPresetError, match="default"):
            cp.load_config(str(path))


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


# ── 자리표시값 경고(#631) ──────────────────────────────────────────────────────
# Studio 콘솔은 stderr 꼬리를 줄당 220자로 자른다. 로그 머리(시각·레벨·로거 이름)가 80자 남짓이므로
# 무엇이·영향·조치는 메시지 앞 130자 안에 있어야 한다.
_CONSOLE_MESSAGE_BUDGET = 130


def _placeholder_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "자리표시값" in r.getMessage()]


@pytest.mark.unit
@pytest.mark.parametrize("env", [False, True], ids=["환경변수 없음", "환경변수 있음"])
def test_placeholder_warning_names_presets_effect_and_fix_in_one_line(tmp_path, monkeypatch, caplog, env):
    """프리셋 자리표시값은 한 줄 앞부분에 프리셋 이름·영향·조치를 적고, 긴 목록은 개수와 앞 몇 개로 줄인다."""
    monkeypatch.delenv(cp.MODEL_PRESETS_ENV, raising=False)
    if env:
        presets = tmp_path / "dev_presets.yaml"
        presets.write_text("model_presets:\n  default: {url: <ENRICHMENT_SERVING_ID>}\n", encoding="utf-8")
        monkeypatch.setenv(cp.MODEL_PRESETS_ENV, str(presets))
    with caplog.at_level("WARNING"):
        cp.load_config(_write(tmp_path, """
model_presets:
  default: {url: "<ENRICHMENT_SERVING_ID>"}
  image: {url: "<IMAGE_DESCRIPTION_SERVING_ID>"}
  page: {url: "<PAGE_DESCRIPTION_SERVING_ID>"}
  ocr: {ocr_endpoint: "<OCR_ENDPOINT>"}
toc:
  model_preset: default
"""))
    [message] = _placeholder_warnings(caplog)
    assert "\n" not in message
    assert message.startswith("모델 프리셋 4개(default·image·page 외 1개)의 접속 정보가 자리표시값(<…>)이라")
    fix = (f"{cp.MODEL_PRESETS_ENV} 이 가리키는 파일에 실제 값을 적으세요" if env
           else f"{cp.MODEL_PRESETS_ENV} 에 실제 값을 적은 프리셋 파일을 지정하세요")
    assert "실패하거나 건너뜁니다" in message
    assert message.index(fix) + len(fix) <= _CONSOLE_MESSAGE_BUDGET
    assert "page.url=<PAGE_DESCRIPTION_SERVING_ID> 외 1개" in message and "<OCR_ENDPOINT>" not in message
    assert (f"파일 {presets}" in message) if env else (f"{cp.MODEL_PRESETS_ENV} 지정 안 됨" in message)


@pytest.mark.unit
def test_placeholder_outside_presets_points_at_the_config_file(tmp_path, monkeypatch, caplog):
    """프리셋 밖의 자리표시값은 고칠 자리가 그 설정 파일이므로 따로, 그 파일을 가리켜 알린다."""
    monkeypatch.delenv(cp.MODEL_PRESETS_ENV, raising=False)
    path = _write(tmp_path, 'whisper:\n  url: "<WHISPER_ENDPOINT>"\n')
    with caplog.at_level("WARNING"):
        cp.load_config(path)
    [message] = _placeholder_warnings(caplog)
    assert message == (f"설정 키 1개가 자리표시값(<…>)이라 이 값을 쓰는 기능은 실패하거나 건너뜁니다. "
                       f"{path} 에서 실제 값으로 바꾸세요: whisper.url=<WHISPER_ENDPOINT>")
