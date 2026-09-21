"""attachment_processor 의 YAML 설정 로딩 단위 테스트.

attachment_processor 는 enrichment 를 사용하지 않고 chunking/loaders/whisper 설정만
yaml 로 읽는다. 여기서는 잘못된/없는 설정에서도 startup 이 깨지지 않고 기본값으로
fallback 하는지(_load_config) 와 기본 설정 경로 해석을 검증한다.

내부 서버 요청 없음. attachment_processor import 가 불가한 환경(docling 미설치 등)에서는
모듈 단위로 skip 된다.
"""

import pytest

# 무거운 의존성(docling 등) 미설치 환경에서는 파일 전체 skip (GitHub CI 에서는 정상 import)
attachment = pytest.importorskip("facade.attachment_processor")

_load_config = attachment._load_config
_resolve_default_attachment_config_path = attachment._resolve_default_attachment_config_path


@pytest.mark.unit
class TestLoadConfig:
    def test_valid_yaml_returns_dict(self, tmp_path):
        cfg_file = tmp_path / "cfg.yaml"
        cfg_file.write_text("defaults:\n  chunker_type: recursive\n", encoding="utf-8")
        assert _load_config(str(cfg_file)) == {"defaults": {"chunker_type": "recursive"}}

    @pytest.mark.parametrize("content", [
        None,                          # 파일 자체가 없음
        "defaults: [unclosed\n  : :\n",  # 깨진 YAML (탭/구문 오류) → 예외 대신 기본값
        "- a\n- b\n",                  # mapping 이 아닌 list
        "",                            # 빈 파일
    ], ids=["missing-file", "invalid-yaml", "non-mapping", "empty-file"])
    def test_unusable_config_returns_empty_dict(self, tmp_path, content):
        cfg_file = tmp_path / "cfg.yaml"
        if content is not None:
            cfg_file.write_text(content, encoding="utf-8")
        assert _load_config(str(cfg_file)) == {}


@pytest.mark.unit
class TestResolveDefaultConfigPath:
    def test_returns_existing_path(self):
        from pathlib import Path
        path = _resolve_default_attachment_config_path()
        assert isinstance(path, str)
        assert Path(path).exists(), f"기본 설정 경로가 존재해야 함: {path}"
        assert path.endswith("attachment_processor_config.yaml")

    def test_prefers_resource_dev(self):
        # resource_dev 파일이 있으면 그쪽을 우선 사용한다.
        from pathlib import Path
        path = Path(_resolve_default_attachment_config_path())
        dev = path.parent.parent / "resource_dev" / "attachment_processor_config.yaml"
        if dev.exists():
            assert "resource_dev" in str(path)
