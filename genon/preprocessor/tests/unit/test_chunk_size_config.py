"""
chunking.chunk_size 의 yaml/kwargs 지정 및 우선순위(kwargs > yaml > 0) 단위 테스트.

의존성(docling 등) 미가용 환경에서는 importorskip 으로 자동 skip 된다(CI gate).
intelligent_processor / convert_processor 두 곳의 DocumentProcessor 가 동일하게 동작하는지 확인한다.
"""

import shutil
from pathlib import Path

import pytest
import yaml

_MODULES = ["intelligent_processor", "convert_processor", "chunking_processor"]

# facade 모듈명 → 출고 config 파일명
_DEFAULT_CONFIG = {
    "intelligent_processor": "intelligent_processor_config.yaml",
    "convert_processor": "convert_processor_config.yaml",
    "chunking_processor": "chunking_processor_config.yaml",
}


def _load_processor(module_name: str):
    mod = pytest.importorskip(f"facade.{module_name}")
    return mod.DocumentProcessor


_UNSET = object()


def _make_config(tmp_path: Path, module_name: str, chunk_size, chunk_mode=_UNSET,
                 include_chunk_header=_UNSET) -> str:
    """출고 config 를 복사하고 chunking.chunk_size(및 옵션 chunk_mode/include_chunk_header)만 덮어쓴다.

    config 는 자신과 같은 디렉터리에서 prompt_*.md 등 형제 파일을 참조하므로 resource/ 를 통째로
    tmp_path 에 복사한다(yaml 하나만 복사하면 intelligent/convert 가 prompt 파일 부재로 init 실패 →
    테스트가 조용히 skip 된다).
    """
    resource_dir = Path(__file__).resolve().parents[2] / "resource"
    repo_resource = resource_dir / _DEFAULT_CONFIG[module_name]
    shutil.copytree(resource_dir, tmp_path, dirs_exist_ok=True)
    cfg = yaml.safe_load(repo_resource.read_text(encoding="utf-8"))
    cfg.setdefault("chunking", {})
    if chunk_size is None:
        cfg["chunking"].pop("chunk_size", None)
    else:
        cfg["chunking"]["chunk_size"] = chunk_size
    if chunk_mode is not _UNSET:
        if chunk_mode is None:
            cfg["chunking"].pop("chunk_mode", None)
        else:
            cfg["chunking"]["chunk_mode"] = chunk_mode
    if include_chunk_header is not _UNSET:
        if include_chunk_header is None:
            cfg["chunking"].pop("include_chunk_header", None)
        else:
            cfg["chunking"]["include_chunk_header"] = include_chunk_header
    out = tmp_path / _DEFAULT_CONFIG[module_name]
    out.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return str(out)


def _init_processor(module_name: str, config_path: str):
    DocumentProcessor = _load_processor(module_name)
    try:
        return DocumentProcessor(config_path=config_path)
    except Exception as e:  # noqa: BLE001 - 모델/네트워크 등 환경 의존
        pytest.skip(f"DocumentProcessor init unavailable: {e}")


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _MODULES)
def test_yaml_chunk_size_is_loaded(tmp_path, module_name):
    """yaml chunking.chunk_size 값이 self._chunk_size 로 로드된다."""
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 1234))
    assert proc._chunk_size == 1234


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _MODULES)
def test_yaml_chunk_size_zero(tmp_path, module_name):
    """출고 기본값 0 도 정상 로드된다(None 이 아님)."""
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 0))
    assert proc._chunk_size == 0


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _MODULES)
def test_yaml_chunk_size_absent_is_none(tmp_path, module_name):
    """chunk_size 미설정 시 self._chunk_size 는 None (split 단계에서 0 으로 처리)."""
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, None))
    assert proc._chunk_size is None


def _spy_split(mod, proc, **split_kwargs):
    """GenosSmartChunker 생성 인자(max_tokens/chunk_mode)를 가로채 반환한다(실제 청킹 생략)."""
    captured = {}
    OrigChunker = mod.GenosSmartChunker

    class _SpyChunker(OrigChunker):
        def __init__(self, **kw):
            captured["max_tokens"] = kw.get("max_tokens")
            captured["chunk_mode"] = kw.get("chunk_mode")
            captured["include_chunk_header"] = kw.get("include_chunk_header")
            super().__init__(**kw)

        def chunk(self, *a, **k):  # 실제 청킹은 생략
            return iter(())

    mod.GenosSmartChunker = _SpyChunker
    try:
        list(proc.split_documents(documents=None, **split_kwargs))
    finally:
        mod.GenosSmartChunker = OrigChunker
    return captured


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _MODULES)
def test_priority_kwargs_over_yaml(tmp_path, module_name):
    """split_documents 의 max_tokens 우선순위: kwargs.chunk_size > yaml > 0.

    clamp(<1024→1024) 간섭을 피하기 위해 최소값 이상 값으로 검증한다.
    """
    mod = pytest.importorskip(f"facade.{module_name}")
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 5000))

    # kwargs 에 chunk_size 전달 → kwargs 우선
    assert _spy_split(mod, proc, chunk_size=7700)["max_tokens"] == 7700
    # kwargs 미전달 → yaml(5000)
    assert _spy_split(mod, proc)["max_tokens"] == 5000


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _MODULES)
def test_chunk_size_min_clamp(tmp_path, module_name):
    """chunk_size 가 0 초과이면서 1024 미만이면 1024 로 보정된다. 0 은 그대로."""
    mod = pytest.importorskip(f"facade.{module_name}")
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 2000))

    # yaml 500(미만) → 1024
    proc._chunk_size = 500
    assert _spy_split(mod, proc)["max_tokens"] == 1024
    # kwargs 500 → 1024
    assert _spy_split(mod, proc, chunk_size=500)["max_tokens"] == 1024
    # kwargs 1024 → 1024 (경계)
    assert _spy_split(mod, proc, chunk_size=1024)["max_tokens"] == 1024
    # kwargs 0 → 0 (분할 안 함, 보정 없음)
    assert _spy_split(mod, proc, chunk_size=0)["max_tokens"] == 0
    # kwargs 2000 → 2000 (보정 없음)
    assert _spy_split(mod, proc, chunk_size=2000)["max_tokens"] == 2000


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _MODULES)
def test_chunk_mode_default_and_override(tmp_path, module_name):
    """chunk_mode: yaml 기본 split_only, 잘못된 값 fallback, kwargs 오버라이드."""
    mod = pytest.importorskip(f"facade.{module_name}")

    # yaml 미설정 → 기본 split_only
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 5000, chunk_mode=None))
    assert proc._chunk_mode == "split_only"
    assert _spy_split(mod, proc)["chunk_mode"] == "split_only"

    # yaml resize_all → 로드/전달
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 5000, chunk_mode="resize_all"))
    assert proc._chunk_mode == "resize_all"
    assert _spy_split(mod, proc)["chunk_mode"] == "resize_all"
    # kwargs 오버라이드
    assert _spy_split(mod, proc, chunk_mode="split_only")["chunk_mode"] == "split_only"

    # 잘못된 yaml 값 → split_only fallback
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 5000, chunk_mode="bogus"))
    assert proc._chunk_mode == "split_only"


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _MODULES)
def test_include_chunk_header_default_and_override(tmp_path, module_name):
    """include_chunk_header: yaml 기본 True, yaml false 로드, kwargs 오버라이드, 잘못된 값 fallback.

    청커에도 같은 값이 전달되어야 크기 산정(_size)이 compose_vectors 의 실제 부착 여부와 일치한다.
    """
    mod = pytest.importorskip(f"facade.{module_name}")

    # yaml 미설정 → 기본 True
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 5000, include_chunk_header=None))
    assert proc._include_chunk_header is True
    assert _spy_split(mod, proc)["include_chunk_header"] is True

    # yaml false → 로드/전달
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 5000, include_chunk_header=False))
    assert proc._include_chunk_header is False
    assert _spy_split(mod, proc)["include_chunk_header"] is False
    # kwargs 오버라이드 (0/1 숫자와 "on"/"off" 문자열 모두 허용)
    assert _spy_split(mod, proc, include_chunk_header=1)["include_chunk_header"] is True
    assert _spy_split(mod, proc, include_chunk_header="on")["include_chunk_header"] is True

    # yaml true + kwargs off → False
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 5000, include_chunk_header=True))
    assert proc._include_chunk_header is True
    assert _spy_split(mod, proc, include_chunk_header=0)["include_chunk_header"] is False
    assert _spy_split(mod, proc, include_chunk_header="off")["include_chunk_header"] is False

    # 잘못된 yaml 값 → True fallback
    proc = _init_processor(module_name, _make_config(tmp_path, module_name, 5000, include_chunk_header="bogus"))
    assert proc._include_chunk_header is True
    # 잘못된 kwargs 값 → yaml 값(True) 유지
    assert _spy_split(mod, proc, include_chunk_header="bogus")["include_chunk_header"] is True
