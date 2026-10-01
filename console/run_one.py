"""GenOS 전처리 콘솔 — 단건 실행 CLI (파서 리포 · #19902).

코드스페이스 러너(`POST /runner/run`)가 **워킹 클론의 편집 중 소스**로 액티비티 1개를 실행할 때 부른다.
서버(`main.py`)·워커 코드는 건드리지 않고, 서버와 같은 디스패처(`main._console_processor`)로 프로세서를 고른다.

    python console/run_one.py parse <parser_id>  --file <path> [--params '<json>']
    python console/run_one.py chunk <chunker_id> --file <path> [--params '<json>']   # 원형 파싱 → 청킹
    (--stdin: 위 인자를 stdin JSON {file, params} 로 받는다 — 인자 길이 제한 회피)

출력 계약: 진행 로그는 stderr, 결과는 stdout **마지막 줄** `@@RESULT@@ {"ok": true, "stage": ..., "data": ...}`.
실패는 exit 1 + `@@RESULT@@ {"ok": false, "error": ..., "error_code": ...}`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "@@RESULT@@"


def _log(msg: str) -> None:
    sys.stderr.write(f"[run_one] {msg}\n")
    sys.stderr.flush()


def _emit(payload: dict) -> None:
    sys.stdout.write(MARK + " " + json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _bootstrap():
    """서버 모듈을 그대로 임포트한다 — 프로세서·디스패처·설정 경로를 서버와 1벌로 공유하기 위해."""
    if os.name == "nt":
        # 리포는 리눅스 파드용(fcntl 파일락). Windows 개발 PC 에서는 no-op 셔임(단일 프로세스 실행에선 무의미).
        import types
        fcntl = types.ModuleType("fcntl")
        fcntl.LOCK_SH, fcntl.LOCK_EX, fcntl.LOCK_NB, fcntl.LOCK_UN = 1, 2, 4, 8
        fcntl.flock = lambda *a, **k: None
        fcntl.lockf = lambda *a, **k: None
        sys.modules["fcntl"] = fcntl
    sys.path.insert(0, os.path.join(ROOT, "genon", "preprocessor", "src"))
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    t0 = time.monotonic()
    import main  # noqa: PLC0415 — 프로세서 초기화(무거움)는 여기서 1회
    _log(f"프로세서 로드 {time.monotonic() - t0:.1f}s (cwd={ROOT})")
    return main


class _Request:
    """FastAPI Request 대용 — 프로세서는 연결 끊김 확인(is_disconnected)과 headers 정도만 본다."""
    headers: dict = {}
    client = None
    url = ""

    async def is_disconnected(self) -> bool:
        return False


def _load_args(ns) -> tuple[str, dict]:
    if ns.stdin:
        payload = json.loads(sys.stdin.read() or "{}")
        file_path = str(payload.get("file") or "")
        params = payload.get("params") or {}
    else:
        file_path = ns.file or ""
        params = json.loads(ns.params) if ns.params else {}
    if not isinstance(params, dict):
        raise ValueError("params 는 JSON 객체여야 합니다")
    if not file_path or not os.path.isfile(file_path):
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {file_path}")
    return file_path, params


async def _parse(main, activity_id: str, file_path: str, params: dict):
    params = {**params, "parser_id": activity_id}
    processor = main._console_processor("parser", params, main.parser_processor)
    _log(f"parse {activity_id} ← {file_path} params={json.dumps(params, ensure_ascii=False)}")
    return await processor(_Request(), file_path, **params)


async def _chunk(main, activity_id: str, file_path: str, params: dict):
    parsed = await _parse(main, "parser_1", file_path, {})          # 원형 파싱 → docling JSON
    n = len(parsed.get("elements") or []) if isinstance(parsed, dict) else "-"
    _log(f"기본 파싱 elements={n} → chunk {activity_id}")
    cparams = {**params, "chunker_id": activity_id, "document": parsed}
    processor = main._console_processor("chunker", cparams, main.chunking_processor)
    chunks = await processor(_Request(), file_path, **cparams)
    return [_plain(c) for c in (chunks or [])]


def _plain(obj):
    """청크(pydantic 모델 등)를 서버 응답(JSON 직렬화)과 같은 dict 로 — FastAPI 가 하던 변환을 대신한다."""
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                pass
    if isinstance(obj, dict):
        return obj
    return {"text": str(obj)}


def main_cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="run_one", description=__doc__.split("\n")[0])
    ap.add_argument("stage", choices=("parse", "chunk"))
    ap.add_argument("activity_id")
    ap.add_argument("--file", default="")
    ap.add_argument("--params", default="")
    ap.add_argument("--stdin", action="store_true", help="{file, params} 를 stdin JSON 으로 받는다")
    ns = ap.parse_args(argv)
    t0 = time.monotonic()
    try:
        file_path, params = _load_args(ns)
        main = _bootstrap()
        if ns.stage == "parse":
            data = asyncio.run(_parse(main, ns.activity_id, file_path, params))
        else:
            data = asyncio.run(_chunk(main, ns.activity_id, file_path, params))
        _log(f"OK {ns.stage} {time.monotonic() - t0:.1f}s")
        _emit({"ok": True, "stage": ns.stage, "activity_id": ns.activity_id, "file": file_path,
               "duration_ms": int((time.monotonic() - t0) * 1000), "data": data})
        return 0
    except Exception as e:  # noqa: BLE001 — 실패 사유를 결과 계약으로 그대로 올린다
        traceback.print_exc(file=sys.stderr)
        _emit({"ok": False, "stage": ns.stage, "activity_id": ns.activity_id,
               "error": f"{type(e).__name__}: {e}", "error_code": getattr(e, "error_code", None),
               "duration_ms": int((time.monotonic() - t0) * 1000)})
        return 1


if __name__ == "__main__":
    sys.exit(main_cli())
