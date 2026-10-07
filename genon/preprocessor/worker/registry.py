"""액티비티 등록부 — `activities/` 의 파일 이름 규칙으로 파싱·청킹 액티비티를 찾는다.

규칙(설계서 v0.25): **파일명 = slug = 액티비티 이름, 첫 단어 = 단계.** `activities/` 최상위에서
이름이 `parse`·`chunk` 이거나 `parse_`·`chunk_` 로 시작하는 파일만 등록한다. 목록을 만들 때는
import 하지 않는다(파사드 import 는 docling 을 끌어와 무겁다).

파일은 `DocumentProcessor` 를 상속만 한다. 액티비티 본문은 부모(`ParserCore`·`ChunkerCore`)의
`run_activity(arg)` 하나이고, 이름은 여기서 붙인다 — 바운드 메서드에는 이름표를 붙일 수 없고,
물려받은 같은 함수에 붙이면 두 번째 slug 가 충돌하므로 slug 마다 얇은 래퍼 함수를 만든다.
프로세서는 slug 마다 프로세스당 1개, 첫 호출에 만든다.

짝 설정은 `resource/<slug>_config.yaml` 이다. 없으면 프로세서 기본 해석에 맡긴다.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import CancelledError

from . import runtime, settings
from .errors import to_application_error

STAGES = ("parse", "chunk")


@dataclass(frozen=True)
class Entry:
    slug: str
    stage: str              # "parse" | "chunk"
    config_path: str | None


def stage_of(slug: str) -> str | None:
    """slug → 단계. 규칙에 맞지 않으면 None."""
    head = slug.split("_", 1)[0]
    return head if head in STAGES else None


def scan(activities_dir: Path | None = None, resource_dir: Path | None = None) -> list[Entry]:
    """activities 디렉터리 최상위(하위 폴더 제외) → Entry 목록(slug 순)."""
    activities_dir = activities_dir or settings.ACTIVITIES_DIR
    resource_dir = resource_dir or settings.resource_dir()
    entries = []
    for path in sorted(activities_dir.glob("*.py")):
        stage = stage_of(path.stem)
        if stage is None:
            continue
        cfg = resource_dir / f"{path.stem}_config.yaml"
        entries.append(Entry(path.stem, stage, str(cfg) if cfg.is_file() else None))
    return entries


_PROCESSORS: dict[str, object] = {}


def processor(entry: Entry):
    """slug 의 DocumentProcessor 인스턴스 — 프로세스당 1개(첫 호출에 생성)."""
    proc = _PROCESSORS.get(entry.slug)
    if proc is None:
        from genon.preprocessor.processing.core.chunker import ChunkerCore
        from genon.preprocessor.processing.core.parser import ParserCore

        cls = importlib.import_module(f"genon.preprocessor.activities.{entry.slug}").DocumentProcessor
        base = ParserCore if entry.stage == "parse" else ChunkerCore
        if not issubclass(cls, base):
            raise TypeError(f"activities/{entry.slug}.py 의 DocumentProcessor 는 {base.__name__} 를 상속해야 합니다")
        proc = cls(config_path=entry.config_path) if entry.config_path else cls()
        _PROCESSORS[entry.slug] = proc
    return proc


def build(entries: list[Entry]) -> list:
    """Entry 목록 → Temporal 에 등록할 액티비티 함수 목록(이름 = slug)."""
    def make(entry: Entry):
        @activity.defn(name=entry.slug)
        def run(arg: dict) -> dict:
            runtime.guard()
            try:
                proc = processor(entry)          # 첫 호출: import·설정 검증·모델 준비
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 성격 판정은 to_application_error 한 곳에서
                raise to_application_error(exc, f"{entry.slug} 프로세서 준비") from exc
            return proc.run_activity(arg)
        return run
    return [make(e) for e in entries]
