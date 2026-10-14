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


@pytest.mark.parametrize("cpu_max, processes, node_cpus, expected", [
    ("100000 100000\n", 5, 4, 1),      # SCP 측정 파드: 1 코어 ÷ 5 → 0 → 최소 1
    ("400000 100000\n", 1, 16, 4),     # 4 코어 ÷ 1
    ("800000 100000\n", 3, 32, 2),     # 8 ÷ 3 = 2.67 → 내림 2(올림 3 이면 합 9 > 8)
    ("150000 100000\n", 1, 4, 1),      # 소수 코어 1.5 → 1
    ("max 100000\n", 2, 8, 4),         # 한도 없음 → 노드 코어
    (None, 1, None, 1),                # cgroup 정보도 코어 수도 없음
], ids=["1core-5proc", "4core-1proc", "8core-3proc", "1.5core", "no-limit", "unknown"])
def test_omp_threads_floor_of_cpu_limit_per_process(cpu_max, processes, node_cpus, expected):
    """연산 스레드 = 파드 CPU 한도 ÷ 프로세스 수의 내림, 최소 1(#514). 한도는 노드 코어가 아니라 cgroup 에서 읽는다."""
    assert settings.omp_threads(cpu_max, processes, node_cpus) == expected


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


@pytest.mark.parametrize("release_after", [0.1, 0.5], ids=["유예 안에 끝남", "유예 뒤에 끝남"])
def test_stuck_processing_rejects_new_work_and_restarts_after_running_work(cfg, monkeypatch, release_after):
    """마감을 넘겨 처리 스레드가 남으면 새 작업은 재시도로 돌려보내고, 같은 프로세스가 받아 둔 다른 작업
    (프로세서 준비 중인 것까지)이 끝나고 **그 뒤로** 보고 유예(0.3)가 지난 다음 프로세스를 끝낸다(WORKER_THREADS>1).
    다른 작업이 유예 안에 끝나든 뒤에 끝나든 같다."""
    import threading
    import time

    exits = []
    monkeypatch.setattr(runtime.os, "_exit", exits.append)
    monkeypatch.setattr(runtime, "_EXIT_DELAY_SEC", 0.3)
    monkeypatch.setattr(runtime, "_IDLE_POLL_SEC", 0.01)
    monkeypatch.setattr(runtime, "_POISONED", False)
    unstick, model_loaded, other_started = threading.Event(), threading.Event(), threading.Event()

    class _Stuck:
        async def __call__(self, request, file_path, **kwargs):
            unstick.wait(5)                     # 동기 파싱처럼 루프를 막는다
            return {"document": {"texts": []}}

    class _Quick:
        async def __call__(self, request, file_path, **kwargs):
            return {"document": {"texts": []}}

    def processor(entry):                       # 다른 slug 의 첫 호출 — 모델 적재가 오래 걸린다
        if entry.slug == "parse_o":
            other_started.set()
            model_loaded.wait(5)
            proc = _Quick()
        else:
            proc = _Stuck()
        proc.run_activity = lambda arg: runtime.execute(proc, "parse", arg)
        return proc
    monkeypatch.setattr(registry, "processor", processor)
    stuck_fn, other_fn = registry.build([registry.Entry("parse", "parse", None), registry.Entry("parse_o", "parse", None)])
    arg = {"read_ref": {"root": "nfs", "path": "raw/a.pdf"}}

    other = threading.Thread(target=ActivityEnvironment().run, args=(other_fn, {**arg, "output_ref": "k1/o"}))
    other.start()
    assert other_started.wait(5)
    env = ActivityEnvironment()
    with pytest.raises(Exception) as stuck:
        env.run(stuck_fn, {**arg, "output_ref": "k1/p", "params": {"request_deadline": 0.2}})
    with pytest.raises(Exception) as nxt:
        env.run(stuck_fn, {**arg, "output_ref": "k1/q"})
    time.sleep(release_after)                   # 준비 중인 작업이 있는 동안은 끝내지 않는다
    exits_while_preparing = list(exits)
    model_loaded.set()
    other.join(5)
    finished = time.monotonic()
    for _ in range(300):
        if exits:
            break
        time.sleep(0.01)
    unstick.set()
    assert stuck.value.type == "timeout"
    assert (nxt.value.type, nxt.value.non_retryable) == ("worker_restarting", False)
    assert (exits_while_preparing, exits) == ([], [runtime.EXIT_RESTART])
    assert time.monotonic() - finished >= 0.25  # 마지막 작업의 결과가 보고될 유예 뒤에 끝낸다


def test_first_processor_is_created_once_under_concurrent_calls(monkeypatch):
    """첫 호출이 여러 액티비티 스레드에서 동시에 와도 slug 의 프로세서는 하나만 만든다(모델 이중 적재 방지)."""
    import concurrent.futures
    import time
    import types

    from genon.preprocessor.processing.core.parser import ParserCore

    made = []

    class _Slow(ParserCore):
        def __init__(self):                      # 무거운 기동(모델 적재)을 흉내 낸다
            made.append(self)
            time.sleep(0.2)

    real = registry.importlib.import_module
    monkeypatch.setattr(registry.importlib, "import_module",
                        lambda name, *a: types.SimpleNamespace(DocumentProcessor=_Slow)
                        if name.endswith(".parse_t") else real(name, *a))
    monkeypatch.setattr(registry, "_PROCESSORS", {})
    entry = registry.Entry("parse_t", "parse", None)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        procs = list(pool.map(lambda _: registry.processor(entry), range(4)))
    assert len(made) == 1 and all(p is made[0] for p in procs)


def test_processor_setup_error_is_classified(monkeypatch):
    def broken(entry):
        raise ValueError("chunking.validation.enable 값이 잘못됐다")
    monkeypatch.setattr(registry, "processor", broken)
    monkeypatch.setattr(runtime, "_POISONED", False)
    with pytest.raises(Exception) as info:
        ActivityEnvironment().run(registry.build([registry.Entry("chunk", "chunk", None)])[0], {})
    assert (info.value.type, info.value.non_retryable) == ("permanent", True)


def test_single_run_cli_uses_worker_body_and_prints_result_last(tmp_path, monkeypatch, capsys):
    """단건 CLI(activities/__main__.py) — 워커와 같은 래퍼로 돌고 stdout 마지막 줄이 @@RESULT@@ 다."""
    import importlib

    cli = importlib.import_module("genon.preprocessor.activities.__main__")
    src = tmp_path / "a.md"
    src.write_text("# 제목\n본문")
    fake = _FakeProcessor({"document": {"texts": [{"text": "제목"}, {"text": "본문"}]}})
    fake.run_activity = lambda arg: runtime.execute(fake, "parse", arg)   # ParserCore.run_activity 와 같다
    monkeypatch.setattr(registry, "processor", lambda entry: fake)
    monkeypatch.setattr(runtime, "_POISONED", False)
    monkeypatch.setattr(runtime, "_CFG", None)
    # 자식 프로세스 대신 같은 프로세스에서 본체(run)를 돌린다 — 가짜 프로세서를 쓰기 위해
    monkeypatch.setattr(cli, "_run_isolated",
                        lambda argv: {"ok": True, "data": cli.run(cli._parser().parse_args(argv))})

    code = cli.main(["run", "--activity", "parse", str(src), "--doc-type", "card",
                     "--params", '{"doc_type": "x", "keep": 1}', "-o", str(tmp_path / "out" / "p.json")])
    last = capsys.readouterr().out.strip().splitlines()[-1]
    result = json.loads(last.removeprefix("@@RESULT@@ "))

    assert code == 0 and last.startswith("@@RESULT@@ ")
    assert (result["ok"], result["stage"], result["activity"], result["data"]["count"]) == (True, "parse", "parse", 2)
    assert fake.calls[0][1] == {"doc_type": "card", "keep": 1}            # --doc-type 이 우선
    assert fake.calls[0][0].endswith("/a.md")                              # 사용자가 준 파일명 그대로
    assert json.loads((tmp_path / "out" / "p.json").read_text()) == {"texts": [{"text": "제목"}, {"text": "본문"}]}

    code = cli.main(["run", "--activity", "nope", str(src)])
    failed = json.loads(capsys.readouterr().out.strip().splitlines()[-1].removeprefix("@@RESULT@@ "))
    assert code == 1 and failed["ok"] is False and "nope" in failed["error"]["message"]

    code = cli.main(["run", "--activity", "parse"])                          # 인자 오류도 결과 한 줄
    usage = json.loads(capsys.readouterr().out.strip().splitlines()[-1].removeprefix("@@RESULT@@ "))
    assert code == 1 and usage["ok"] is False and usage["error"]["type"] == "_UsageError"
