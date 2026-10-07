"""액티비티 본문 — `ParserCore`·`ChunkerCore` 의 `run_activity(arg)` 가 여기로 온다.

인자는 dict 하나, 반환은 ref 와 요약뿐이다(페이로드 2MB 한계). 경로는 상대 참조만 받는다(paths.py).

    parse  arg = {read_ref, output_ref, source_name?, params?}
           read_ref 의 파일을 이 인스턴스의 `__call__`(입력·라우트·출력 훅 전부)로 파싱해
           ARTIFACT_ROOT/<output_ref>.json 에 쓴다. 저장 모양은 파이프라인이 HTTP 응답에서 꺼내
           저장하던 것과 같다 — `document` 가 있으면 그것, 없으면 `elements` 를 가진 응답 전체.
    chunk  arg = {parsed_ref, output_ref, source_name?, params?}
           parsed_ref 의 파싱 결과를 params["document"] 로 넘기고 file_path 는 빈 값으로 부른다 —
           현 파이프라인의 /chunker 호출과 같다(청크 메타가 그대로다).

params 는 요청 파라미터 + 파이프라인이 싣는 전처리 정책(llm_cache·workflow_id·error_policy·
request_deadline)이다. `interim_root` 는 다른 파드의 절대경로라 버리고, 캐시를 켠 요청이면 이 파드의
LLM_CACHE_ROOT 로 채운다.

프로세서는 별도 스레드에서 `asyncio.run` 으로 돌리고, 액티비티 스레드는 끝날 때까지 하트비트를
친다(파싱 본체가 동기라 같은 스레드에서 돌리면 하트비트가 멈춘다). 동기 파싱은 중간에 멈출 수
없으므로, 취소·마감 초과로 처리 스레드를 남긴 채 돌아갈 때는 이 프로세스를 끝낸다 — 부모
(`__main__`)가 새로 띄운다. 프로세스당 1건이라 다른 작업에는 영향이 없다.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
import time

from fastapi.encoders import jsonable_encoder
from temporalio import activity
from temporalio.exceptions import ApplicationError, CancelledError

from genon.preprocessor.processing.core.cli import mock_request

from . import paths
from .errors import to_application_error
from .settings import Settings

log = logging.getLogger("genon.worker")

#: 처리 스레드를 남긴 채 돌아갈 때의 종료 코드 — 부모가 이 자식을 새로 띄운다.
EXIT_RESTART = 75
_EXIT_DELAY_SEC = 10.0      # 실패 결과가 서버에 보고될 시간

_CFG: Settings | None = None
#: 처리 스레드를 남긴 뒤로는 새 작업을 받지 않는다 — 공유 인스턴스가 두 건을 동시에 돌게 된다.
_POISONED = False


def configure(cfg: Settings) -> None:
    global _CFG
    _CFG = cfg


def _cfg() -> Settings:
    if _CFG is None:
        raise RuntimeError("worker.runtime.configure() 가 먼저 불려야 합니다")
    return _CFG


def _restart_process(reason: str) -> None:
    global _POISONED
    if _POISONED:
        return
    _POISONED = True
    log.error("처리 스레드를 멈출 수 없어 이 워커 프로세스를 다시 띄운다 — %s", reason)
    timer = threading.Timer(_EXIT_DELAY_SEC, os._exit, (EXIT_RESTART,))
    timer.daemon = True
    timer.start()


def guard() -> None:
    """재기동을 기다리는 프로세스가 받은 작업은 처리하지 않고 재시도로 돌려보낸다."""
    if _POISONED:
        raise ApplicationError("워커 프로세스 재기동 중 — 재시도한다", type="worker_restarting",
                               non_retryable=False)


def _seconds(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _call(proc, file_path: str, params: dict, heartbeat_interval: float):
    """proc.__call__ 을 별도 스레드에서 돌리며 하트비트. 마감은 request_deadline(초)."""
    deadline = _seconds(params.get("request_deadline"))

    async def call():
        return await proc(mock_request(), file_path, **params)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="processor")
    future = None
    started = time.monotonic()
    try:
        # submit 도 이 경계 안에 둔다 — 취소 예외는 동기 액티비티 스레드에 아무 때나 주입된다(SDK).
        future = pool.submit(asyncio.run, call())
        while True:
            # 완료 대기와 결과 회수를 나눈다 — 처리 본체가 던진 TimeoutError 를 대기 시간 초과로
            # 오인하지 않게(3.11+ 에서 concurrent.futures.TimeoutError 는 내장 TimeoutError 다).
            wait = heartbeat_interval
            if deadline:
                wait = max(0.0, min(wait, deadline - (time.monotonic() - started)))
            done, _ = concurrent.futures.wait([future], timeout=wait)
            if done:
                return future.result()
            if deadline and time.monotonic() - started >= deadline:
                raise TimeoutError(f"request_deadline {deadline:.0f}초를 넘었습니다")
            activity.heartbeat()
    except BaseException as exc:
        # 처리 스레드가 아직 돌고 있으면(또는 submit 직후라 알 수 없으면) 이 프로세스를 다시 띄운다.
        if future is None or not future.done():
            _restart_process(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        pool.shutdown(wait=False)


def _parsed_payload(result):
    """파서 결과 → 저장할 모양. 파이프라인이 HTTP 응답에서 꺼내던 규칙과 같다."""
    payload = jsonable_encoder(result) or {}
    if isinstance(payload, dict):
        if payload.get("document"):
            return payload["document"]
        if isinstance(payload.get("elements"), list):
            return payload
    keys = sorted(payload)[:10] if isinstance(payload, dict) else type(payload).__name__
    raise ValueError(f"파서 결과에 document/elements 가 없습니다 — 받은 키: {keys}")


def _kb(n: int) -> str:
    return f"{n / 1024:.0f}KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f}MB"


def _clip(value, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit}자)"


def _summarize(stage: str, payload, size: int, limit: int) -> tuple[int, str, str]:
    if stage == "chunk":
        count = len(payload)
        first = payload[0].get("text", payload[0]) if count and isinstance(payload[0], dict) else ""
        return count, f"청크 {count}건 · {_kb(size)}", _clip(first, limit)
    if isinstance(payload.get("elements"), list):
        count = len(payload["elements"])
    else:
        count = len(payload.get("texts") or [])
    text = payload.get("text") or payload.get("elements") or payload.get("texts") or ""
    return count, f"파싱 {_kb(size)} · 요소 {count}", _clip(text, limit)


def execute(proc, stage: str, arg: dict) -> dict:
    """액티비티 본체. 하트비트 때문에 Temporal 액티비티 문맥 안에서 불러야 한다."""
    cfg = _cfg()
    started = time.monotonic()
    name = activity.info().activity_type
    where = f"{name} {arg.get('source_name') or arg.get('output_ref', '')}"
    try:
        params = dict(arg.get("params") or {})
        params.pop("interim_root", None)
        if params.get("llm_cache"):
            if cfg.llm_cache_root:
                params["interim_root"] = cfg.llm_cache_root
            else:
                params.pop("llm_cache", None)
        output_ref = arg["output_ref"]
        if stage == "parse":
            file_path = str(paths.read_path(cfg.nfs_root, cfg.artifact_root, arg["read_ref"]))
            payload = _parsed_payload(_call(proc, file_path, params, cfg.heartbeat_interval))
        else:
            params["document"] = paths.load_json(cfg.artifact_root, arg["parsed_ref"])
            payload = jsonable_encoder(_call(proc, "", params, cfg.heartbeat_interval)) or []
        activity.heartbeat()           # 저장 직전 — 취소됐으면 여기서 멈춘다
        size = paths.save_json(cfg.artifact_root, output_ref, payload)
    except CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 성격 판정은 to_application_error 한 곳에서
        raise to_application_error(exc, where) from exc
    count, summary, preview = _summarize(stage, payload, size, cfg.preview_chars)
    return {"stage": stage, "activity": name, "ref": output_ref, "bytes": size, "count": count,
            "summary": summary, "preview": preview,
            "duration_ms": int((time.monotonic() - started) * 1000)}
