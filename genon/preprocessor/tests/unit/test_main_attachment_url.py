"""main.py의 presigned URL attachment 엔드포인트 단위 테스트."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient


_REPO_ROOT = Path(__file__).resolve().parents[4]
_MAIN_PATH = _REPO_ROOT / "main.py"


class _DummyProcessor:
    def __init__(self, *args, **kwargs):
        pass


def _module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def main_module(monkeypatch):
    """무거운 facade 초기화를 대체하고 루트 main.py만 격리 로드한다."""

    class _DummyLogger:
        @staticmethod
        def getLogger(name):
            return logging.getLogger(name)

    class _DummyGenosServiceException(Exception):
        error_code = "1"
        error_msg = "error"

    stubs = {
        "logger": _module("logger", Logger=_DummyLogger),
        "utils": _module("utils", make_success_response=lambda data=None: {"code": 0, "data": data}),
        "config": _module("config", cors_config=lambda app: None),
        "common.exception": _module(
            "common.exception",
            GenosServiceException=_DummyGenosServiceException,
        ),
        "common.settings": _module(
            "common.settings",
            settings=SimpleNamespace(PREPROCESSOR_ID=None),
        ),
        "util.minio_resource": _module(
            "util.minio_resource",
            download_resource_files=lambda **kwargs: None,
        ),
    }
    for facade_name in (
        "attachment_processor",
        "intelligent_processor",
        "convert_processor",
        "parser_processor",
        "chunking_processor",
    ):
        qualified_name = f"genon.preprocessor.facade.{facade_name}"
        stubs[qualified_name] = _module(qualified_name, DocumentProcessor=_DummyProcessor)

    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)

    module_name = "_test_root_main_attachment_url"
    spec = importlib.util.spec_from_file_location(module_name, _MAIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_attachment_url_runs_processor_with_temp_file_and_cleans_up(main_module, monkeypatch):
    captured = {}

    async def fake_download(presigned_url, destination):
        captured["presigned_url"] = presigned_url
        Path(destination).write_bytes(b"%PDF-downloaded-content")
        return len(b"%PDF-downloaded-content")

    async def fake_run(tag, processor, request, file_path, params, marker=None):
        path = Path(file_path)
        captured.update(
            tag=tag,
            processor=processor,
            path=path,
            parent=path.parent,
            content=path.read_bytes(),
            params=params,
        )
        return {"code": 0, "data": [{"text": "attachment chunk"}]}

    monkeypatch.setattr(main_module, "_download_presigned_file", fake_download)
    monkeypatch.setattr(main_module, "_run", fake_run)

    with TestClient(main_module.app) as client:
        response = client.post(
            "/preprocess_attachment_url",
            json={
                "presigned_url": "https://storage.example.com/signed?secret=hidden",
                "file_name": "../sample.pdf",
                "params": {"chunk_size": 1234},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"code": 0, "data": [{"text": "attachment chunk"}]}
    assert captured["presigned_url"] == "https://storage.example.com/signed?secret=hidden"
    assert captured["tag"] == "preprocess_attachment_url"
    assert captured["processor"] is main_module.attachment_processor
    assert captured["path"].name == "sample.pdf"
    assert captured["content"] == b"%PDF-downloaded-content"
    assert captured["params"] == {"chunk_size": 1234}
    assert not captured["parent"].exists()


@pytest.mark.unit
def test_attachment_url_rejects_file_name_without_extension(main_module, monkeypatch):
    download_called = False

    async def fake_download(presigned_url, destination):
        nonlocal download_called
        download_called = True

    monkeypatch.setattr(main_module, "_download_presigned_file", fake_download)

    with TestClient(main_module.app) as client:
        response = client.post(
            "/preprocess_attachment_url",
            json={
                "presigned_url": "https://storage.example.com/signed",
                "file_name": "sample",
                "params": {},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 1
    assert body["error_code"] == main_module.ERROR_CODE_INPUT
    assert body["tag"] == "preprocess_attachment_url"
    assert download_called is False


@pytest.mark.unit
def test_attachment_url_rejects_unsupported_url_scheme(main_module):
    with TestClient(main_module.app) as client:
        response = client.post(
            "/preprocess_attachment_url",
            json={
                "presigned_url": "file:///etc/passwd",
                "file_name": "sample.pdf",
                "params": {},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 1
    assert body["error_code"] == main_module.ERROR_CODE_INPUT
    assert body["tag"] == "preprocess_attachment_url"
    assert body["file_path"] == "sample.pdf"
    assert body["stage"] == "download"
    decoded = response.content.decode("utf-8")
    assert "http 또는 https URL이어야 합니다." in decoded
    assert "\\u" not in decoded


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_presigned_file_streams_to_disk(main_module, monkeypatch, tmp_path):
    original_async_client = httpx.AsyncClient
    chunks = [b"%PDF-", b"streamed-", b"content"]

    def handler(request):
        assert request.url.host == "storage.example.com"
        return httpx.Response(200, content=b"".join(chunks))

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **kwargs: original_async_client(transport=transport, **kwargs),
    )

    destination = tmp_path / "sample.pdf"
    downloaded_bytes = await main_module._download_presigned_file(
        "https://storage.example.com/signed?secret=hidden",
        str(destination),
    )

    assert downloaded_bytes == len(b"".join(chunks))
    assert destination.read_bytes() == b"".join(chunks)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_presigned_file_rejects_declared_oversize(
        main_module, monkeypatch, tmp_path):
    original_async_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(
            200,
            headers={"content-length": "11"},
            content=b"hello world",
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **kwargs: original_async_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(main_module, "_PRESIGNED_DOWNLOAD_MAX_BYTES", 10)

    destination = tmp_path / "oversize.pdf"
    with pytest.raises(ValueError, match="다운로드 제한"):
        await main_module._download_presigned_file(
            "https://storage.example.com/signed",
            str(destination),
        )

    assert not destination.exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_total_timeout_includes_semaphore_wait(
        main_module, monkeypatch, tmp_path):
    blocker = asyncio.Event()
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    async def fake_download(presigned_url, destination):
        await blocker.wait()

    monkeypatch.setattr(main_module, "_download_presigned_file", fake_download)
    monkeypatch.setattr(main_module, "_PRESIGNED_DOWNLOAD_SEMAPHORE", semaphore)
    monkeypatch.setattr(
        main_module,
        "_PRESIGNED_DOWNLOAD_TOTAL_TIMEOUT_SECONDS",
        0.01,
    )

    with pytest.raises(
            main_module.PresignedDownloadTimeout,
            match="전체 다운로드 제한 시간",
    ):
        await main_module._download_presigned_file_with_limits(
            "https://storage.example.com/signed",
            str(tmp_path / "sample.pdf"),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_concurrency_is_limited(main_module, monkeypatch, tmp_path):
    active = 0
    max_active = 0

    async def fake_download(presigned_url, destination):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.01)
            return 1
        finally:
            active -= 1

    monkeypatch.setattr(main_module, "_download_presigned_file", fake_download)
    monkeypatch.setattr(
        main_module,
        "_PRESIGNED_DOWNLOAD_SEMAPHORE",
        asyncio.Semaphore(1),
    )
    monkeypatch.setattr(
        main_module,
        "_PRESIGNED_DOWNLOAD_TOTAL_TIMEOUT_SECONDS",
        1,
    )

    results = await asyncio.gather(
        main_module._download_presigned_file_with_limits(
            "https://storage.example.com/one",
            str(tmp_path / "one.pdf"),
        ),
        main_module._download_presigned_file_with_limits(
            "https://storage.example.com/two",
            str(tmp_path / "two.pdf"),
        ),
    )

    assert results == [1, 1]
    assert max_active == 1


@pytest.mark.unit
def test_attachment_url_reports_download_timeout_in_korean(main_module, monkeypatch):
    async def fake_download(presigned_url, destination):
        raise main_module.PresignedDownloadTimeout(
            "presigned URL 전체 다운로드 제한 시간(120초)을 초과했습니다."
        )

    monkeypatch.setattr(
        main_module,
        "_download_presigned_file_with_limits",
        fake_download,
    )

    with TestClient(main_module.app) as client:
        response = client.post(
            "/preprocess_attachment_url",
            json={
                "presigned_url": "https://storage.example.com/signed",
                "file_name": "sample.pdf",
                "params": {},
            },
        )

    body = response.json()
    assert body["code"] == 1
    assert body["error_code"] == main_module.ERROR_CODE_TIMEOUT
    assert body["error_kind"] == "timeout"
    assert body["stage"] == "download"
    assert "전체 다운로드 제한 시간" in body["errMsg"]
    decoded = response.content.decode("utf-8")
    assert "전체 다운로드 제한 시간" in decoded
    assert "\\u" not in decoded


@pytest.mark.unit
def test_request_deadline_covers_download(main_module, monkeypatch):
    async def slow_download(presigned_url, destination):
        await asyncio.sleep(0.05)
        return 1

    async def unexpected_run(*args, **kwargs):
        raise AssertionError("processor must not run after request deadline")

    monkeypatch.setattr(
        main_module,
        "_download_presigned_file_with_limits",
        slow_download,
    )
    monkeypatch.setattr(main_module, "_run", unexpected_run)

    with TestClient(main_module.app) as client:
        response = client.post(
            "/preprocess_attachment_url",
            json={
                "presigned_url": "https://storage.example.com/signed",
                "file_name": "sample.pdf",
                "params": {"request_deadline": 0.01},
            },
        )

    body = response.json()
    assert body["code"] == 1
    assert body["error_code"] == main_module.ERROR_CODE_TIMEOUT
    assert body["error_kind"] == "timeout"
    assert body["stage"] == "request"
    assert "전체 요청 제한 시간(0.01초)" in body["errMsg"]
    decoded = response.content.decode("utf-8")
    assert "전체 요청 제한 시간(0.01초)" in decoded
    assert "\\u" not in decoded
