"""계측(processing/common/profiling.py) — 켜면 단계와 모든 HTTP 호출을 재고, 동시 호출 수를 센다(#514)."""
import concurrent.futures
import http.server
import logging
import threading
import time

import httpx
import pytest
import requests
import requests.adapters

from genon.preprocessor.processing.common import profiling as prof


class _Slow(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        time.sleep(0.3)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.mark.unit
def test_profiling_logs_stage_and_counts_concurrent_http_calls(monkeypatch, caplog):
    monkeypatch.setattr(prof, "ENABLED", True)
    monkeypatch.setattr(prof, "_installed", False)
    # install() 이 전송 계층을 바꿔 끼우므로, 시험이 끝나면 원래 함수로 되돌리게 등록해 둔다
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", requests.adapters.HTTPAdapter.send)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", httpx.HTTPTransport.handle_request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request",
                        httpx.AsyncHTTPTransport.handle_async_request)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/v1/x"
    caplog.set_level(logging.INFO, logger="genon.prof")
    try:
        prof.install()
        prof.set_doc("doc a")
        with prof.stage("convert"):
            with concurrent.futures.ThreadPoolExecutor(3) as pool:
                list(pool.map(lambda _: requests.get(url, timeout=5), range(3)))
            httpx.get(url, timeout=5)
    finally:
        server.shutdown()

    calls = [r.getMessage() for r in caplog.records if "[prof] call" in r.getMessage()]
    stages = [r.getMessage() for r in caplog.records if "[prof] stage" in r.getMessage()]
    assert len(calls) == 4 and all(f"ep=127.0.0.1:{server.server_port}/v1/x status=200" in c for c in calls)
    assert sorted(int(c.split("inflight=")[1].split()[0]) for c in calls) == [1, 1, 2, 3]   # 셋이 겹치고 하나는 혼자
    assert len(stages) == 1 and "name=convert doc=doc_a wall=" in stages[0]
