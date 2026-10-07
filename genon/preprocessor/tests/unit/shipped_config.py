"""출고 custom_fields 설정을 내부 형태로 읽는 테스트 공용 헬퍼.

출고 설정(`resource/`, `genon/sites/<site>/resource/`)은 v2 표기다. `yaml.safe_load` 로 raw 를 읽고
내부 형태의 최상위 키(`text_fields`, `field_labels`, `body_fields`, `chunk_prefix_fields`,
`first_chunk_fields` …)를 찾으면 전부 None 이 되어 **검사가 조용히 무력해진다** —
통과하지만 아무것도 보지 않는 상태가 된다. 실제로 그 상태로 4개 파일이 흘러갔다.

매퍼가 하는 것과 같은 번역(`config_v2.load`)을 거쳐 한 모양으로 맞춘다. 검사마다 설정 표기를
따로 읽으면 스키마가 하나 더 늘어나는 셈이 되므로, 번역은 이 한 곳에만 둔다.
"""

from pathlib import Path

import yaml

PREPROCESSOR_DIR = Path(__file__).resolve().parents[2]

# 모니모 사이트 완성본. 표준 resource/ 의 사본에 모니모 소유 파일을 더한, 그대로 실행하는 전체 폴더다
# (build-script/sync-sites.sh 가 표준 사본을 맞춘다). PREPROCESSOR_DIR 기준 상대경로로 두어
# `resource_dir` 파라미터 자리에 그대로 쓴다.
SITE_MONIMO = "../sites/monimo/resource"

# 전수 검사가 도는 출고 설정 폴더.
SHIPPED_ROOTS = ("resource", SITE_MONIMO)


def sibling_presets(path: Path) -> dict:
    """같은 폴더의 프로세서 설정이 쓰는 모델 프리셋을 기동과 같은 규칙으로 모은다.

    자식 yaml 의 `model_preset:` 참조는 기동 때 프로세서 설정의 프리셋으로 펼쳐진다
    (`EnrichmentConfig` 가 등록 블록에 실어 매퍼로 내린다). 여기서 프리셋을 주지 않으면
    참조가 그대로 남아 "url 이 없다" 로 갈리므로, 검사도 같은 것을 보게 맞춘다.
    """
    from genon.preprocessor.processing.common import config_parse as cp

    config = Path(path).parent / "parse_config.yaml"
    if not config.exists():
        return {}
    cfg = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    return cp.collect_model_presets(cfg, str(config))


def load_shipped(path: Path) -> dict:
    """설정 파일 하나를 v1 형태 dict 로 읽는다."""
    from genon.preprocessor.processing.enrichment import config_v2 as cv2

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return cv2.load(raw, label=Path(path).name, presets=sibling_presets(path))[0]


def load_shipped_named(name: str, resource_dir: str = "resource") -> dict:
    """`resource` 또는 사이트 완성본(SITE_MONIMO) 안의 설정을 이름으로 읽는다."""
    return load_shipped(PREPROCESSOR_DIR / resource_dir / name)
