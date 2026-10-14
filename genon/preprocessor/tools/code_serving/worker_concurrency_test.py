#!/usr/bin/env python3
"""워커 모드(PREPROC_MODE=worker) parse + chunk 동시 처리 측정 — concurrency_test.py 의 워커판(#514).

HTTP 측정과 같은 잡(문서 7종 x on/off, --manifest)을 같은 순서로 보낸다. 요청 대신 문서마다 작은
워크플로 하나를 시작하고, 워크플로가 파드의 큐(--activity-queue)에 parse → chunk 액티비티를 차례로 건다.
워크플로와 이 클라이언트는 이 프로세스(코드스페이스)에서 돌고, 실제 처리는 워커 파드가 한다.

    python worker_concurrency_test.py --activity-queue <WORKER_TASK_QUEUE> --docdir <NFS_ROOT 기준 문서 폴더> \\
        --out <결과 폴더> [--artifact-root <ARTIFACT_ROOT 마운트 경로>] [--manifest monimo_manifest.json]

Temporal 접속은 워커와 같은 env(TEMPORAL_HOST·TEMPORAL_PORT·TEMPORAL_NAMESPACE)를 쓴다.
액티비티는 파일을 NFS 상대 경로(read_ref)로 받으므로 --docdir 은 파드의 NFS_ROOT 기준 상대 경로다.

측정 항목(잡마다, 단계마다):
  *_elapsed  예약 → 완료. HTTP 측정의 요청 소요 시간과 같은 뜻이다(대기 포함)
  *_wait     예약 → 시작. 큐·스레드가 비기를 기다린 시간. chunk_wait 가 "긴 parse 뒤 chunk 대기"다
  *_run      시작 → 완료. 파드 안에서 처리한 시간(slug 의 첫 호출은 프로세서 생성 시간을 포함한다)
  시각은 모두 워크플로 이력에 Temporal 서버가 기록한 이벤트 시각이다
  max_inflight  단계별로 파드 안에서 동시에 처리 중이던 최대 건수. WORKER_THREADS 만큼 올라가야 동시 처리다
--artifact-root 를 주면 산출물(ARTIFACT_ROOT/<ref>.json)을 읽어 HTTP 측정과 같은 간섭 검사(R1·R2·청크 수·필드 집합)도 한다.
산출물은 ARTIFACT_ROOT/measure/<run>/ 아래에 남는다. 측정이 끝나면 지운다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import timedelta

from temporalio import workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from concurrency_test import build_jobs, chunk_metrics, interference, load_manifest, parse_metrics, table  # noqa: E402


@workflow.defn(name="measure_parse_chunk")
class MeasureParseChunk:
    """parse → chunk 를 차례로 걸고 단계별 결과(실패면 error)를 돌려준다. 재시도하지 않는다 — 실패를 바로 드러낸다.
    시각은 여기서 재지 않는다. 끝난 뒤 이력(history)의 예약·시작·완료 이벤트 시각(Temporal 서버 기록)을 읽는다."""

    @workflow.run
    async def run(self, spec: dict) -> dict:
        out = {}
        for stage in ("parse", "chunk"):
            try:
                out[stage] = {"result": await workflow.execute_activity(
                    spec[f"{stage}_activity"], spec[f"{stage}_arg"], task_queue=spec["queue"],
                    start_to_close_timeout=timedelta(seconds=spec["timeout"]),
                    retry_policy=RetryPolicy(maximum_attempts=1))}
            except Exception as exc:  # noqa: BLE001 — 실패는 결과로 돌려주고 다음 단계는 건너뛴다
                out[stage] = {"error": str(exc.__cause__ or exc)}
                break
        return out


async def activity_times(handle) -> dict:
    """이력에서 액티비티 이름별 예약·시작·완료(실패·시간초과 포함) 시각(epoch 초)을 모은다."""
    sched, times = {}, {}
    async for ev in handle.fetch_history_events():
        t = ev.event_time.ToDatetime().timestamp()
        if ev.HasField("activity_task_scheduled_event_attributes"):
            name = ev.activity_task_scheduled_event_attributes.activity_type.name
            sched[ev.event_id] = name
            times[name] = {"scheduled": t}
            continue
        for field, key in (("activity_task_started_event_attributes", "started"),
                           ("activity_task_completed_event_attributes", "completed"),
                           ("activity_task_failed_event_attributes", "completed"),
                           ("activity_task_timed_out_event_attributes", "completed")):
            if ev.HasField(field):
                times[sched[getattr(ev, field).scheduled_event_id]][key] = t
    return times


def _spec(job: dict, args, prefix: str) -> dict:
    key = f"{prefix}/{job['name']}"
    params = dict(job["params"])
    chunk_params = {} if args.chunk_size is None else {"chunk_size": args.chunk_size}
    return {"queue": args.activity_queue, "timeout": args.timeout,
            "parse_activity": args.parse_activity, "chunk_activity": args.chunk_activity,
            "parse_arg": {"read_ref": {"root": "nfs", "path": job["file_path"]}, "output_ref": f"{key}/parse",
                          "source_name": job["name"], "params": params},
            "chunk_arg": {"parsed_ref": f"{key}/parse", "output_ref": f"{key}/chunk",
                          "source_name": job["name"], "params": chunk_params}}


def _load(artifact_root: str, ref: str):
    with open(os.path.join(artifact_root, f"{ref}.json"), encoding="utf-8") as f:
        return json.load(f)


def _record(job: dict, out: dict, times: dict, args) -> dict:
    rec = {"name": job["name"], "variant": job["variant"], "file_path": job["file_path"]}
    for stage in ("parse", "chunk"):
        s, t = out.get(stage), times.get(getattr(args, f"{stage}_activity"), {})
        if not s:
            continue
        if "completed" in t:
            rec[f"{stage}_elapsed"] = round(t["completed"] - t["scheduled"], 2)
        if "started" in t:
            rec[f"{stage}_wait"] = round(t["started"] - t["scheduled"], 2)
            rec[f"{stage}_run"] = round(t["completed"] - t["started"], 2)
            rec[f"_{stage}_span"] = (t["started"], t["completed"])
        if "error" in s:
            rec[f"{stage}_code"], rec[f"{stage}_err"] = -1, s["error"]
            continue
        rec[f"{stage}_code"], rec[f"{stage}_err"] = 0, None
        if args.artifact_root:
            data = _load(args.artifact_root, s["result"]["ref"])
            if stage == "parse":
                rec.update(parse_metrics(data if isinstance(data, dict) and "elements" in data else {"document": data}))
            else:
                rec.update(chunk_metrics(data))
    rec["t_start"] = min((v["scheduled"] for v in times.values()), default=0)
    rec["t_end"] = max((v.get("completed", 0) for v in times.values()), default=0)
    return rec


def _max_inflight(spans: list[tuple[float, float]]) -> int:
    events = sorted([(a, 1) for a, _ in spans] + [(b, -1) for _, b in spans])
    peak = cur = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)
    return peak


async def run_phase(client: Client, jobs, args, run_id: str, phase: str, workers: int) -> dict:
    sem = asyncio.Semaphore(workers)

    async def one(job):
        async with sem:
            handle = await client.start_workflow(MeasureParseChunk.run, _spec(job, args, f"measure/{run_id}/{phase}"),
                                                 id=f"measure-{run_id}-{phase}-{job['name']}", task_queue=args.wf_queue)
            out = await handle.result()
            return _record(job, out, await activity_times(handle), args)

    t0 = time.time()
    recs = await asyncio.gather(*(one(j) for j in jobs))
    wall = round(time.time() - t0, 2)
    base = min((r["t_start"] for r in recs if r.get("t_start")), default=0)
    res = {"phase": phase, "workers": workers, "wall": wall}
    for stage in ("parse", "chunk"):
        spans = [r.pop(f"_{stage}_span") for r in recs if f"_{stage}_span" in r]
        res[f"max_inflight_{stage}"] = _max_inflight(spans)
        waits = [r[f"{stage}_wait"] for r in recs if f"{stage}_wait" in r]
        res[f"max_{stage}_wait"] = max(waits, default=None)
    for r in recs:
        r["t_start"], r["t_end"] = round(r["t_start"] - base, 2), round(r["t_end"] - base, 2)
    res["jobs"] = recs
    return res


def wait_table(phase: dict) -> str:
    cols = ["name", "parse_wait", "parse_run", "chunk_wait", "chunk_run"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in phase["jobs"]:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


async def amain(args) -> int:
    extra = {}
    for kv in args.param:
        k, _, v = kv.partition("=")
        try:
            extra[k] = int(v)
        except ValueError:
            extra[k] = v
    jobs = [] if args.only_manifest else build_jobs(args.docdir, extra)
    if args.manifest:
        for j in load_manifest(args.manifest, args.docdir):
            j["params"].update(extra)
            jobs.append(j)
    if not jobs:
        raise SystemExit("보낼 잡이 없습니다 (--only-manifest 에는 --manifest 가 필요합니다).")
    os.makedirs(args.out, exist_ok=True)

    address = f"{os.environ['TEMPORAL_HOST']}:{os.environ.get('TEMPORAL_PORT') or 7233}"
    client = await Client.connect(address, namespace=os.environ.get("TEMPORAL_NAMESPACE") or "ns-ifa-app")
    run_id = time.strftime("%Y%m%d-%H%M%S")
    args.wf_queue = f"measure-wf-{uuid.uuid4().hex[:8]}"
    results = {"mode": "worker", "activity_queue": args.activity_queue, "run": run_id,
               "extra_params": extra, "phases": {}}
    print(f"[worker] run={run_id} activity_queue={args.activity_queue} workflow_queue={args.wf_queue}", flush=True)

    async with Worker(client, task_queue=args.wf_queue, workflows=[MeasureParseChunk],
                      workflow_runner=UnsandboxedWorkflowRunner(), max_concurrent_workflow_tasks=100):
        solo = None
        for phase in [s.strip() for s in args.phases.split(",") if s.strip()]:
            workers = 1 if phase == "solo" else args.workers
            print(f"[{phase}] start — {len(jobs)} jobs, workflows in flight={workers}", flush=True)
            res = await run_phase(client, jobs, args, run_id, phase, workers)
            res["issues"] = interference(res, solo)
            res["sum_parse_elapsed"] = round(sum(r.get("parse_elapsed") or 0 for r in res["jobs"]), 2)
            results["phases"][phase] = res
            if phase == "solo":
                solo = res
            print(f"[{phase}] wall={res['wall']}s  parse 합계={res['sum_parse_elapsed']}s  "
                  f"동시 처리 최대 parse={res['max_inflight_parse']} chunk={res['max_inflight_chunk']}  "
                  f"대기 최대 parse={res['max_parse_wait']}s chunk={res['max_chunk_wait']}s", flush=True)
            print(table(res), flush=True)
            print(wait_table(res), flush=True)
            print(f"[{phase}] 간섭 징후 {len(res['issues'])}건", flush=True)
            for i in res["issues"]:
                print("  - " + i, flush=True)
            with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--activity-queue", required=True, help="워커 파드의 WORKER_TASK_QUEUE")
    p.add_argument("--docdir", required=True, help="워커 파드의 NFS_ROOT 기준 문서 폴더(상대 경로)")
    p.add_argument("--out", required=True)
    p.add_argument("--artifact-root", default=None, help="이 머신에서 보이는 ARTIFACT_ROOT. 주면 간섭 검사도 한다")
    p.add_argument("--phases", default="solo,concurrent")
    p.add_argument("--workers", type=int, default=15, help="concurrent 단계에서 동시에 띄울 워크플로 수")
    p.add_argument("--chunk-size", type=int, default=None, help="생략하면 서빙 config 값")
    p.add_argument("--timeout", type=float, default=1800, help="액티비티 하나의 처리 시간 한도(초). 대기 시간은 포함하지 않는다")
    p.add_argument("--param", action="append", default=[], help="모든 parse 요청에 더할 params KEY=VALUE")
    p.add_argument("--manifest", default=None, help="추가 잡 매니페스트 JSON(name, file, params). 예: monimo_manifest.json")
    p.add_argument("--only-manifest", action="store_true", help="기본 문서 7종 x on/off 를 빼고 매니페스트만 보낸다")
    p.add_argument("--parse-activity", default="parse")
    p.add_argument("--chunk-activity", default="chunk")
    return asyncio.run(amain(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
