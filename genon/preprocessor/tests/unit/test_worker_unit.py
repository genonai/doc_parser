"""전처리기 Temporal 워커(genon/preprocessor/worker) — 등록·경로·실행·오류 분류.

실제 파사드 대신 가짜 프로세서를 쓴다. 워커가 지키는 계약(어디서 읽고 어디에 쓰는가,
무엇을 프로세서에 넘기는가, 어떤 예외를 재시도하는가)만 본다.
"""
import errno
import json

import pytest
from temporalio.testing import ActivityEnvironment

from genon.preprocessor.processing.core.errors import GenosServiceException
from genon.preprocessor.worker import errors, paths, registry, runtime, settings

pytestmark = pytest.mark.unit


def test_scan_registers_files_named_by_stage_with_paired_config(tmp_path):
    acts, resource = tmp_path / "activities", tmp_path / "resource"
    acts.mkdir(), resource.mkdir()
    for name in ("parse", "parse_card", "chunk", "parsefoo", "_helper", "__main__"):
        (acts / f"{name}.py").write_text("class DocumentProcessor: ...\n")
    (acts / "legacy").mkdir()
    (acts / "legacy" / "parse_old.py").write_text("")
    (resource / "parse_card_config.yaml").write_text("{}\n")

    assert registry.scan(acts, resource) == [
        registry.Entry("chunk", "chunk", None),
        registry.Entry("parse", "parse", None),
        registry.Entry("parse_card", "parse", str(resource / "parse_card_config.yaml")),
    ]


def test_build_gives_each_slug_its_own_activity_name():
    """같은 부모 메서드를 물려받는 slug 둘이 이름 충돌 없이 함께 등록된다(SDK 1.34)."""
    from temporalio.activity import _Definition

    entries = [registry.Entry("parse", "parse", None), registry.Entry("parse_card", "parse", None)]
    names = [_Definition.must_from_callable(fn).name for fn in registry.build(entries)]
    assert names == ["parse", "parse_card"]


def test_real_activities_follow_the_rule_and_inherit_core():
    from genon.preprocessor.processing.core.chunker import ChunkerCore
    from genon.preprocessor.processing.core.parser import ParserCore

    slugs = {e.slug: e.stage for e in registry.scan()}
    assert {"parse": "parse", "chunk": "chunk"}.items() <= slugs.items()
    parse_cls = registry.importlib.import_module("genon.preprocessor.activities.parse").DocumentProcessor
    chunk_cls = registry.importlib.import_module("genon.preprocessor.activities.chunk").DocumentProcessor
    assert issubclass(parse_cls, ParserCore) and issubclass(chunk_cls, ChunkerCore)


@pytest.mark.parametrize("ref", [
    {"root": "nfs", "path": "/etc/passwd"}, {"root": "nfs", "path": "../outside.pdf"},
    {"root": "artifact", "path": "a/../../x"}, {"root": "nfs", "path": ""},
    {"root": "other", "path": "a.pdf"}, "raw/a.pdf",
])
def test_read_ref_rejects_outside_root_or_bad_shape(tmp_path, ref):
    with pytest.raises(ValueError):
        paths.read_path(str(tmp_path / "nfs"), str(tmp_path / "art"), ref)


class _FakeProcessor:
    def __init__(self, result, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    async def __call__(self, request, file_path, **kwargs):
        self.calls.append((file_path, kwargs))
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "nfs" / "raw").mkdir(parents=True)
    (tmp_path / "nfs" / "raw" / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "art" / "k1").mkdir(parents=True)
    (tmp_path / "art" / "k1" / "fixed.json").write_text("{}")
    c = settings.load({"TEMPORAL_HOST": "t", "WORKER_TASK_QUEUE": "q", "HEARTBEAT_INTERVAL": "0.05",
                       "NFS_ROOT": str(tmp_path / "nfs"), "ARTIFACT_ROOT": str(tmp_path / "art"),
                       "LLM_CACHE_ROOT": str(tmp_path / "cache")})
    runtime.configure(c)
    return c


def test_parse_then_chunk_exchange_refs_on_artifact_root(cfg):
    document = {"texts": [{"text": "카드 개요"}], "text": "카드 개요"}
    parser = _FakeProcessor({"document": document})
    chunker = _FakeProcessor([{"text": "카드 개요", "i_chunk_on_doc": 0}])
    env = ActivityEnvironment()

    parsed = env.run(runtime.execute, parser, "parse",
                     {"read_ref": {"root": "nfs", "path": "raw/a.pdf"}, "output_ref": "k1/parse",
                      "params": {"doc_type": "card", "interim_root": "/other-pod/cache",
                                 "llm_cache": True, "workflow_id": "wf-1"}})
    chunked = env.run(runtime.execute, chunker, "chunk",
                      {"parsed_ref": "k1/parse", "output_ref": "k1/chunk", "params": {"doc_type": "card"}})

    assert parser.calls == [(f"{cfg.nfs_root}/raw/a.pdf",
                             {"doc_type": "card", "llm_cache": True, "workflow_id": "wf-1",
                              "interim_root": cfg.llm_cache_root})]          # 캐시 루트는 이 파드 env
    assert chunker.calls == [("", {"doc_type": "card", "document": document})]  # 현 /chunker 호출과 같다
    assert paths.load_json(cfg.artifact_root, "k1/parse") == document
    assert json.loads(paths.artifact_path(cfg.artifact_root, "k1/chunk").read_text()) \
        == [{"text": "카드 개요", "i_chunk_on_doc": 0}]
    assert (parsed["ref"], parsed["count"], chunked["ref"], chunked["count"]) == ("k1/parse", 1, "k1/chunk", 1)
    assert parsed["preview"] == "카드 개요" and parsed["summary"].startswith("파싱 ")
    assert chunked["summary"].startswith("청크 1건")


def test_parse_reads_repaired_copy_from_artifact_root(cfg):
    parser = _FakeProcessor({"document": {"texts": []}})
    ActivityEnvironment().run(runtime.execute, parser, "parse",
                              {"read_ref": {"root": "artifact", "path": "k1/fixed.json"}, "output_ref": "k1/parse"})
    assert parser.calls[0][0] == f"{cfg.artifact_root}/k1/fixed.json"


def test_cache_flag_is_dropped_without_cache_root(cfg):
    runtime.configure(settings.load({"TEMPORAL_HOST": "t", "WORKER_TASK_QUEUE": "q",
                                     "NFS_ROOT": cfg.nfs_root, "ARTIFACT_ROOT": cfg.artifact_root}))
    parser = _FakeProcessor({"document": {"texts": []}})
    ActivityEnvironment().run(runtime.execute, parser, "parse",
                              {"read_ref": {"root": "nfs", "path": "raw/a.pdf"}, "output_ref": "k1/p",
                               "params": {"llm_cache": True}})
    assert parser.calls[0][1] == {}


def test_processor_timeout_surfaces_as_retryable_timeout_not_a_hang(cfg):
    """처리 본체가 던진 TimeoutError 를 하트비트 대기의 시간 초과로 오인하면 무한 루프가 된다."""
    parser = _FakeProcessor(None, exc=TimeoutError("deadline"))
    with pytest.raises(Exception) as info:
        ActivityEnvironment().run(runtime.execute, parser, "parse",
                                  {"read_ref": {"root": "nfs", "path": "raw/a.pdf"}, "output_ref": "k1/p"})
    assert (info.value.type, info.value.non_retryable) == ("timeout", False)


def _os_error(code, cls=OSError):
    return cls(code, "x")


@pytest.mark.parametrize("exc, non_retryable, kind", [
    (GenosServiceException("E1", "bad", error_type="permanent"), True, "permanent"),
    (GenosServiceException("E2", "llm 503", error_type="transient"), False, "transient"),
    (FileNotFoundError("missing"), True, "permanent"),
    (TimeoutError(), False, "timeout"),
    (RuntimeError("unknown"), False, "RuntimeError"),
    (_os_error(errno.ESTALE), False, "transient"),
    (_os_error(errno.EACCES, PermissionError), True, "permanent"),
])
def test_error_classification(exc, non_retryable, kind):
    err = errors.to_application_error(exc, "parse_x a.pdf")
    assert (err.non_retryable, err.type) == (non_retryable, kind)


@pytest.mark.parametrize("cause, non_retryable", [(_os_error(errno.EIO), False), (ValueError("bad json"), True)])
def test_wrapped_error_without_type_follows_its_cause(cause, non_retryable):
    """core 가 error_type 없이 감싼 입력 오류는 원인으로 가른다 — NFS 순단은 재시도."""
    try:
        try:
            raise cause
        except Exception as e:
            raise GenosServiceException("1", "JSON 파일을 읽을 수 없습니다") from e
    except GenosServiceException as wrapped:
        assert errors.to_application_error(wrapped).non_retryable is non_retryable


def test_stuck_processing_poisons_the_process_and_rejects_next_work(cfg, monkeypatch):
    """마감을 넘겨 처리 스레드가 남으면 재기동을 예약하고, 그동안 온 작업은 재시도로 돌려보낸다."""
    import threading

    timers = []
    monkeypatch.setattr(runtime.threading, "Timer", lambda *a: timers.append(a) or _NoTimer())
    monkeypatch.setattr(runtime, "_POISONED", False)
    release = threading.Event()

    class _Stuck:
        async def __call__(self, request, file_path, **kwargs):
            release.wait(5)                     # 동기 파싱처럼 루프를 막는다
            return {"document": {"texts": []}}

    env = ActivityEnvironment()
    with pytest.raises(Exception) as info:
        env.run(runtime.execute, _Stuck(), "parse",
                {"read_ref": {"root": "nfs", "path": "raw/a.pdf"}, "output_ref": "k1/p",
                 "params": {"request_deadline": 0.2}})
    release.set()
    assert info.value.type == "timeout" and timers and timers[0][1:] == (runtime.os._exit, (runtime.EXIT_RESTART,))
    with pytest.raises(Exception) as nxt:
        env.run(registry.build([registry.Entry("parse", "parse", None)])[0], {})
    assert (nxt.value.type, nxt.value.non_retryable) == ("worker_restarting", False)


class _NoTimer:
    daemon = False

    def start(self):
        pass


def test_processor_setup_error_is_classified(monkeypatch):
    def broken(entry):
        raise ValueError("chunking.validation.enable 값이 잘못됐다")
    monkeypatch.setattr(registry, "processor", broken)
    monkeypatch.setattr(runtime, "_POISONED", False)
    with pytest.raises(Exception) as info:
        ActivityEnvironment().run(registry.build([registry.Entry("chunk", "chunk", None)])[0], {})
    assert (info.value.type, info.value.non_retryable) == ("permanent", True)
