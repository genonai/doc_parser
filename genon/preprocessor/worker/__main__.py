"""전처리기 Temporal 워커 진입점 — 코드서빙 리비전의 시작 명령이다(#577).

    python -m genon.preprocessor.worker          (저장소 루트에서 — facade 와 같은 import 규칙)

uvicorn 을 한 프로세스로 띄워 `/health`·`/version`(`worker/app.py`)을 받고, 같은 프로세스에서 감시자가
자식 프로세스 WORKER_PROCESSES 개를 띄운다. 자식마다 Temporal 워커 1개 · 동시 WORKER_THREADS 건이다
(패키지 docstring 참고). 포트는 `PORT`(기본 8080, 0 이면 빈 포트).

코드서빙 규약(GenOS 템플릿): startupProbe `GET /health :8080` 이 기동 때 한 번 200 을 받아야 하고 그 뒤로는
probe 가 없다. PID1 은 supervisord 라 이 프로세스가 끝나면 시작 명령을 다시 실행한다. 그래서 자식이 짧은
시간에 너무 자주 죽으면 감시자가 전체를 내리고 1 로 끝난다.
"""
from __future__ import annotations

import logging
import sys


def main() -> int:
    import uvicorn

    from . import settings
    from .app import app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(levelname)s %(message)s")
    port = settings.load().port          # 필수 env 가 없으면 여기서 이름을 모두 적어 멈춘다
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info"))
    server.run()
    sup = getattr(app.state, "supervisor", None)
    return 0 if server.started and not (sup is not None and sup.fatal) else 1


if __name__ == "__main__":
    sys.exit(main())
