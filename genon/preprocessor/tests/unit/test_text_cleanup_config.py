"""chunking.text_cleanup 의 yaml/kwargs 지정 및 우선순위(kwargs > yaml > off) 단위 테스트.

의존성(docling 등) 미가용 환경에서는 importorskip 으로 자동 skip 된다(CI gate).
활성 processor 3종이 동일하게 동작하는지 확인한다(청킹 로직은 lockstep 복제 대상).
"""

import shutil
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_MODULES = ["intelligent_processor", "convert_processor", "chunking_processor"]

_DEFAULT_CONFIG = {
    "intelligent_processor": "intelligent_processor_config.yaml",
    "convert_processor": "convert_processor_config.yaml",
    "chunking_processor": "chunking_processor_config.yaml",
}

_UNSET = object()


def _load_processor(module_name: str):
    mod = pytest.importorskip(f"facade.{module_name}")
    return mod.DocumentProcessor


def _make_config(tmp_path: Path, module_name: str, text_cleanup=_UNSET) -> str:
    """출고 config 를 복사하고 chunking.text_cleanup 만 덮어쓴다.

    config 는 같은 디렉터리의 prompt_*.md 등 형제 파일을 참조하므로 resource/ 를 통째로
    복사한다(yaml 하나만 복사하면 init 실패로 테스트가 조용히 skip 된다).
    """
    resource_dir = Path(__file__).resolve().parents[2] / "resource"
    shutil.copytree(resource_dir, tmp_path, dirs_exist_ok=True)
    cfg = yaml.safe_load((resource_dir / _DEFAULT_CONFIG[module_name]).read_text(encoding="utf-8"))
    cfg.setdefault("chunking", {})
    if text_cleanup is _UNSET:
        pass
    elif text_cleanup is None:
        cfg["chunking"].pop("text_cleanup", None)
    else:
        cfg["chunking"]["text_cleanup"] = text_cleanup
    out = tmp_path / _DEFAULT_CONFIG[module_name]
    out.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return str(out)


def _init_processor(module_name: str, config_path: str):
    DocumentProcessor = _load_processor(module_name)
    try:
        return DocumentProcessor(config_path=config_path)
    except Exception as e:  # noqa: BLE001 - 모델/네트워크 등 환경 의존
        pytest.skip(f"DocumentProcessor init unavailable: {e}")


# 출고 기본값. 청커만 켜져 있다(#363) — 파서+청커 배포 단위의 청크 본문을 RAG 검색용으로
# 정제하기로 결정했다. intelligent/convert 는 각자 다른 사이트가 쓰고 재색인 시점도 다르므로
# 건드리지 않았다. 여기서 값을 고정해 두는 이유는 기본값이 조용히 뒤집히면 재색인 없이
# 산출이 바뀌기 때문이다.
_SHIPPED_MODE = {
    "intelligent_processor": "off",
    "convert_processor": "off",
    "chunking_processor": "safe",
}


@pytest.mark.parametrize("module_name", _MODULES)
def test_shipped_default_mode(tmp_path, module_name):
    proc = _init_processor(module_name, _make_config(tmp_path, module_name))
    assert proc._text_cleanup == _SHIPPED_MODE[module_name]


def test_shipped_chunker_rules_strip_decorative_glyphs(tmp_path):
    """청커 출고 설정의 규칙이 실제로 장식 마커를 지운다."""
    from genon.preprocessor.facade.chunking import text_norm as tn

    proc = _init_processor("chunking_processor", _make_config(tmp_path, "chunking_processor"))
    rules = tn.rules_of(proc)
    assert rules, "출고 설정에 text_cleanup 규칙이 있어야 한다"
    assert tn.apply_rules("■ 제목\n◈ 항목\n※ 주의", rules) == "제목\n항목\n주의"


@pytest.mark.parametrize("module_name", _MODULES)
def test_absent_key_is_off(tmp_path, module_name):
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, None))
    assert proc._text_cleanup == "off"


@pytest.mark.parametrize("module_name", _MODULES)
def test_yaml_safe_is_loaded(tmp_path, module_name):
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, "safe"))
    assert proc._text_cleanup == "safe"


@pytest.mark.parametrize("module_name", _MODULES)
def test_yaml_typo_falls_back_to_off(tmp_path, module_name):
    """설정 오타로 파이프라인이 죽지 않는다."""
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, "saef"))
    assert proc._text_cleanup == "off"


@pytest.mark.parametrize("module_name", _MODULES)
def test_kwargs_override_yaml(tmp_path, module_name):
    """요청 kwargs 의 text_cleanup 이 yaml 보다 우선한다(공용 tn.mode_for)."""
    from genon.preprocessor.facade.chunking import text_norm as tn

    proc = _init_processor(module_name, _make_config(tmp_path, module_name, "off"))
    assert tn.mode_for({"text_cleanup": "safe"}, proc._text_cleanup) == "safe"
    assert tn.mode_for({}, proc._text_cleanup) == "off"
    assert tn.mode_for({"text_cleanup": None}, "safe") == "safe"
    assert tn.enabled_for({"text_cleanup": "safe"}, "off") is True
