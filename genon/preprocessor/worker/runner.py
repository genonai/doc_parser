"""자식 프로세스 본체 — Temporal 워커 1개, 동시 1건.

`__main__.py` 와 분리한 이유: multiprocessing(spawn)은 자식에서 대상 함수를 모듈 이름으로 다시
import 하는데, 패키지의 `__main__` 모듈은 다시 import 하지 않는다. 그래서 대상 함수는 여기 둔다.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging

from . import registry, runtime, settings

log = logging.getLogger("genon.worker")


def child(index: int) -> None:
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [worker-{index}] %(levelname)s %(message)s")
    asyncio.run(_serve(settings.load()))


async def _serve(cfg: settings.Settings) -> None:
    from temporalio.client import Client
    from temporalio.worker import Worker

    runtime.configure(cfg)
    entries = registry.scan()
    if not entries:
        raise RuntimeError(f"등록할 액티비티가 없습니다 — {settings.ACTIVITIES_DIR} 에 parse·chunk·parse_…·chunk_… 파일이 없다")
    log.info("Temporal 접속: address=%s namespace=%s task_queue=%s",
             cfg.temporal_address, cfg.temporal_namespace, cfg.task_queue)
    client = await Client.connect(cfg.temporal_address, namespace=cfg.temporal_namespace)
    log.info("액티비티 %d개: %s", len(entries), ", ".join(f"{e.slug}({e.stage})" for e in entries))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        await Worker(client, task_queue=cfg.task_queue, activities=registry.build(entries),
                     activity_executor=pool, max_concurrent_activities=1).run()
