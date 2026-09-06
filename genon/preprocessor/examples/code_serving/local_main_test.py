#!/usr/bin/env python
"""루트 main.py 를 그대로 띄워 /parser → /chunker 를 확인한다 (#363 08-5).

배포 형태가 루트 `main.py` 이므로(07-A 결정) 그 형태 그대로 확인하는 수단이 필요하다.
기존 예제들은 원격 서빙(게이트웨이 또는 분리 배포된 `/run`)만 호출한다.

서버를 포트에 띄우지 않고 FastAPI TestClient 로 앱을 직접 호출한다 — main.py 가 만드는
프로세서 5종과 라우팅·예외 핸들러를 전부 실제로 태운다.

    ./local_main_test.py                                   # 기본 xlsx 로 파서→청커
    ./local_main_test.py --file sample_files/drill/x1_junk_header.xlsx --doc-type menu
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

# main.py 는 common.settings 를 거치는데 그것이 MQ·MinIO 접속 정보를 **필수**로 요구하고,
# 그 값은 배포 환경의 `genon/env/.env.<profile>` 에서 온다(저장소에 없다). 로컬에서
# 앱을 올려 보려면 여기서 채워야 한다 — 로그 MQ 와 MinIO 는 이 시험에서 쓰이지 않는다.
_DUMMY_ENV = {
    # PREPROCESSOR_ID 는 **주지 않는다** — main.py 가 그 값이 있으면 MinIO 에서
    # /app/resource 로 리소스를 내려받으려 하고, 로컬에는 /app 이 없다.
    "PROFILE": "dev", "POD_ID": "local",
    # LOG_PATH 는 list[str] 이라 env 로 줄 때 JSON 배열이어야 한다.
    "LOG_PATH": json.dumps([tempfile.mkdtemp(prefix="local_main_log_")]),
    "INTERIM_ROOT": tempfile.mkdtemp(prefix="local_main_interim_"),
    "MQ_HOST": "127.0.0.1", "MQ_PORT": "5672", "MQ_USER": "guest",
    "MQ_PASSWORD": "guest", "MQ_VHOST": "/", "MQ_EXCHANGE_TYPE": "topic",
    "MQ_EXCHANGE_NAME_LOG": "log", "MQ_QUEUE_NAME_LOG": "log",
    "MQ_QUEUE_BIND_ROUTING_KEY_LOG": "log.#", "MQ_ROUTING_KEY_LOG": "log.local",
    "MINIO_ENDPOINT": "127.0.0.1:9000", "MINIO_ACCESS_KEY": "x", "MINIO_SECRET_KEY": "x",
}
for _k, _v in _DUMMY_ENV.items():
    os.environ.setdefault(_k, _v)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default="genon/preprocessor/sample_files/monimo/monimo_menu_sample.xlsx",
                    help="파싱할 원천(저장소 루트 기준 상대경로 또는 절대경로)")
    ap.add_argument("--doc-type", default="menu")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    src = Path(args.file)
    if not src.is_absolute():
        src = REPO_ROOT / src
    if not src.exists():
        print(f"[FAIL] 원천이 없습니다: {src}")
        return 1

    begin = time.time()
    from fastapi.testclient import TestClient

    import main as app_main   # 프로세서 5종이 이 import 에서 생성된다
    client = TestClient(app_main.app)
    print(f"[1/3] main.py 로드 · 프로세서 생성  ({time.time() - begin:.1f}초)")

    r = client.get("/health")
    assert r.status_code == 200 and r.json().get("status") == "ok", r.text
    print("[2/3] /health ok")

    params = {"doc_type": args.doc_type} if args.doc_type else {}
    r = client.post("/parser", json={"file_path": str(src), "params": params})
    body = r.json()
    if body.get("code") != 0:
        print(f"[FAIL] /parser 실패: {body.get('errMsg')}")
        return 1
    parsed = body["data"]
    n_el = len(parsed.get("elements") or [])
    has_doc = isinstance(parsed.get("document"), dict)
    print(f"[3/3] /parser ok · elements {n_el}건 · document {'있음' if has_doc else '없음'}")

    # 청커는 파서 산출을 params["document"] 로 인라인 받는다.
    r = client.post("/chunker", json={"file_path": str(src),
                                      "params": {**params, "document": parsed}})
    body = r.json()
    if body.get("code") != 0:
        print(f"[FAIL] /chunker 실패: {body.get('errMsg')}")
        return 1
    chunks = body["data"]
    print(f"      /chunker ok · 청크 {len(chunks)}건")
    if chunks:
        first = chunks[0]
        print(f"      첫 청크 doc_type={first.get('doc_type')!r} "
              f"n_char={first.get('n_char')} text={str(first.get('text'))[:60]!r}")

    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "parse.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"      저장: {out}")

    print(f"\n통과 · 총 {time.time() - begin:.1f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
