"""자식 프로세스 본체 — Temporal 워커 1개, 동시 WORKER_THREADS 건.

`__main__.py` 와 분리한 이유: multiprocessing(spawn)은 자식에서 대상 함수를 모듈 이름으로 다시
import 하는데, 패키지의 `__main__` 모듈은 다시 import 하지 않는다. 그래서 대상 함수는 여기 둔다.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import logging

from . import registry, runtime, settings
from .groups import Group

log = logging.getLogger("genon.worker")


def group_settings(group: Group, base: settings.Settings) -> settings.Settings:
    """공통 값(TEMPORAL_*·NFS_ROOT·ARTIFACT_ROOT 등)은 env 그대로, 큐·프로세스·스레드만 묶음 값으로(#577)."""
    return dataclasses.replace(base, task_queue=group.task_queue, processes=group.processes, threads=group.threads)


def child(group: Group, index: int) -> None:
    logging.basicConfig(level=logging.INFO,
                        format=f"%(asctime)s [worker-{group.name}-{index}] %(levelname)s %(message)s")
    from genon.preprocessor.processing.common import profiling
    profiling.install()          # GENOS_PROFILE=1 일 때만 — 단계·외부 호출 계측(#514)
    asyncio.run(_serve(group_settings(group, settings.load())))


async def _serve(cfg: settings.Settings) -> None:
    from temporalio.client import Client
    from temporalio.worker import Worker

    runtime.configure(cfg)
    entries = registry.scan()
    if not entries:
        raise RuntimeError(f"등록할 액티비티가 없습니다 — {settings.ACTIVITIES_DIR} 에 parse·chunk·parse_…·chunk_… 폴더(processor.py)가 없다")
    log.info("Temporal 접속: address=%s namespace=%s task_queue=%s threads=%d",
             cfg.temporal_address, cfg.temporal_namespace, cfg.task_queue, cfg.threads)
    client = await Client.connect(cfg.temporal_address, namespace=cfg.temporal_namespace)
    log.info("액티비티 %d개: %s", len(entries), ", ".join(f"{e.slug}({e.stage})" for e in entries))
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.threads) as pool:
        await Worker(client, task_queue=cfg.task_queue, activities=registry.build(entries),
                     activity_executor=pool, max_concurrent_activities=cfg.threads).run()
