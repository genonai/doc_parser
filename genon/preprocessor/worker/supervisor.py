"""워커 감시자 — 묶음마다 자식 프로세스를 띄우고 다시 띄운다(#577).

진입점 앱(`worker/app.py`)이 lifespan 에서 `Supervisor.start()` 로 배경 스레드를 띄우고, 종료 때 `stop()` 을
부른다. 동작은 #577 전의 `worker/__main__.py` 감시 루프와 같다.

- 자식은 spawn 으로 띄운다. fork 하면 uvicorn 이벤트 루프 상태를 물려받는다.
- 자식이 죽으면 그 자식만 다시 띄운다(한 건만 영향). `_RESTART_WINDOW_SEC` 초 안에 `_RESTART_LIMIT` 번을 넘게
  죽으면 치명이다 — `fatal` 을 세우고 자기 프로세스에 SIGTERM 을 보낸다. uvicorn 이 내려가고 시작 명령이 1 로
  끝나면 supervisord 가 다시 띄운다.
- `OMP_NUM_THREADS` 가 env 에 없으면 자식을 띄우기 전에 "파드 CPU ÷ 모든 묶음의 프로세스 합"으로 정한다(#514).
  torch·docling 이 import 때 읽으므로 자식 env 에 미리 있어야 한다.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import signal
import threading
import time

from . import runner, settings
from .groups import Group

log = logging.getLogger("genon.worker")

_RESTART_LIMIT = 5
_RESTART_WINDOW_SEC = 300
_CHECK_INTERVAL_SEC = 1.0


def _spawn_child(group: Group, index: int):
    p = mp.get_context("spawn").Process(target=runner.child, args=(group, index),
                                        name=f"worker-{group.name}-{index}", daemon=True)
    p.start()
    return p


class Supervisor:
    def __init__(self, groups: list[Group], spawn=_spawn_child):
        if not groups:
            raise ValueError("워커 묶음이 없습니다")
        self.groups = list(groups)
        self.fatal = False
        self._spawn = spawn
        self._children: dict[tuple[str, int], object] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._restarts: list[float] = []

    # ── 수명 ──────────────────────────────────────────────────────────────
    def start(self) -> None:
        total = sum(g.processes for g in self.groups)
        if not os.environ.get("OMP_NUM_THREADS"):
            os.environ["OMP_NUM_THREADS"] = str(
                settings.omp_threads(settings.read_cpu_max(), total, os.cpu_count()))
        for g in self.groups:
            log.info("묶음 %s: task_queue=%s processes=%d threads=%d", g.name, g.task_queue, g.processes, g.threads)
        log.info("OMP_NUM_THREADS=%s (자식 프로세스 합 %d)", os.environ["OMP_NUM_THREADS"], total)
        for g in self.groups:
            for i in range(g.processes):
                self._children[(g.name, i)] = self._spawn(g, i)
        self._thread = threading.Thread(target=self._loop, name="worker-supervisor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 30) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        for p in self._children.values():
            if p.is_alive():
                p.terminate()
        for p in self._children.values():
            p.join(timeout=timeout)

    # ── 상태 ──────────────────────────────────────────────────────────────
    def status(self) -> dict:
        """묶음별 {alive, total}."""
        children = list(self._children.items())
        return {g.name: {"alive": sum(1 for (n, _), p in children if n == g.name and p.is_alive()),
                         "total": g.processes} for g in self.groups}

    def healthy(self) -> bool:
        return not self.fatal and all(s["alive"] == s["total"] for s in self.status().values())

    # ── 감시 ──────────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.check():
                    return
            except Exception:             # 재기동 중 spawn 실패 등 — 감시가 멈춘 채 부모만 남지 않게 전체를 끝낸다
                log.exception("감시자 오류 — 전체를 끝낸다(supervisord 가 다시 띄운다).")
                self._fatal()
                return
            self._stop.wait(_CHECK_INTERVAL_SEC)

    def check(self, now: float | None = None) -> bool:
        """죽은 자식을 다시 띄운다. 치명이면 False."""
        by_name = {g.name: g for g in self.groups}
        for key, p in list(self._children.items()):
            if p.is_alive():
                continue
            t = time.monotonic() if now is None else now
            self._restarts = [r for r in self._restarts if t - r < _RESTART_WINDOW_SEC] + [t]
            if len(self._restarts) > _RESTART_LIMIT:
                log.error("자식이 %d초 안에 %d번 넘게 죽었다 — 전체를 끝낸다(supervisord 가 다시 띄운다).",
                          _RESTART_WINDOW_SEC, _RESTART_LIMIT)
                self._fatal()
                return False
            log.warning("자식 프로세스 종료 — %s(exitcode=%s). 다시 띄운다.", getattr(p, "name", key), p.exitcode)
            self._children[key] = self._spawn(by_name[key[0]], key[1])
        return True

    def _fatal(self) -> None:
        self.fatal = True
        self._stop.set()
        os.kill(os.getpid(), signal.SIGTERM)
