"""사이트 완성본(sites/<site>/resource/)의 표준 사본이 표준 resource/ 와 같은지 지킨다.

사이트 폴더는 그대로 실행·배포하는 전체 설정이라 표준 파일의 사본을 품는다. 사본은
build-script/sync-sites.sh 가 맞추며, 사이트가 소유하는 파일은 manifest.yaml 의 owned 에 적는다.
예전 resource_dev 는 사본이 조용히 어긋나도 아무도 몰랐다 — 이 검사가 그 틈을 막는다.
"""

from pathlib import Path

import pytest
import yaml

from shipped_config import PREPROCESSOR_DIR

STANDARD = PREPROCESSOR_DIR / "resource"
SITES = PREPROCESSOR_DIR.parents[1] / "sites"


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


@pytest.mark.unit
@pytest.mark.parametrize("site", sorted(p.parent.name for p in SITES.glob("*/manifest.yaml")))
def test_site_copies_match_standard(site):
    """owned 가 아닌 파일은 표준과 한 바이트도 다르지 않고, 표준에 없는 파일은 owned 여야 한다."""
    site_root = SITES / site / "resource"
    owned = set((yaml.safe_load((SITES / site / "manifest.yaml").read_text(encoding="utf-8")) or {})
                .get("owned") or [])
    standard, present = _files(STANDARD), _files(site_root)

    stale = sorted(n for n in standard - owned
                   if n not in present or (site_root / n).read_bytes() != (STANDARD / n).read_bytes())
    stray = sorted(present - standard - owned)
    missing_owned = sorted(owned - present)
    assert (stale, stray, missing_owned) == ([], [], []), (
        f"{site}: 표준과 다른 사본 {stale}, 소유 표시 없는 파일 {stray}, 없는 owned {missing_owned}. "
        f"표준을 고쳤다면 build-script/sync-sites.sh {site} 를 실행하고, 사이트 전용 파일이면 "
        f"sites/{site}/manifest.yaml 의 owned 에 적는다."
    )
