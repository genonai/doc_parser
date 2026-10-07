"""워커 env. 값은 기동 때 한 번 읽는다(`load()`).

    TEMPORAL_HOST          필수. Temporal frontend 주소
    TEMPORAL_PORT          기본 7233
    TEMPORAL_NAMESPACE     기본 ns-ifa-app (파이프라인과 같은 기본값)
    WORKER_TASK_QUEUE      필수. 이 워커가 폴링할 큐 — 파이프라인의 DOC_PARSER_TASK_QUEUE 와 같아야 한다
    WORKER_PROCESSES       기본 1. 동시에 처리할 문서 수 = 워커 프로세스 수
    NFS_ROOT               필수. read_ref root="nfs"(원본·업로드 하드링크)를 여는 루트
    ARTIFACT_ROOT          필수. 산출물 ref 와 read_ref root="artifact"(JSON 복구본)를 여는 루트
                           (파이프라인 INTERIM_ROOT 와 같은 저장소를 이 파드에 마운트한 경로)
    LLM_CACHE_ROOT         선택. params.llm_cache 가 켜진 요청의 LLM 캐시 루트(파이프라인 DOC_PARSER_CACHE_ROOT
                           가 가리키던 NFS 자리). 없으면 캐시를 쓰지 않는다
    PREVIEW_CHARS          기본 2000. 반환 preview 길이(페이로드 상한 안)
    HEARTBEAT_INTERVAL     기본 10초
    PORT                   기본 8080 — /health

`INTERIM_ROOT` 는 쓰지 않는다. 전처리기에서 그 이름은 LLM 캐시 루트다(docling/utils/llm_cache.py).
`GENOS_RESOURCE_DIR` 은 루트 main.py 와 같은 뜻 — 있으면 그 폴더의 설정을 읽는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PREPROCESSOR_DIR = Path(__file__).resolve().parents[1]      # genon/preprocessor
ACTIVITIES_DIR = PREPROCESSOR_DIR / "activities"


def resource_dir() -> Path:
    return Path(os.environ.get("GENOS_RESOURCE_DIR") or PREPROCESSOR_DIR / "resource")


@dataclass(frozen=True)
class Settings:
    temporal_host: str
    temporal_port: int
    temporal_namespace: str
    task_queue: str
    processes: int
    nfs_root: str
    artifact_root: str
    heartbeat_interval: float
    port: int
    llm_cache_root: str | None = None
    preview_chars: int = 2000

    @property
    def temporal_address(self) -> str:
        return f"{self.temporal_host}:{self.temporal_port}"


_REQUIRED = ("TEMPORAL_HOST", "WORKER_TASK_QUEUE", "NFS_ROOT", "ARTIFACT_ROOT")


def load(env: dict | None = None) -> Settings:
    """env → Settings. 필수 값이 없으면 이름을 모두 적어 ValueError."""
    env = os.environ if env is None else env
    missing = [k for k in _REQUIRED if not (env.get(k) or "").strip()]
    if missing:
        raise ValueError(f"워커 필수 env 가 없습니다: {', '.join(missing)}")
    processes = int(env.get("WORKER_PROCESSES") or 1)
    if processes < 1:
        raise ValueError(f"WORKER_PROCESSES 는 1 이상이어야 합니다: {processes}")
    return Settings(
        temporal_host=env["TEMPORAL_HOST"].strip(),
        temporal_port=int(env.get("TEMPORAL_PORT") or 7233),
        temporal_namespace=(env.get("TEMPORAL_NAMESPACE") or "ns-ifa-app").strip(),
        task_queue=env["WORKER_TASK_QUEUE"].strip(),
        processes=processes,
        nfs_root=env["NFS_ROOT"].strip(),
        artifact_root=env["ARTIFACT_ROOT"].strip(),
        heartbeat_interval=float(env.get("HEARTBEAT_INTERVAL") or 10),
        port=int(env.get("PORT") or 8080),
        llm_cache_root=(env.get("LLM_CACHE_ROOT") or "").strip() or None,
        preview_chars=int(env.get("PREVIEW_CHARS") or 2000),
    )
