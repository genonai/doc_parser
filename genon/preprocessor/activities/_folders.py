"""액티비티 폴더 규칙 — 어디에 무엇이 있고, 사이트가 무엇을 덮어쓰는가(#495·#497).

    genon/preprocessor/activities/<slug>/     원형·사본 모두 같은 모양. 폴더 이름 = slug = 액티비티 이름,
        processor.py                           첫 단어 = 단계(parse·chunk)
        config.yaml                            필수. 없으면 등록 오류(기본 설정으로 조용히 떨어지지 않는다)
        prompt_*.md · custom_field_*.yaml      설정이 파일명으로 부르는 것 = 폴더 안 사본(폴더 밖 참조 거부)
    모델·외부 서비스 접속은 설정 폴더의 model_presets.yaml 을 **이름으로 참조**한다(복사하지 않는다).

사이트 완성본(genon/sites/<site>/)은 `resource/`(설정 폴더)와 `activities/<slug>/`(액티비티 설정) 두 자리를
갖는다. 배포에서는 `GENOS_RESOURCE_DIR` 이 사이트의 `resource/` 를 가리키고, 그 **옆의** `activities/<slug>/`
가 그 slug 의 설정 폴더다(코드는 저장소의 processor.py 그대로). 사이트 배포는 **사이트 완성본만** 읽는다 —
그 slug 폴더가 사이트에 없으면 오류이고 저장소 폴더로 떨어지지 않는다(#561). 저장소와 사이트 설정이 섞이면
어느 설정이 잘못됐는지 찾을 수 없기 때문이다. 사이트 완성본은 모든 slug 폴더를 갖는다
(build-script/sync-sites.sh 가 표준 사본을 맞추고 빠진 slug 폴더를 만든다).

`_` 로 시작하는 파일·폴더와 `__pycache__` 는 액티비티가 아니다.
"""
from __future__ import annotations

import os
from pathlib import Path

ACTIVITIES_DIR = Path(__file__).resolve().parent
PREPROCESSOR_DIR = ACTIVITIES_DIR.parent
STANDARD_RESOURCE_DIR = PREPROCESSOR_DIR / "resource"
PROCESSOR_FILE = "processor.py"
CONFIG_FILE = "config.yaml"
STAGES = ("parse", "chunk")


def resource_dir() -> Path:
    """설정 폴더 — 사이트 배포면 GENOS_RESOURCE_DIR, 아니면 표준 resource/."""
    return Path(os.environ.get("GENOS_RESOURCE_DIR") or STANDARD_RESOURCE_DIR)


def site_activities_dir() -> Path | None:
    """사이트 완성본의 액티비티 자리(GENOS_RESOURCE_DIR 옆의 activities/). 사이트 배포가 아니면 None."""
    if not os.environ.get("GENOS_RESOURCE_DIR"):
        return None
    return resource_dir().resolve().parent / "activities"


def stage_of(slug: str) -> str | None:
    """slug → 단계. 규칙에 맞지 않으면 None."""
    head = slug.split("_", 1)[0]
    return head if head in STAGES else None


def slugs(activities_dir: Path | None = None) -> list[str]:
    """저장소의 액티비티 폴더 이름(slug 순) — processor.py 가 있는 폴더만."""
    root = activities_dir or ACTIVITIES_DIR
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith("_") and (p / PROCESSOR_FILE).is_file()
                  and stage_of(p.name) is not None)


def config_dir(slug: str, activities_dir: Path | None = None) -> Path:
    """slug 의 설정 폴더 — 사이트 배포면 사이트 완성본의 그 slug 폴더, 아니면 저장소의 액티비티 폴더.
    사이트 배포에서 폴더가 없어도 저장소로 떨어지지 않는다(config_path 가 오류로 알린다)."""
    site = site_activities_dir()
    if site is not None:
        return site / slug
    return (activities_dir or ACTIVITIES_DIR) / slug


def config_path(slug: str, activities_dir: Path | None = None) -> Path:
    """slug 의 config.yaml 경로. 없으면 ValueError — 어느 폴더를 봤는지 적는다."""
    folder = config_dir(slug, activities_dir)
    site = site_activities_dir()
    if site is not None and not folder.is_dir():
        raise ValueError(f"사이트 완성본({site})에 액티비티 {slug!r} 폴더가 없습니다: {folder} — 사이트 배포(GENOS_RESOURCE_DIR)는 "
                         "사이트 완성본의 설정만 읽고 저장소 폴더로 떨어지지 않습니다. 사이트에 그 slug 를 두십시오"
                         "(사이트 원형 복제: activities new, 표준 사본: build-script/sync-sites.sh)")
    path = folder / CONFIG_FILE
    if not path.is_file():
        raise ValueError(f"액티비티 {slug!r} 의 설정이 없습니다: {path} — 액티비티 폴더에는 config.yaml 이 있어야 합니다")
    return path
