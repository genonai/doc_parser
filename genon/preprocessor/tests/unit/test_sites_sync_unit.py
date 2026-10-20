"""사이트 완성본(genon/sites/<site>/)의 표준 사본이 표준과 같은지 지킨다.
완성본은 두 자리다 — `resource/`(표준 resource/ 의 사본 + owned)와 `activities/<slug>/`(표준 액티비티 폴더의
설정 파일 사본 + owned, 코드는 없음). 사본은 build-script/sync-sites.sh 가 맞추며, 사이트가 소유하는 파일은
manifest.yaml 의 owned(사이트 루트 기준 경로)에 적는다.
예전 resource_dev 는 사본이 조용히 어긋나도 아무도 몰랐다 — 이 검사가 그 틈을 막는다.
"""
from pathlib import Path

import pytest
import yaml

from shipped_config import PREPROCESSOR_DIR

SITES = PREPROCESSOR_DIR.parent / "sites"
CODE = {"processor.py", "__init__.py", "__main__.py"}   # 액티비티 폴더의 코드 — 사이트 완성본에 두지 않는다


def _files(root: Path, skip: set[str] = frozenset()) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.name not in skip}


def _standard_for(rel: str) -> Path | None:
    """사이트 루트 기준 폴더(resource · activities/<slug>) → 표준 폴더. 표준에 없으면 None."""
    if rel == "resource":
        return PREPROCESSOR_DIR / "resource"
    slug = rel.split("/", 1)[1]
    standard = PREPROCESSOR_DIR / "activities" / slug
    return standard if standard.is_dir() else None


@pytest.mark.unit
@pytest.mark.parametrize("site", sorted(p.parent.name for p in SITES.glob("*/manifest.yaml")))
def test_site_copies_match_standard(site):
    """owned 가 아닌 파일은 표준과 한 바이트도 다르지 않고, 표준에 없는 파일은 owned 여야 한다.
    사이트 액티비티 폴더에는 코드(processor.py·__init__.py)가 없어야 하고, 표준에 없는 slug 폴더도 안 된다.
    저장소의 모든 slug 가 사이트에 있어야 한다 — 사이트 배포는 저장소 폴더로 떨어지지 않는다(#561)."""
    site_root = SITES / site
    owned = set((yaml.safe_load((site_root / "manifest.yaml").read_text(encoding="utf-8")) or {}).get("owned") or [])
    folders = ["resource", *sorted(f"activities/{p.name}" for p in (site_root / "activities").glob("*") if p.is_dir())]
    standard_slugs = {p.parent.name for p in (PREPROCESSOR_DIR / "activities").glob("*/processor.py")
                      if not p.parent.name.startswith("_")}
    problems = {f"activities/{slug}": "사이트에 없는 slug" for slug in sorted(standard_slugs)
                if not (site_root / "activities" / slug).is_dir()}
    for rel in folders:
        standard = _standard_for(rel)
        if standard is None:
            problems[rel] = "표준에 없는 액티비티 폴더"
            continue
        skip = CODE if rel != "resource" else frozenset()
        std, present = _files(standard, skip), _files(site_root / rel)
        own = {n[len(rel) + 1:] for n in owned if n.startswith(rel + "/")}
        stale = sorted(n for n in std - own
                       if n not in present or (site_root / rel / n).read_bytes() != (standard / n).read_bytes())
        stray = sorted(present - std - own)
        missing_owned = sorted(own - present)
        if stale or stray or missing_owned:                       # 사이트 폴더의 코드 파일은 표준 목록에 없어 stray 로 걸린다
            problems[rel] = {"표준과 다른 사본": stale, "소유 표시 없는 파일": stray, "없는 owned": missing_owned}
    assert problems == {}, (
        f"{site}: {problems}. 표준을 고쳤다면 build-script/sync-sites.sh {site} 를 실행하고, 사이트 전용 파일이면 "
        f"genon/sites/{site}/manifest.yaml 의 owned 에 적는다.")
    unknown = sorted(n for n in owned
                     if not (n.startswith("resource/") or n.startswith("activities/")) or Path(n).name in CODE)
    assert unknown == [], f"{site}: owned 경로는 resource/ 또는 activities/<slug>/ 아래여야 하고 코드 파일은 안 된다: {unknown}"


@pytest.mark.unit
@pytest.mark.parametrize("site", sorted(p.parent.name for p in SITES.glob("*/manifest.yaml")))
def test_site_owned_copies_agree(site):
    """사이트가 같은 이름의 파일을 resource/ 와 activities/<slug>/ 양쪽에 소유하면(기본 전처리기 서비스는 resource/
    완성본만 받고 그 간이 파서 설정이 custom_field 를 같은 폴더에서 부른다) 두 사본은 같아야 한다 — 한쪽만 고치면
    서비스에 따라 문서유형 설정이 갈린다. sync 로 복사하는 길은 없다 — 표준에 없는 파일이라 표준 대조에 걸린다."""
    site_root = SITES / site
    owned = (yaml.safe_load((site_root / "manifest.yaml").read_text(encoding="utf-8")) or {}).get("owned") or []
    by_name: dict[str, list[str]] = {}
    for n in owned:
        by_name.setdefault(Path(n).name, []).append(n)
    differing = {name: paths for name, paths in by_name.items() if len(paths) > 1
                 and len({(site_root / p).read_bytes() for p in paths if (site_root / p).exists()}) > 1}
    assert differing == {}, f"{site}: 같은 이름의 owned 사본이 서로 다르다: {differing}"
