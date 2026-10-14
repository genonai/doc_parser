"""액티비티 등록부 — `activities/<slug>/` 폴더 규칙으로 파싱·청킹 액티비티를 찾는다(#495).

규칙: **폴더 이름 = slug = 액티비티 이름, 첫 단어 = 단계.** `activities/` 바로 아래에서 `processor.py` 가 있고
이름이 `parse`·`chunk` 이거나 `parse_`·`chunk_` 로 시작하는 폴더만 등록한다. 목록을 만들 때는 import 하지
않는다(파사드 import 는 docling 을 끌어와 무겁다). 폴더 규칙·사이트 덮어쓰기는 `activities/_folders.py`.

processor.py 는 `DocumentProcessor` 를 상속만 한다. 액티비티 본문은 부모(`ParserCore`·`ChunkerCore`)의
`run_activity(arg)` 하나이고, 이름은 여기서 붙인다 — 바운드 메서드에는 이름표를 붙일 수 없고,
물려받은 같은 함수에 붙이면 두 번째 slug 가 충돌하므로 slug 마다 얇은 래퍼 함수를 만든다.
프로세서는 slug 마다 프로세스당 1개, 첫 호출에 만든다(액티비티 스레드 여럿이 함께 쓴다).

설정은 폴더의 `config.yaml` 이 **필수**다 — 없으면 목록을 만들 때 오류다(예전엔 짝 설정이 없으면 조용히
패키지 기본 설정으로 떨어져 사이트 문서유형을 몰랐다). 사이트 완성본이 그 slug 폴더를 갖고 있으면
그 폴더의 config.yaml 을 쓴다(`_folders.config_dir`).
"""
from __future__ import annotations

import importlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import CancelledError

from genon.preprocessor.activities import _folders

from . import runtime
from .errors import to_application_error

STAGES = _folders.STAGES
stage_of = _folders.stage_of
log = logging.getLogger("genon.worker")


@dataclass(frozen=True)
class Entry:
    slug: str
    stage: str              # "parse" | "chunk"
    config_path: str        # 폴더(또는 사이트 완성본)의 config.yaml


def scan(activities_dir: Path | None = None) -> list[Entry]:
    """activities 디렉터리의 액티비티 폴더 → Entry 목록(slug 순). config.yaml 이 없는 폴더는 ValueError.

    slug 마다 실제로 쓰는 설정 경로와 출처(사이트 완성본 | 저장소 표준)를 로그에 남긴다(#520 — 기동 로그만으로 어느
    설정으로 도는지 알 수 있게). 사이트 배포(GENOS_RESOURCE_DIR)인데 그 옆에 activities/ 가 없으면 경고한다 — 액티비티
    설정이 전부 저장소 표준으로 떨어져 사이트 문서유형 등록이 빠진 채 돈다(모델 접속은 resource/ 의 model_presets.yaml
    로 그대로 적용된다). 실패로 막지는 않는다(표준 설정으로 도는 사이트도 있을 수 있다).
    """
    site = _folders.site_activities_dir()
    if site is not None and not site.is_dir():
        log.warning("사이트 설정(GENOS_RESOURCE_DIR=%s) 옆에 activities/ 가 없다 — 액티비티 설정은 전부 저장소 표준을 "
                    "쓴다(사이트 문서유형 등록 미반영 — 모델 접속은 resource/model_presets.yaml 로 적용된다). "
                    "사이트 완성본은 resource/ 와 activities/<slug>/ 두 자리다", _folders.resource_dir())
    entries = []
    for slug in _folders.slugs(activities_dir):
        path = _folders.config_path(slug, activities_dir)
        source = "사이트 완성본" if site is not None and path.parent.parent == site else "저장소 표준"
        log.info("액티비티 %s: 설정 %s (%s)", slug, path, source)
        entries.append(Entry(slug, stage_of(slug), str(path)))
    return entries


_PROCESSORS: dict[str, object] = {}
#: 첫 생성은 slug 마다 한 번 — 첫 호출이 여러 스레드에서 동시에 오면 모델을 두 번 올린다(WORKER_THREADS>1).
_CREATE_LOCK = threading.Lock()


def processor(entry: Entry):
    """slug 의 DocumentProcessor 인스턴스 — 프로세스당 1개(첫 호출에 생성)."""
    proc = _PROCESSORS.get(entry.slug)
    if proc is not None:
        return proc
    with _CREATE_LOCK:
        proc = _PROCESSORS.get(entry.slug)
        if proc is not None:
            return proc
        from genon.preprocessor.processing.core.chunker import ChunkerCore
        from genon.preprocessor.processing.core.parser import ParserCore

        cls = importlib.import_module(f"genon.preprocessor.activities.{entry.slug}.processor").DocumentProcessor
        base = ParserCore if entry.stage == "parse" else ChunkerCore
        if not issubclass(cls, base):
            raise TypeError(f"activities/{entry.slug}/processor.py 의 DocumentProcessor 는 {base.__name__} 를 "
                            "상속해야 합니다")
        proc = cls(config_path=entry.config_path)
        _PROCESSORS[entry.slug] = proc
    return proc


def build(entries: list[Entry]) -> list:
    """Entry 목록 → Temporal 에 등록할 액티비티 함수 목록(이름 = slug)."""
    def make(entry: Entry):
        @activity.defn(name=entry.slug)
        def run(arg: dict) -> dict:
            with runtime.running():              # 재기동 중이면 여기서 거절한다
                try:
                    proc = processor(entry)      # 첫 호출: import·설정 검증·모델 준비
                except CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — 성격 판정은 to_application_error 한 곳에서
                    raise to_application_error(exc, f"{entry.slug} 프로세서 준비") from exc
                return proc.run_activity(arg)
        return run
    return [make(e) for e in entries]
