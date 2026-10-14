"""처리 단계 시간·외부 호출 계측(#514). env `GENOS_PROFILE=1` 일 때만 동작하고, 아니면 아무것도 하지 않는다.

병목이 CPU 인지 외부 모델 대기인지 가르기 위한 측정용이다. 로그 한 줄이 이벤트 하나이고 접두사는 `[prof]` 다.

    [prof] stage name=<단계> doc=<문서> wall=<초> thread_cpu=<초> proc_cpu=<초>
    [prof] call ep=<호스트+경로> status=<코드> wall=<초> inflight=<같은 ep 동시 호출 수> total=<전체 동시 호출 수>

- `thread_cpu` 는 그 단계를 부른 스레드의 CPU 시간이다. 단계가 안에서 띄운 스레드(쪽 묶음·보강 병렬 호출)의 CPU 는
  들어가지 않는다.
- `proc_cpu` 는 프로세스 전체 CPU 증분이다. 한 번에 한 문서만 처리할 때(순차 측정)만 그 단계의 몫으로 읽는다.
- `call` 은 requests·httpx 의 전송 계층에서 모든 HTTP 호출을 잰다(레이아웃 VLM, 보강 LLM, OCR 등 호출부와 무관).
  `inflight`·`total` 은 호출이 시작될 때 자기를 포함해 진행 중인 수다.

`tools/code_serving/prof_report.py` 가 이 로그를 표로 묶는다.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import threading
import time
from urllib.parse import urlsplit

ENABLED = os.environ.get("GENOS_PROFILE") == "1"

_log = logging.getLogger("genon.prof")
_doc: contextvars.ContextVar[str] = contextvars.ContextVar("genon_prof_doc", default="-")
_lock = threading.Lock()
_inflight: dict[str, int] = {}
_installed = False


def set_doc(name: str | None) -> None:
    """이 스레드(문맥)에서 이어지는 stage 로그에 붙일 문서 이름."""
    if ENABLED:
        _doc.set(str(name or "-").replace(" ", "_"))


@contextlib.contextmanager
def stage(name: str):
    """with 블록 하나를 단계 하나로 잰다."""
    if not ENABLED:
        yield
        return
    w0, t0, p0 = time.monotonic(), time.thread_time(), time.process_time()
    try:
        yield
    finally:
        _log.info("[prof] stage name=%s doc=%s wall=%.3f thread_cpu=%.3f proc_cpu=%.3f", name, _doc.get(),
                  time.monotonic() - w0, time.thread_time() - t0, time.process_time() - p0)


def _endpoint(url) -> str:
    parts = urlsplit(str(url))
    return f"{parts.netloc}{parts.path}" or "-"


def _begin(url) -> tuple[str, int, int, float]:
    ep = _endpoint(url)
    with _lock:
        _inflight[ep] = _inflight.get(ep, 0) + 1
        return ep, _inflight[ep], sum(_inflight.values()), time.monotonic()


def _end(started: tuple[str, int, int, float], status) -> None:
    ep, inflight, total, w0 = started
    with _lock:
        _inflight[ep] -= 1
    _log.info("[prof] call ep=%s status=%s wall=%.3f inflight=%d total=%d", ep, status,
              time.monotonic() - w0, inflight, total)


def install() -> None:
    """requests·httpx 의 전송 계층을 감싸 모든 HTTP 호출을 잰다. 프로세스마다 한 번, 꺼져 있으면 아무것도 안 한다."""
    global _installed
    if not ENABLED or _installed:
        return
    _installed = True
    _log.setLevel(logging.INFO)

    def measure(url, send):
        started, status = _begin(url), "error"
        try:
            resp = send()
            status = getattr(resp, "status_code", "ok")
            return resp
        finally:
            _end(started, status)

    async def ameasure(url, send):
        started, status = _begin(url), "error"
        try:
            resp = await send()
            status = getattr(resp, "status_code", "ok")
            return resp
        finally:
            _end(started, status)

    try:
        import requests.adapters

        orig = requests.adapters.HTTPAdapter.send

        def send(self, request, *args, **kwargs):
            return measure(request.url, lambda: orig(self, request, *args, **kwargs))

        requests.adapters.HTTPAdapter.send = send
    except ImportError:
        pass
    try:
        import httpx

        orig_sync = httpx.HTTPTransport.handle_request
        orig_async = httpx.AsyncHTTPTransport.handle_async_request

        def handle_request(self, request):
            return measure(request.url, lambda: orig_sync(self, request))

        async def handle_async_request(self, request):
            return await ameasure(request.url, lambda: orig_async(self, request))

        httpx.HTTPTransport.handle_request = handle_request
        httpx.AsyncHTTPTransport.handle_async_request = handle_async_request
    except ImportError:
        pass
    _log.info("[prof] enabled pid=%d", os.getpid())
