"""전처리기 Temporal 워커 진입점 — 코드서빙 시작 커맨드로 쓴다.

    python -m genon.preprocessor.worker          (저장소 루트에서 — facade 와 같은 import 규칙)

부모 프로세스는 /health(PORT, startupProbe 용)만 서비스하고, 실제 워커는 자식 프로세스
WORKER_PROCESSES 개가 돈다. 자식마다 Temporal 워커 1개 · 동시 1건이다(패키지 docstring 참고).
코드서빙 규약(GenOS 템플릿): startupProbe `GET /health :8080` 이 기동 때 한 번 200 을 받아야 하고
그 뒤로는 probe 가 없다. PID1 은 supervisord 라 이 프로세스가 끝나면 START_COMMAND 를 다시 실행한다.
그래서 기동 후 503 에 기대지 않는다 — 자식이 죽으면 그 자식만 다시 띄우고(한 건만 영향),
짧은 시간에 너무 자주 죽으면 전체를 끝내 supervisord 재기동에 맡긴다.
"""
from __future__ import annotations

import http.server
import logging
import multiprocessing as mp
import signal
import sys
import threading
import time

from . import registry, runner, settings

log = logging.getLogger("genon.worker")

_RESTART_LIMIT = 5
_RESTART_WINDOW_SEC = 300


class _Health(http.server.BaseHTTPRequestHandler):
    children: list = []

    def do_GET(self):  # noqa: N802
        ok = self.path.rstrip("/") in ("/health", "/healthcheck") and all(p.is_alive() for p in self.children)
        self.send_response(200 if ok else 503)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}' if ok else b'{"status":"down"}')

    def log_message(self, *args):
        pass


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(levelname)s %(message)s")
    cfg = settings.load()
    entries = registry.scan()
    log.info("task_queue=%s processes=%d NFS_ROOT=%s ARTIFACT_ROOT=%s 액티비티=%s",
             cfg.task_queue, cfg.processes, cfg.nfs_root, cfg.artifact_root,
             ", ".join(e.slug for e in entries) or "<없음>")
    ctx = mp.get_context("spawn")

    def spawn(i: int):
        p = ctx.Process(target=runner.child, args=(i,), name=f"worker-{i}", daemon=True)
        p.start()
        return p

    children = [spawn(i) for i in range(cfg.processes)]
    _Health.children = children
    server = http.server.ThreadingHTTPServer(("0.0.0.0", cfg.port), _Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    code = 0
    restarts: list[float] = []
    while not stop.is_set():
        for i, p in enumerate(children):
            if p.is_alive():
                continue
            now = time.monotonic()
            restarts = [t for t in restarts if now - t < _RESTART_WINDOW_SEC] + [now]
            if len(restarts) > _RESTART_LIMIT:
                log.error("자식이 %d초 안에 %d번 넘게 죽었다 — 전체를 끝낸다(supervisord 가 다시 띄운다).",
                          _RESTART_WINDOW_SEC, _RESTART_LIMIT)
                code = 1
                stop.set()
                break
            log.warning("자식 프로세스 종료 — %s(exitcode=%s). 다시 띄운다.", p.name, p.exitcode)
            children[i] = spawn(i)
        _Health.children = children
        time.sleep(1)
    for p in children:
        if p.is_alive():
            p.terminate()
    for p in children:
        p.join(timeout=30)
    server.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
