"""워커 액티비티 경로 동시 실행 — 한 프로세스에서 여러 건을 돌려도 결과가 한 건씩과 같은가(#502).

실제 등록부(`registry.scan()` → `build`)와 실제 파사드(activities/parse.py·chunk.py)로 Temporal 에 워커를
띄운다. 운영 워커는 프로세스당 동시 1건이지만, 이 시험은 액티비티 스레드를 여러 개
둔다(`max_concurrent_activities`). 한 건씩 돌린 결과를 기준으로, 같은 것을 한꺼번에 돌린 결과가 같아야 한다.

  샘플 parse→chunk   모델·LLM 없이 처리되는 형식들, 문서마다 chunk_size 를 다르게
  쪽 문서 chunk      쪽별 청크 수가 다른 합성 문서 둘 — #481 전에는 인스턴스에 둔 쪽별 청크 수가
                     섞였다(이 경로로 40건 중 0~3건). 확률로 드러나므로 결정적인 재현은
                     tests/unit/test_request_state_unit.py(R2)가 맡고, 여기서는 워커 경로를 본다.

Temporal 은 env `TEMPORAL_TEST_TARGET` 로 고른다(워크플로우는 이 시험의 탐침이라 샌드박스 없이 돌린다).
  local(기본)  SDK 개발 서버 — 처음 한 번 내려받는다(인터넷 필요)
  scp          배포 환경의 Temporal(워커와 같은 env `TEMPORAL_HOST`·`TEMPORAL_PORT`·`TEMPORAL_NAMESPACE`)에
               시험 전용 큐를 만들어 쓴다
"""
import asyncio
import concurrent.futures
import contextlib
import json
import os
import random
import threading
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from genon.preprocessor.worker import registry, runtime, settings

SAMPLES = Path(__file__).resolve().parents[2] / "sample_files"
#: (원본, chunk_size) — 문서와 파라미터를 서로 다르게 해 섞이면 결과가 달라지게 한다
CASES = [("md_sample.md", 300), ("md_sample2.md", 1200), ("txt_sample.txt", 200),
         ("docx_sample.docx", 800), ("html_sample.html", 500)]
PAGED = {"X": (1, 12), "Y": (6, 1)}         # 이름: (쪽 수, 쪽당 문단 수)
THREADS = 8
ROUNDS = 2                                  # 샘플은 2벌, 쪽 문서는 PAGED_ROUNDS 벌을 한꺼번에
PAGED_ROUNDS = 20


@contextlib.asynccontextmanager
async def _client():
    target = os.environ.get("TEMPORAL_TEST_TARGET", "local")
    if target == "scp":
        from temporalio.client import Client

        address = f"{os.environ['TEMPORAL_HOST']}:{os.environ.get('TEMPORAL_PORT') or 7233}"
        yield await Client.connect(address, namespace=os.environ.get("TEMPORAL_NAMESPACE") or "ns-ifa-app")
        return
    assert target == "local", f"TEMPORAL_TEST_TARGET 은 local 또는 scp: {target!r}"
    async with await WorkflowEnvironment.start_local() as env:
        yield env.client


@workflow.defn(name="t_steps")
class _Steps:
    """[(액티비티, 인자), …] 를 차례로 부른다."""

    @workflow.run
    async def run(self, steps: list) -> list:
        return [await workflow.execute_activity(name, arg, start_to_close_timeout=timedelta(minutes=5))
                for name, arg in steps]


def _counted(entries, gauge):
    """등록 함수(이름 = slug)를 같은 이름으로 감싸 slug 별로 동시에 돈 수의 최댓값을 잰다
    — 같은 프로세서 인스턴스를 여러 스레드가 함께 썼는지."""
    lock = threading.Lock()

    def wrap(entry, fn):
        @activity.defn(name=entry.slug)
        def run(arg: dict) -> dict:
            now, peak = f"{entry.slug}_now", entry.slug
            with lock:
                gauge[now] = gauge.get(now, 0) + 1
                gauge[peak] = max(gauge.get(peak, 0), gauge[now])
            try:
                return fn(arg)
            finally:
                with lock:
                    gauge[now] -= 1
        return run
    return [wrap(e, fn) for e, fn in zip(entries, registry.build(entries))]


def _paged_doc(name: str, pages: int, per_page: int) -> dict:
    """쪽 정보가 있는 파싱 결과(docling dict) — 외부 모델 없이 쪽별 청크 수를 만든다.
    test_request_state_unit.py 의 `_doc` 과 같은 모양이다. 시험 폴더가 패키지가 아니라 서로 import 하지 않는다."""
    d = DoclingDocument(name=name)
    words = ["계약", "보험", "약관", "지급", "청구", "기간"]
    for pg in range(1, pages + 1):
        d.add_page(page_no=pg, size=Size(width=600, height=800))
        for k in range(per_page):
            rnd = random.Random(f"{name}{pg}{k}")
            text = f"{name} {pg}쪽 {k}번. " + " ".join(rnd.choice(words) + str(rnd.randint(1, 999)) for _ in range(60))
            d.add_text(label=DocItemLabel.TEXT, text=text,
                       prov=ProvenanceItem(page_no=pg, bbox=BoundingBox(l=0, t=k * 10, r=500, b=k * 10 + 9),
                                           charspan=(0, len(text))))
    return d.export_to_dict()


def _sample_steps(name: str, chunk_size: int, tag: str) -> list:
    key = f"{tag}/{Path(name).stem}"
    # 기본 설정은 표 설명 LLM 이 켜져 있다(md_sample2 의 표) — 요청에서 끈다
    return [("parse", {"read_ref": {"root": "nfs", "path": name}, "output_ref": f"{key}/parse",
                       "params": {"table_text_desc": 0}}),
            ("chunk", {"parsed_ref": f"{key}/parse", "output_ref": f"{key}/chunk",
                       "params": {"chunk_size": chunk_size}})]


def _paged_steps(name: str, tag: str) -> list:
    return [("chunk", {"parsed_ref": f"paged/{name}", "output_ref": f"{tag}/{name}/chunk",
                       "params": {"chunk_size": 300}})]


@pytest.mark.smoke
def test_concurrent_activities_equal_one_at_a_time(tmp_path):
    for name, shape in PAGED.items():
        (tmp_path / "paged").mkdir(exist_ok=True)
        (tmp_path / "paged" / f"{name}.json").write_text(json.dumps(_paged_doc(name, *shape)))
    runtime.configure(settings.load({"TEMPORAL_HOST": "local", "WORKER_TASK_QUEUE": "local",
                                     "HEARTBEAT_INTERVAL": "1",
                                     "NFS_ROOT": str(SAMPLES), "ARTIFACT_ROOT": str(tmp_path)}))
    entries = [e for e in registry.scan() if e.slug in ("parse", "chunk")]
    gauge = {}

    async def main():
        async with _client() as client:
            queue = f"t-concurrency-{uuid.uuid4().hex[:8]}"
            with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as pool:
                async with Worker(client, task_queue=queue, workflows=[_Steps],
                                  activities=_counted(entries, gauge),
                                  activity_executor=pool, max_concurrent_activities=THREADS,
                                  workflow_runner=UnsandboxedWorkflowRunner()):
                    def run(steps):
                        return client.execute_workflow(_Steps.run, steps, id=f"{queue}-{uuid.uuid4().hex[:8]}",
                                                       task_queue=queue, execution_timeout=timedelta(minutes=15))
                    for name, size in CASES:                     # 기준: 한 건씩(프로세서 생성도 여기서)
                        await run(_sample_steps(name, size, "solo"))
                    for name in PAGED:
                        await run(_paged_steps(name, "solo"))
                    await asyncio.gather(*[run(_sample_steps(name, size, f"r{r}"))
                                           for r in range(ROUNDS) for name, size in CASES],
                                         *[run(_paged_steps(name, f"p{r}"))
                                           for r in range(PAGED_ROUNDS) for name in PAGED])

    asyncio.run(main())

    def read(tag, name, stage):
        data = json.loads((tmp_path / tag / name / f"{stage}.json").read_text())
        # reg_date 는 청크를 만든 시각이라 실행마다 다르다 — 그 필드만 빼고 비교한다
        return [{k: v for k, v in c.items() if k != "reg_date"} for c in data] if stage == "chunk" else data

    def samples(tag):
        return {Path(n).stem: (read(tag, Path(n).stem, "parse"), read(tag, Path(n).stem, "chunk")) for n, _ in CASES}

    def paged(tag):
        return {n: read(tag, n, "chunk") for n in PAGED}

    assert gauge["parse"] > 1 and gauge["chunk"] > 1                # 같은 프로세서가 실제로 겹쳐 돌았다
    assert [r for r in range(ROUNDS) if samples(f"r{r}") != samples("solo")] == []
    assert [r for r in range(PAGED_ROUNDS) if paged(f"p{r}") != paged("solo")] == []
    assert {c["n_chunk_of_page"] for c in paged("solo")["X"]} != {c["n_chunk_of_page"] for c in paged("solo")["Y"]}
