"""전처리기 Temporal 워커의 동시 실행 — 한 프로세스에서 여러 건을 돌려도 결과가 한 건씩과 같은가(#502·#507).

실제 등록부(`registry.scan()` → `build`)와 실제 파사드(activities/parse.py·chunk.py)를 쓴다. 한 건씩 돌린
결과를 기준으로, 같은 것을 한꺼번에 돌린 결과가 같아야 한다. 시험 둘:

  test_concurrent_activities_equal_one_at_a_time
    시험 안에서 워커를 조립해 액티비티 스레드를 여럿 둔다. 샘플 parse→chunk(모델·LLM 없이 처리되는
    형식들, 문서마다 chunk_size 를 다르게)와 쪽별 청크 수가 다른 합성 문서 chunk — #481 전에는
    인스턴스에 둔 쪽별 청크 수가 섞였다(이 경로로 40건 중 0~3건). 확률로 드러나므로 결정적인 재현은
    tests/unit/test_request_state_unit.py(R2)가 맡고, 여기서는 같은 프로세서를 여러 스레드가 실제로
    겹쳐 쓴 상태(gauge)에서 결과가 같은지를 본다
  test_entrypoint_runs_monimo_doc_types_with_default_and_changed_copies
    코드서빙이 쓰는 진입점(`python -m genon.preprocessor.worker`)을 자식 프로세스로 그대로 띄운다
    (env 로 설정, `WORKER_THREADS`). 모니모 사이트 설정·문서유형별 표본을 기본 parse·chunk 와
    **설정·코드를 고친 slug 사본** parse_t·chunk_t 로 함께 돌린다 — Studio 가 사본을 만들어 거기서부터
    바꾸는 꼴(#472 의 new 가 하는 일). 사본은 시험 동안만 activities/ 에 폴더로 만들고 끝나면 지운다.
    사본의 원천은 그 slug 의 **실제** 설정 폴더(_folders.config_dir — 사이트 완성본이 덮어쓰면 그쪽)와
    저장소의 processor.py 다. 그 뒤 chunk_t 의 config.yaml 에서 chunk_size 를 줄이고, processor.py 끝에
    훅을 덧붙인다(parse_t 는 청크 메타 SLUG_MARK, chunk_t 는 청크 본문 앞 표식).
    사이트 완성본(sites/monimo)은 임시 폴더에 복사해 GENOS_RESOURCE_DIR 로 준다 — 기본 parse 는 그 폴더의
    activities/parse/ 설정(모니모 14 유형)으로 돈다(#497). 모니모 폴더는 공개 배포본에 없어 배포본
    (코드서빙 파드)에서는 건너뛴다

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
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from genon.preprocessor.worker import registry, runtime, settings

PREPROC = Path(__file__).resolve().parents[2]              # genon/preprocessor
REPO = PREPROC.parents[1]
ACTIVITIES, SAMPLES = PREPROC / "activities", PREPROC / "sample_files"
SITE = REPO / "genon" / "sites" / "monimo"                   # 완성본: resource/ + activities/<slug>/


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
    """[(액티비티, 인자), …] 를 차례로 부른다. 재시도 없음 — 실패를 바로 드러낸다."""

    @workflow.run
    async def run(self, steps: list) -> list:
        return [await workflow.execute_activity(name, arg, start_to_close_timeout=timedelta(minutes=5),
                                                retry_policy=RetryPolicy(maximum_attempts=1))
                for name, arg in steps]


def _run(client, queue: str, steps: list):
    return client.execute_workflow(_Steps.run, steps, task_queue=queue, id=f"{queue}-{uuid.uuid4().hex[:8]}",
                                   execution_timeout=timedelta(minutes=15))


def _read(root: Path, *parts: str):
    """산출물 JSON. reg_date 는 청크를 만든 시각이라 실행마다 다르다 — 그 필드만 빼고 비교한다."""
    data = json.loads(root.joinpath(*parts).with_suffix(".json").read_text(encoding="utf-8"))
    return [{k: v for k, v in c.items() if k != "reg_date"} for c in data] if parts[-1] == "chunk" else data


# ── 1. 시험 안에서 조립한 워커 — 같은 프로세서를 여러 스레드가 겹쳐 쓴다 ─────────────────────────────

#: (원본, chunk_size) — 문서와 파라미터를 서로 다르게 해 섞이면 결과가 달라지게 한다
CASES = [("md_sample.md", 300), ("md_sample2.md", 1200), ("txt_sample.txt", 200),
         ("docx_sample.docx", 800), ("html_sample.html", 500)]
PAGED = {"X": (1, 12), "Y": (6, 1)}         # 이름: (쪽 수, 쪽당 문단 수)
THREADS = 8
ROUNDS = 2                                  # 샘플은 2벌, 쪽 문서는 PAGED_ROUNDS 벌을 한꺼번에
PAGED_ROUNDS = 20


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
                    for name, size in CASES:                     # 기준: 한 건씩(프로세서 생성도 여기서)
                        await _run(client, queue, _sample_steps(name, size, "solo"))
                    for name in PAGED:
                        await _run(client, queue, _paged_steps(name, "solo"))
                    await asyncio.gather(*[_run(client, queue, _sample_steps(name, size, f"r{r}"))
                                           for r in range(ROUNDS) for name, size in CASES],
                                         *[_run(client, queue, _paged_steps(name, f"p{r}"))
                                           for r in range(PAGED_ROUNDS) for name in PAGED])

    asyncio.run(main())

    def samples(tag):
        return {Path(n).stem: (_read(tmp_path, tag, Path(n).stem, "parse"), _read(tmp_path, tag, Path(n).stem, "chunk"))
                for n, _ in CASES}

    def paged(tag):
        return {n: _read(tmp_path, tag, n, "chunk") for n in PAGED}

    assert gauge["parse"] > 1 and gauge["chunk"] > 1                # 같은 프로세서가 실제로 겹쳐 돌았다
    assert [r for r in range(ROUNDS) if samples(f"r{r}") != samples("solo")] == []
    assert [r for r in range(PAGED_ROUNDS) if paged(f"p{r}") != paged("solo")] == []
    assert {c["n_chunk_of_page"] for c in paged("solo")["X"]} != {c["n_chunk_of_page"] for c in paged("solo")["Y"]}


# ── 2. 운영 진입점 워커 — 기본과 설정·코드를 고친 slug 사본을 함께 ──────────────────────────────────

#: (표본, doc_type) — 외부 모델 없이 처리되는 모니모 유형들(검증 #43 QA 기준선과 같은 표본)
MONIMO = [("monimo/monimo_faq_json_sample.json", "faq"), ("monimo/monimo_term_sample.xlsx", "term"),
          ("monimo/monimo_menu_sample.xlsx", "menu"), ("monimo/monimo_cs_slf_sample.json", "cs_slf"),
          ("monimo/monimo_cs_ssf_sample.dtms", "cs_ssf"), ("json/monimo_event_sample.json", "monimo_event"),
          ("monimo/monimo_cs_hpp_sample.html", "cs_hpp")]
COMBOS = [("parse", "chunk"), ("parse_t", "chunk_t")]
ENTRY_THREADS = 3
MARK = "[chunk_t] "

#: 사본 파일 끝에 덧붙이는 코드 — 복사한 파사드를 같은 이름으로 다시 상속해 훅 하나를 바꾼다
PARSE_T_CODE = '''

class DocumentProcessor(DocumentProcessor):          # 사본에서 고친 코드(시험)
    def edit_output(self, ext, doc_type, result, **kwargs):
        return tb.set_chunk_metadata(result, {"SLUG_MARK": "parse_t"})
'''
CHUNK_T_CODE = f'''

class DocumentProcessor(DocumentProcessor):          # 사본에서 고친 코드(시험)
    def edit_chunk(self, text, info, **kwargs):
        return {MARK!r} + text
'''


@pytest.fixture
def slug_copies(tmp_path, monkeypatch):
    """사이트 완성본의 임시 사본(GENOS_RESOURCE_DIR = 그 resource/) + 사본 slug 폴더 둘(설정·코드 모두 복사 뒤 변경)."""
    from genon.preprocessor.activities import _folders

    site = tmp_path / "monimo"
    shutil.copytree(SITE, site, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("GENOS_RESOURCE_DIR", str(site / "resource"))
    made = []
    try:
        for slug, origin, code in (("parse_t", "parse", PARSE_T_CODE), ("chunk_t", "chunk", CHUNK_T_CODE)):
            dest = ACTIVITIES / slug
            assert not dest.exists(), f"{dest} 가 이미 있다 — 시험이 덮어쓰지 않는다"
            made.append(dest)
            # 원천 = 그 slug 의 실제 설정 폴더(사이트가 덮어쓰면 그쪽) + 저장소의 코드
            shutil.copytree(_folders.config_dir(origin), dest, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
            for name in ("__init__.py", "processor.py"):
                shutil.copyfile(ACTIVITIES / origin / name, dest / name)
            (dest / "processor.py").write_text((dest / "processor.py").read_text(encoding="utf-8") + code,
                                               encoding="utf-8")
        cfg = yaml.safe_load((ACTIVITIES / "chunk_t" / "config.yaml").read_text(encoding="utf-8"))
        cfg["chunking"].update({"chunk_size": 80, "min_chunk_size": 40})   # 설정 복사 뒤 변경 — 표본이 짧아 작게
        (ACTIVITIES / "chunk_t" / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        yield site / "resource"
    finally:
        for p in made:
            shutil.rmtree(p, ignore_errors=True)


def _monimo_steps(sample: str, doc_type: str, parse: str, chunk: str, tag: str) -> list:
    key = f"{tag}/{doc_type}/{parse}-{chunk}"
    return [(parse, {"read_ref": {"root": "nfs", "path": sample}, "output_ref": f"{key}/parse",
                     "params": {"doc_type": doc_type, "table_text_desc": 0}}),
            (chunk, {"parsed_ref": f"{key}/parse", "output_ref": f"{key}/chunk", "params": {"doc_type": doc_type}})]


@pytest.mark.smoke
@pytest.mark.skipif(not (SITE.is_dir() and (SAMPLES / "monimo").is_dir()),
                    reason="모니모 사이트 설정·표본 없음(공개 배포본) — 코드스페이스·로컬에서 돈다")
def test_entrypoint_runs_monimo_doc_types_with_default_and_changed_copies(slug_copies, tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    log = tmp_path / "worker.log"                      # 실패 진단용

    async def main():
        async with _client() as client:
            host, port = client.service_client.config.target_host.rsplit(":", 1)
            queue = f"t-entry-{uuid.uuid4().hex[:8]}"
            worker_env = {**os.environ, "TEMPORAL_HOST": host, "TEMPORAL_PORT": port,
                          "TEMPORAL_NAMESPACE": client.namespace, "WORKER_TASK_QUEUE": queue,
                          "WORKER_PROCESSES": "1", "WORKER_THREADS": str(ENTRY_THREADS), "PORT": "0",
                          "NFS_ROOT": str(SAMPLES), "ARTIFACT_ROOT": str(artifact),
                          "GENOS_RESOURCE_DIR": str(slug_copies),
                          "PYTHONPATH": f"{REPO}{os.pathsep}{PREPROC}"}
            with log.open("w") as fh:
                proc = subprocess.Popen([sys.executable, "-m", "genon.preprocessor.worker"],
                                        cwd=REPO, env=worker_env, stdout=fh, stderr=subprocess.STDOUT)
            try:
                async with Worker(client, task_queue=queue, workflows=[_Steps],
                                  workflow_runner=UnsandboxedWorkflowRunner()):
                    for sample, doc_type in MONIMO:                                  # 기준: 한 건씩
                        for parse, chunk in COMBOS:
                            await _run(client, queue, _monimo_steps(sample, doc_type, parse, chunk, "solo"))
                    await asyncio.gather(*[_run(client, queue, _monimo_steps(sample, doc_type, parse, chunk, "all"))
                                           for sample, doc_type in MONIMO for parse, chunk in COMBOS])
            finally:
                proc.terminate()
                proc.wait(15)

    asyncio.run(main())

    def outputs(tag):
        return {(d, p, c): (_read(artifact, tag, d, f"{p}-{c}", "parse"), _read(artifact, tag, d, f"{p}-{c}", "chunk"))
                for _, d in MONIMO for p, c in COMBOS}

    solo = outputs("solo")
    assert outputs("all") == solo                                                 # 한꺼번에 돌려도 한 건씩과 같다
    for _, d in MONIMO:
        base, copy = solo[(d, "parse", "chunk")][1], solo[(d, "parse_t", "chunk_t")][1]
        assert base and copy
        assert all(not c["text"].startswith(MARK) and c.get("SLUG_MARK") is None for c in base), d   # 기본은 그대로
        assert all(c["text"].startswith(MARK) and c.get("SLUG_MARK") == "parse_t" for c in copy), d  # 사본 코드
    assert any(len(solo[(d, "parse_t", "chunk_t")][1]) > len(solo[(d, "parse", "chunk")][1]) for _, d in MONIMO)  # 사본 설정
