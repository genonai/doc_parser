"""전처리기 Temporal 워커(genon/preprocessor/worker) — 등록·경로·실행·오류 분류.

실제 액티비티 처리기 대신 가짜 프로세서를 쓴다. 워커가 지키는 계약(어디서 읽고 어디에 쓰는가,
무엇을 프로세서에 넘기는가, 어떤 예외를 재시도하는가)만 본다.
"""
import dataclasses
import errno
import json
import logging
import os
from pathlib import Path

import pytest
import yaml
from temporalio.testing import ActivityEnvironment

from genon.preprocessor.processing.common.reason_codes import CodedValueError
from genon.preprocessor.processing.core.errors import GenosServiceException
from genon.preprocessor.worker import errors, paths, registry, runtime, settings

pytestmark = pytest.mark.unit


def test_scan_registers_folders_named_by_stage_with_config(tmp_path, monkeypatch):
    """폴더 이름 = slug, 첫 단어 = 단계, processor.py 가 있어야 액티비티. config.yaml 은 필수(#495)."""
    monkeypatch.delenv("GENOS_RESOURCE_DIR", raising=False)
    acts = tmp_path / "activities"
    for name, files in (("parse", ("processor.py", "config.yaml")), ("parse_card", ("processor.py", "config.yaml")),
                        ("chunk", ("processor.py", "config.yaml")), ("parsefoo", ("processor.py", "config.yaml")),
                        ("_helper", ("processor.py", "config.yaml")), ("legacy", ("config.yaml",))):
        (acts / name).mkdir(parents=True)
        for f in files:
            (acts / name / f).write_text("")
    (acts / "stray.py").write_text("")                      # 평평한 파일은 더 이상 액티비티가 아니다

    assert registry.scan(acts) == [
        registry.Entry("chunk", "chunk", str(acts / "chunk" / "config.yaml")),
        registry.Entry("parse", "parse", str(acts / "parse" / "config.yaml")),
        registry.Entry("parse_card", "parse", str(acts / "parse_card" / "config.yaml")),
    ]
    (acts / "parse_card" / "config.yaml").unlink()
    with pytest.raises(ValueError, match="parse_card"):
        registry.scan(acts)


def test_site_folder_overrides_activity_config(tmp_path, monkeypatch, caplog):
    """사이트 완성본(GENOS_RESOURCE_DIR 옆 activities/<slug>/)이 그 slug 의 설정 폴더가 된다(#497) — 코드는 저장소 것.
    기동 로그는 slug 마다 설정 경로·출처를 적고(#520), 사이트에 activities/ 가 없으면 워커는 뜨되 호출이 오류다(#522).
    사이트에 그 slug 폴더만 없어도 저장소로 떨어지지 않고 그 slug 호출이 영구 오류다(#561)."""
    import logging

    from temporalio.exceptions import ApplicationError

    acts, site = tmp_path / "activities", tmp_path / "site"
    for name in ("parse", "chunk"):
        (acts / name).mkdir(parents=True)
        (acts / name / "processor.py").write_text("")
        (acts / name / "config.yaml").write_text("")
    (site / "resource").mkdir(parents=True)
    monkeypatch.setenv("GENOS_RESOURCE_DIR", str(site / "resource"))
    with caplog.at_level(logging.INFO, logger="genon.worker"):
        entries = registry.scan(acts)
    assert [e.config_path for e in entries] == [str(site / "activities" / "chunk" / "config.yaml"),
                                                str(site / "activities" / "parse" / "config.yaml")]
    assert not any(r.levelno == logging.WARNING for r in caplog.records)         # 경고는 보는 사람이 없다 — 호출 오류로
    with pytest.raises(ApplicationError) as info:                                # 호출한 쪽이 결함을 그대로 본다
        ActivityEnvironment().run(registry.build(entries)[0], {})
    assert str(site / "activities") in str(info.value) and "activities/ 가 없습니다" in str(info.value)
    caplog.clear()
    (site / "activities" / "parse").mkdir(parents=True)
    (site / "activities" / "parse" / "config.yaml").write_text("")
    with caplog.at_level(logging.INFO, logger="genon.worker"):
        entries = registry.scan(acts)
    chunk, parse = entries
    assert (chunk.config_path, parse.config_path) == (str(site / "activities" / "chunk" / "config.yaml"),
                                                      str(site / "activities" / "parse" / "config.yaml"))
    assert parse.problem is None and "저장소 폴더로 떨어지지 않습니다" in chunk.problem
    with pytest.raises(ApplicationError) as info:                                # 저장소 chunk 설정으로 돌지 않는다
        ActivityEnvironment().run(registry.build([chunk])[0], {})
    assert info.value.non_retryable and "'chunk' 폴더가 없습니다" in str(info.value)
    lines = [r.getMessage() for r in caplog.records]
    assert any("액티비티 parse:" in ln and "사이트 완성본" in ln and "오류" not in ln for ln in lines)
    assert any(ln.startswith("액티비티 chunk [호출마다 오류") for ln in lines)   # 결함 표시는 경로 앞(220자에서 잘리지 않게)


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
    if (Path(__file__).resolve().parents[3] / "genon" / "sites").is_dir():     # 원본 저장소 — 원형만 둔다(#496)
        assert slugs == {"parse": "parse", "chunk": "chunk"}, "고객 slug 는 고객 저장소(서빙본 작업 브랜치)에서 만든다"
    else:                                                                       # 서빙본·고객 저장소 — 사본이 더 있을 수 있다
        assert {"parse": "parse", "chunk": "chunk"}.items() <= slugs.items()
    parse_cls = registry.importlib.import_module("genon.preprocessor.activities.parse.processor").DocumentProcessor
    chunk_cls = registry.importlib.import_module("genon.preprocessor.activities.chunk.processor").DocumentProcessor
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
    assert chunked["summary"].startswith("청크 1건") and "chunk_validation" not in chunked


def test_chunk_result_carries_validation_summary_from_request_state(cfg):
    """청커(ChunkerCore)가 요청 상태에 올린 청크 검증 요약을 HTTP 성공 봉투와 같은 키로 싣는다(#589).
    걸린 것이 없을 때 키가 붙지 않는 것은 위 교환 시험이 본다."""
    summary = {"action": "report", "count": 1, "reasons": {"too_short": 1}, "items": []}

    class _Chunker(_FakeProcessor):
        async def __call__(self, request, file_path, **kwargs):
            request.state.chunk_validation = summary
            return self.result

    result = ActivityEnvironment().run(runtime.execute, _Chunker([{"text": "a"}]), "chunk",
                                       {"parsed_ref": "k1/fixed", "output_ref": "k1/chunk"})
    assert result["chunk_validation"] == summary


class _Lines(logging.Handler):
    """배치 워커와 같은 형식(`%(doc_tag)s%(message)s`)으로 찍힌 줄을 모은다."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []
        self.addFilter(runtime.DocTagFilter())
        self.setFormatter(logging.Formatter("%(doc_tag)s%(message)s"))

    def emit(self, record):
        self.lines.append(self.format(record))


def _env(slug: str) -> ActivityEnvironment:
    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, activity_type=slug)
    return env


def test_batch_log_lines_carry_document_tag(cfg):
    """배치 워커 로그는 parse·chunk 의 여러 문서가 섞인다 — 처리 스레드에서 찍은 줄과 끝 줄에 `[단계 파일명]`
    이 붙고, 문서 처리가 끝난 뒤의 줄에는 붙지 않는다."""
    class _Logging(_FakeProcessor):
        async def __call__(self, request, file_path, **kwargs):
            logging.getLogger("genon.preprocessor.processing.core.parser").info("표 3개를 읽었습니다")
            return self.result

    lines, root = _Lines(), logging.getLogger()
    root.addHandler(lines)
    old_level = root.level
    root.setLevel(logging.INFO)
    try:
        _env("parse_q1").run(runtime.execute, _Logging({"document": {"texts": []}}), "parse",
                             {"read_ref": {"root": "nfs", "path": "raw/a.pdf"}, "output_ref": "k1/parse",
                              "source_name": "/nfs/raw/a.pdf"})
        logging.getLogger("genon.worker").info("다음 작업을 기다립니다")
    finally:
        root.removeHandler(lines)
        root.setLevel(old_level)
    assert "[PARSE a.pdf] 표 3개를 읽었습니다" in lines.lines                # 처리 스레드의 줄 — 복제본도 단계만
    assert any(ln.startswith("[PARSE a.pdf] 처리 완료: 파싱 ") for ln in lines.lines)
    assert lines.lines[-1] == "다음 작업을 기다립니다"


@pytest.mark.parametrize("stage, doc, expected", [
    ("parse", "/nfs/raw/a.pdf", "PARSE a.pdf"),
    ("chunk", "", "CHUNK"),
], ids=["단계 대문자·파일명만", "파일 없음 — 단계만"])
def test_doc_tag_is_upper_stage_and_file_name(stage, doc, expected):
    """배치 로그 줄 앞 표시는 `[단계 파일명]` 이다 — pipeline 워커(`[EMBED a.pdf]`·`[PREPARE]`)와 같은 형식."""
    assert runtime.doc_tag(stage, doc) == expected


@pytest.mark.parametrize("exc, expected", [
    (GenosServiceException("1", "doc_type=x 에 맞는 등록이 없습니다.\n  설정을 고치세요.", error_type="permanent"),
     "doc_type=x 에 맞는 등록이 없습니다. 설정을 고치세요. (액티비티 parse_q1 · 파일 a.pdf)"),
    (KeyError("title"), "KeyError: 'title' (액티비티 parse_q1 · 파일 a.pdf)"),
], ids=["처리기 예외 — 클래스 이름 없음·한 줄", "그 밖의 예외 — 이름이 뜻을 보탠다"])
def test_failure_message_puts_reason_first(cfg, exc, expected):
    """실패 메시지는 사유가 맨 앞이고 한 줄이다 — Studio 콘솔(220자)과 GenOS 적재 상태(error_message)가 앞부분만
    보인다. 어디서(액티비티·파일 이름)는 뒤 괄호. 예전엔 `parse_q1 /nfs/raw/a.pdf: GenosServiceException: …` 이었다."""
    with pytest.raises(Exception) as info:
        _env("parse_q1").run(runtime.execute, _FakeProcessor(None, exc), "parse",
                             {"read_ref": {"root": "nfs", "path": "raw/a.pdf"}, "output_ref": "k1/parse",
                              "source_name": "/nfs/raw/a.pdf"})
    assert info.value.message == expected


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
    (yaml.YAMLError("다른 로더가 감싸지 않고 올린 문법 오류"), True, "permanent"),
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


def _caused_by(exc, cause):
    exc.__cause__ = cause
    return exc


@pytest.mark.parametrize("exc, expected", [
    (_caused_by(GenosServiceException("1", "JSON 파일을 읽을 수 없습니다"), CodedValueError("손상", "CHUNK_P004")),
     {"reason_code": "CHUNK_P004", "error_category": "preprocess", "error_kind": "permanent"}),
    (RuntimeError("unknown"), {"reason_code": None, "error_category": None, "error_kind": "RuntimeError"}),
], ids=["원인이 코드를 실음", "코드 없음"])
def test_application_error_details_carry_reason_code(exc, expected):
    """사유 코드(#528)는 details[0] 로 실린다(#589) — 원인 체인에서 찾고, 없으면 None(지어내지 않는다).
    error_kind 는 type 과 같다. 겉 예외가 직접 코드를 실은 경우는 아래 단건 CLI 시험의 실패 봉투가 본다."""
    err = errors.to_application_error(exc)
    assert (list(err.details), err.type) == ([expected], expected["error_kind"])


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
        def __init__(self, config_path=None):    # 무거운 기동(모델 적재)을 흉내 낸다
            made.append(self)
            time.sleep(0.2)

    real = registry.importlib.import_module
    monkeypatch.setattr(registry.importlib, "import_module",
                        lambda name, *a: types.SimpleNamespace(DocumentProcessor=_Slow)
                        if name.endswith(".parse_t.processor") else real(name, *a))
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


def test_config_yaml_syntax_error_is_permanent_one_line(tmp_path, monkeypatch):
    """config.yaml 문법 오류는 같은 설정이면 늘 실패한다 — 재시도하지 않고, 파일·행·열과 조치를 한 줄로 앞에 적는다.
    Studio 콘솔(줄당 220자)·GenOS error_message(약 500자)에서 잘려도 고칠 자리가 보여야 한다."""
    import types

    from genon.preprocessor.processing.common import config_parse
    from genon.preprocessor.processing.core.parser import ParserCore

    class _Reads(ParserCore):
        def __init__(self, config_path=None):    # 실제 프로세서처럼 기동 때 설정을 읽는다
            config_parse.load_config(config_path)

    cfg = tmp_path / "activities" / "parse_t" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("defaults:\n  log_level: 4\n  doc_types: [card, memo\nchunk: {}\n", encoding="utf-8")
    real = registry.importlib.import_module
    monkeypatch.setattr(registry.importlib, "import_module",
                        lambda name, *a: types.SimpleNamespace(DocumentProcessor=_Reads)
                        if name.endswith(".parse_t.processor") else real(name, *a))
    monkeypatch.setattr(registry, "_PROCESSORS", {})
    monkeypatch.setattr(runtime, "_POISONED", False)
    monkeypatch.setattr(config_parse, "_PREPROCESSOR_DIR", tmp_path.resolve())   # 저장소 배치처럼 경로를 줄인다

    with pytest.raises(Exception) as info:
        ActivityEnvironment().run(registry.build([registry.Entry("parse_t", "parse", str(cfg))])[0], {})
    err, message = info.value, info.value.message
    assert (err.type, err.non_retryable) == ("permanent", True)
    assert "\n" not in message
    head = message[:130]                                            # 핵심은 앞 130자 안(#632)
    assert "activities/parse_t/config.yaml 4행 6열의 YAML 문법이 틀렸습니다" in head
    assert "). 괄호·들여쓰기·따옴표를 고치세요." in message          # 접두·접미(액티비티·파일)는 to_application_error 몫


def test_single_run_cli_uses_worker_body_and_prints_result_last(tmp_path, monkeypatch, capsys, caplog):
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
    caplog.set_level(logging.INFO)

    code = cli.main(["run", "--activity", "parse", "--input", str(src), "--doc-type", "card",   # <file> 과 같다
                     "--params", '{"doc_type": "x", "keep": 1}', "-o", str(tmp_path / "out" / "p.json")])
    last = capsys.readouterr().out.strip().splitlines()[-1]
    result = json.loads(last.removeprefix("@@RESULT@@ "))

    assert code == 0 and last.startswith("@@RESULT@@ ")
    assert (result["ok"], result["stage"], result["activity"], result["data"]["count"]) == (True, "parse", "parse", 2)
    assert fake.calls[0][1] == {"doc_type": "card", "keep": 1}            # --doc-type 이 우선
    assert fake.calls[0][0].endswith("/a.md")                              # 사용자가 준 파일명 그대로
    assert json.loads((tmp_path / "out" / "p.json").read_text()) == {"texts": [{"text": "제목"}, {"text": "본문"}]}
    # 액티비티마다의 설정 경로 줄은 워커 기동용이다 — 단건은 시작 줄에 이번 slug 의 설정만 찍는다
    assert not [r for r in caplog.records if r.levelno >= logging.INFO and ": 설정 " in r.getMessage()]

    code = cli.main(["run", "--activity", "nope", str(src)])
    failed = json.loads(capsys.readouterr().out.strip().splitlines()[-1].removeprefix("@@RESULT@@ "))
    assert code == 1 and failed["ok"] is False and "nope" in failed["error"]["message"]
    assert "reason_code" not in failed["error"]                              # 본체 밖의 실패에는 사유 코드가 없다

    fake.exc = GenosServiceException("1", "필수 열 부재", error_type="permanent", reason_code="PARSE_D001")
    code = cli.main(["run", "--activity", "parse", str(src)])                 # 본체 실패 — 워커와 같은 details
    coded = json.loads(capsys.readouterr().out.strip().splitlines()[-1].removeprefix("@@RESULT@@ "))
    # message 에 type 접두("permanent: ")가 붙지 않는다 — type 은 따로 싣는다
    assert code == 1 and coded["error"].pop("message") == "필수 열 부재 (액티비티 parse · 파일 a.md)"
    # Studio 콘솔은 실패 때 stderr 꼬리 6줄만 보인다 — 사유가 stderr 마지막 줄에 있어야 한다
    failed_lines = [r.getMessage() for r in caplog.records if r.name == "genon.activities" and r.levelname == "ERROR"]
    assert failed_lines[-1] == "단건 실행 실패(PARSE_D001): 필수 열 부재 (액티비티 parse · 파일 a.md)"
    assert coded["error"] == {"type": "permanent", "non_retryable": True, "reason_code": "PARSE_D001",
                              "error_category": "data", "error_kind": "permanent"}

    code = cli.main(["run", "--activity", "parse"])                          # 인자 오류도 결과 한 줄
    usage = json.loads(capsys.readouterr().out.strip().splitlines()[-1].removeprefix("@@RESULT@@ "))
    assert code == 1 and usage["ok"] is False and usage["error"]["type"] == "_UsageError"


@pytest.mark.parametrize("codespace, shell_value, expected", [
    ("cs-19", None, "/workspace/.studio/model_presets.yaml"),   # 코드스페이스 — 개발 env 파일의 값을 쓴다
    ("cs-19", "/mine.yaml", "/mine.yaml"),                       # 이미 설정된 env 는 덮지 않는다
    (None, None, None),                                          # 코드스페이스가 아니면 읽지 않는다
])
def test_single_run_cli_uses_codespace_dev_env_presets(tmp_path, monkeypatch, caplog, codespace, shell_value, expected):
    """콘솔이 부르는 단건 CLI 가 개발 워커(Job)와 같은 GENOS_MODEL_PRESETS_FILE 로 자식 프로세스를 띄운다."""
    import importlib

    cli = importlib.import_module("genon.preprocessor.activities.__main__")
    dev_env = tmp_path / "dev.env"
    dev_env.write_text("# 개발 워커 env\nNFS_ROOT=/workspace\n"
                       "export GENOS_MODEL_PRESETS_FILE='/workspace/.studio/model_presets.yaml'\n", encoding="utf-8")
    monkeypatch.setenv("STUDIO_DEV_ENV", str(dev_env))
    for name, value in (("CODESPACE_ID", codespace), ("GENOS_MODEL_PRESETS_FILE", shell_value)):
        monkeypatch.setenv(name, value or "")          # 되돌림을 기록해 둔다 — CLI 가 os.environ 에 직접 쓴다
        if value is None:
            monkeypatch.delenv(name)
    seen = {}

    def isolated(argv):
        seen["presets"] = os.environ.get("GENOS_MODEL_PRESETS_FILE")
        return {"ok": True, "data": {"stage": "parse"}}

    monkeypatch.setattr(cli, "_run_isolated", isolated)
    caplog.set_level(logging.INFO, logger="genon.activities")
    assert cli.main(["run", "--activity", "parse", str(tmp_path / "a.md")]) == 0
    assert seen["presets"] == expected
    notes = [r.getMessage() for r in caplog.records if "개발 env" in r.getMessage()]
    assert len(notes) == (1 if codespace and not shell_value else 0)
    assert all("model_presets.yaml" not in n and "NFS_ROOT" not in n for n in notes)   # 값·다른 키는 싣지 않는다


# ── activities diff(#496) — 사본이 복사된 뒤 원형이 바뀐 것 ───────────────────────────

def _copied_repo(tmp_path):
    """임시 git 저장소: 원형 parse → 사본 parse_t 복사 커밋 → 원형 변경(수정·추가·삭제) 커밋. activities 폴더를 돌려준다."""
    import os
    import shutil
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    acts = tmp_path / "activities"
    (acts / "parse").mkdir(parents=True)
    (acts / "parse" / "processor.py").write_text("class DocumentProcessor: ...\n", encoding="utf-8")
    (acts / "parse" / "config.yaml").write_text("chunking:\n  chunk_size: 1500\n", encoding="utf-8")
    (acts / "parse" / "지울_프롬프트.md").write_text("old\n", encoding="utf-8")       # 한글 이름 — ls-tree 인용 회귀를 잡는다
    git("init", "-q"); git("add", "-A"); git("commit", "-q", "-m", "원형")
    shutil.copytree(acts / "parse", acts / "parse_t")                                # 복사(Studio 의 new)
    git("add", "-A"); git("commit", "-q", "-m", "parse_t 복사")
    (acts / "parse" / "config.yaml").write_text("chunking:\n  chunk_size: 300\n", encoding="utf-8")   # 원형이 바뀐다
    (acts / "parse" / "new.md").write_text("new\n", encoding="utf-8")
    (acts / "parse" / "지울_프롬프트.md").unlink()
    git("add", "-A"); git("commit", "-q", "-m", "원형 변경")
    return acts


def test_diff_shows_origin_changes_since_copy(tmp_path):
    from genon.preprocessor.activities.__main__ import diff_origin

    acts = _copied_repo(tmp_path)
    summary, body = diff_origin("parse_t", activities_dir=acts)
    assert {k: summary[k] for k in ("slug", "origin", "changed", "added", "removed", "unchanged")} == {
        "slug": "parse_t", "origin": "parse", "changed": ["config.yaml"], "added": ["new.md"],
        "removed": ["지울_프롬프트.md"], "unchanged": 1}
    assert summary["copied_at"]["commit"] and "-  chunk_size: 1500" in body and "+  chunk_size: 300" in body


def test_diff_reports_uncommitted_copy(tmp_path):
    import shutil

    from genon.preprocessor.activities.__main__ import diff_origin

    acts = _copied_repo(tmp_path)
    shutil.copytree(acts / "parse", acts / "parse_u")                      # 아직 커밋하지 않은 사본
    summary, body = diff_origin("parse_u", activities_dir=acts)
    assert summary["copied_at"] is None and "미커밋" in summary["note"] and body == ""


# ── activities new / remove(#472) — 생성·복제 = 폴더 복사, 삭제 = 폴더 삭제 ─────────────────────

def _activities(tmp_path, monkeypatch, site_overrides_parse=False):
    """저장소 액티비티 폴더(parse·chunk) + 선택적으로 사이트 완성본(GENOS_RESOURCE_DIR 옆 activities/parse/)."""
    acts = tmp_path / "activities"
    for slug in ("parse", "chunk"):
        (acts / slug).mkdir(parents=True)
        (acts / slug / "processor.py").write_text(f"# {slug} 원형 별명\nclass DocumentProcessor: ...\n", encoding="utf-8")
        (acts / slug / "__init__.py").write_text("", encoding="utf-8")
        (acts / slug / "config.yaml").write_text(f"who: repo-{slug}\n", encoding="utf-8")
        (acts / slug / "prompt_x.md").write_text("repo prompt\n", encoding="utf-8")
    monkeypatch.delenv("GENOS_RESOURCE_DIR", raising=False)
    if site_overrides_parse:
        site = tmp_path / "site"
        (site / "resource").mkdir(parents=True)
        (site / "activities" / "parse").mkdir(parents=True)
        (site / "activities" / "parse" / "config.yaml").write_text("who: site-parse\n", encoding="utf-8")
        (site / "activities" / "parse" / "custom_field_faq.yaml").write_text("site field\n", encoding="utf-8")
        (site / "activities" / "parse" / "plugins").mkdir()
        (site / "activities" / "parse" / "plugins" / "extract.py").write_text("def run(): ...\n", encoding="utf-8")
        monkeypatch.setenv("GENOS_RESOURCE_DIR", str(site / "resource"))
    return acts


def test_new_copies_repo_origin_folder(tmp_path, monkeypatch):
    """사본은 저장소 원본 폴더(설정·코드)에서 온다 — 사이트 설정은 배포처 동기화가 덮는다(#561). 첫 줄 별명만 바뀐다."""
    from genon.preprocessor.activities.__main__ import new_activity

    acts = _activities(tmp_path, monkeypatch, site_overrides_parse=True)
    files = new_activity("parse", "parse_1", activities_dir=acts)
    # 저장소 원본 폴더의 것 전부 — 사이트 완성본(GENOS_RESOURCE_DIR)의 설정은 저장소 사본으로 새지 않는다
    assert sorted(Path(f).name for f in files) == ["__init__.py", "config.yaml", "processor.py", "prompt_x.md"]
    assert (acts / "parse_1" / "config.yaml").read_text(encoding="utf-8") == "who: repo-parse\n"
    assert (acts / "parse_1" / "processor.py").read_text(encoding="utf-8") == \
        "# parse 원형 별명 사본\nclass DocumentProcessor: ...\n"


@pytest.mark.parametrize("origin, slug, title, needle", [
    ("parse", "parse", None, "원형 이름"), ("parse", "Parse_1", None, "규칙"), ("parse", "chunk_1", None, "단계가 다릅니다"),
    ("nope", "parse_1", None, "원본 'nope'"), ("parse", "parse_1", "두\n줄", "한 줄"),
])
def test_new_rejects_bad_requests(tmp_path, monkeypatch, origin, slug, title, needle):
    from genon.preprocessor.activities.__main__ import new_activity

    acts = _activities(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=needle):
        new_activity(origin, slug, title=title, activities_dir=acts)
    assert sorted(p.name for p in acts.iterdir()) == ["chunk", "parse"]                              # 아무것도 만들지 않았다


def test_remove_deletes_copy_but_not_prototype_or_site_overridden(tmp_path, monkeypatch):
    from genon.preprocessor.activities.__main__ import new_activity, remove_activity

    acts = _activities(tmp_path, monkeypatch, site_overrides_parse=True)
    new_activity("chunk", "chunk_1", activities_dir=acts)
    removed = remove_activity("chunk_1", activities_dir=acts)
    assert sorted(Path(f).name for f in removed) == ["__init__.py", "config.yaml", "processor.py", "prompt_x.md"]
    assert not (acts / "chunk_1").exists()
    for slug, needle in (("parse", "원형"), ("chunk_1", "없습니다")):
        with pytest.raises(ValueError, match=needle):
            remove_activity(slug, activities_dir=acts)
    new_activity("parse", "parse_s", activities_dir=acts)
    (tmp_path / "site" / "activities" / "parse_s").mkdir()                       # 사이트가 덮어쓰는 slug
    with pytest.raises(ValueError, match="사이트 완성본"):
        remove_activity("parse_s", activities_dir=acts)
    assert (acts / "parse_s").exists()


def test_health_server_serves_version_stamp(tmp_path, monkeypatch):
    """워커의 /version 은 루트 main.py 의 /version 과 같은 스탬프(VERSION·UPDATED_AT 첫 줄)를 돌려준다."""
    import http.server
    import json
    import threading
    import urllib.request

    from genon.preprocessor.worker import __main__ as entry

    (tmp_path / "VERSION").write_text(json.dumps({"source_version": "9.9.9"}), encoding="utf-8")
    (tmp_path / "UPDATED_AT").write_text("2026-11-03 반영\n둘째 줄\n", encoding="utf-8")
    monkeypatch.setattr(entry, "BASE_DIR", tmp_path)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), entry._Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/version") as resp:
            body = json.loads(resp.read())
    finally:
        server.shutdown()
    assert (body["version"], body["source"], body["manual_updated_at"]) == ("9.9.9", "file", "2026-11-03 반영")
