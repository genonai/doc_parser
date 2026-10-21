"""전처리기 워커의 HTTP 앱 — 코드서빙 계약 `/health`·`/version` 만 받는다(#577).

`python -m genon.preprocessor.worker`(`worker/__main__.py`)가 이 앱을 uvicorn 한 프로세스로 띄운다. 패턴은
data-ingestion-pipeline 의 `app/launch.py` 와 같다 — uvicorn 이 HTTP 를 받고, 같은 프로세스에서 워커 감시자가 돈다.
파싱·청킹은 HTTP 가 아니라 Temporal 큐로 받는다.

    GET /health    코드서빙 startupProbe. 자식이 모두 살아 있으면 200 {"status":"ok"}, 아니면 503 {"status":"down"}
    GET /version   배포 스탬프(VERSION) · UPDATED_AT 첫 줄 · 기동 시각 — common/version_info.py
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from genon.preprocessor.src.common.version_info import get_version_info

from . import groups, registry, settings
from .supervisor import Supervisor

BASE_DIR = Path(__file__).resolve().parents[3]          # 저장소(배포본) 루트 — VERSION·UPDATED_AT 이 놓인 곳

log = logging.getLogger("genon.worker")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    cfg = settings.load()
    entries = registry.scan()
    log.info("NFS_ROOT=%s ARTIFACT_ROOT=%s 액티비티=%s", cfg.nfs_root, cfg.artifact_root,
             ", ".join(e.slug for e in entries) or "<없음>")
    sup = Supervisor(groups.from_settings(cfg))
    app.state.supervisor = sup
    sup.start()
    try:
        yield
    finally:
        sup.stop()


app = FastAPI(lifespan=_lifespan)


@app.get("/health")
@app.get("/healthcheck")
async def health():
    sup = getattr(app.state, "supervisor", None)
    ok = sup is not None and sup.healthy()
    return JSONResponse({"status": "ok" if ok else "down"}, status_code=200 if ok else 503)


@app.get("/version")
async def version():
    return get_version_info(BASE_DIR)
