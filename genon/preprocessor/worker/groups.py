"""워커 묶음 — 감시자가 자식 프로세스를 띄우는 단위(#577).

묶음 하나 = 큐 하나 × 프로세스 N개 × 프로세스당 스레드 M개. 감시자는 묶음 목록을 받아 묶음마다 자식을 띄운다.
지금 기본값은 env(`WORKER_TASK_QUEUE`·`WORKER_PROCESSES`·`WORKER_THREADS`)로 만드는 묶음 하나다. 파싱·청킹을
한 파드 안에서 따로 늘리고 줄이는 큐 분리는 env 로 묶음을 여럿 만드는 해석을 settings 에 붙여 쓴다.

자식 프로세스(spawn)로 넘어가므로 피클할 수 있는 frozen dataclass 로 둔다. settings 가 이 모듈을 import 해도
감시자(supervisor)를 끌어오지 않도록 의존이 없는 작은 모듈이다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Group:
    name: str           # 로그 접두([worker-<name>-<번호>])와 상태 파일의 묶음 이름
    task_queue: str
    processes: int
    threads: int


def from_settings(cfg) -> list[Group]:
    """env 로 읽은 설정 → 묶음 하나(기본)."""
    return [Group(name="default", task_queue=cfg.task_queue, processes=cfg.processes, threads=cfg.threads)]
