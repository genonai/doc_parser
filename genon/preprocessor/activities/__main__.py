"""액티비티 단건 실행 — 전처리 Studio(목업 ② 단건 테스트)와 VS Code 터미널이 같은 명령을 부른다.

    python -m genon.preprocessor.activities run --activity <slug> (<file> | --input <file>) [--doc-type …] [--params '<json>'] [-o <out>]
    python -m genon.preprocessor.activities new --from <원형 slug> --slug <새 slug> [--title <별명>]
    python -m genon.preprocessor.activities remove --slug <slug>
    python -m genon.preprocessor.activities diff <slug> [--from <원형>]

    parse  <file> = 원본 문서        → 파싱 결과 JSON(-o, 기본 ./<파일명>.<slug>.json)
    chunk  <file> = 파싱 결과 JSON   → 청크 배열 JSON

**워커와 같은 본문**으로 돈다 — Temporal 서버 없이 SDK 로컬 실행 환경(`ActivityEnvironment`)에서
워커가 등록하는 그 slug 의 래퍼(`worker/registry.build`)를 부른다. 그래서 인자 해석·설정·저장·오류
분류가 큐 왕복과 같다. 입력은 임시 루트에 **사용자가 준 파일명 그대로** 복사해 상대 참조로 넘긴다
(원본 폴더에 아무것도 쓰지 않는다).

실제 처리는 **자식 프로세스**에서 돈다. 프로세서·docling 이 stdout 에 찍는 출력은 stderr 로 가고,
마감 초과로 처리 스레드가 남아도(워커는 그 프로세스를 끝낸다) 부모가 결과 한 줄과 종료 코드를 정한다.

출력 계약(Studio 디스패처가 읽는다): stderr = 진행 로그 · stdout **마지막 줄**
`@@RESULT@@ {"ok", "stage", "activity", "duration_ms", "data" | "error"}` · 실패는 exit 1.

`new`·`remove` (#472, Studio 의 "+ 새 액티비티 · 복제"·삭제): 액티비티 하나 = 폴더 하나라 생성·복제는 **폴더 복사 한 번**,
삭제는 폴더 삭제다. 복사 원천은 저장소의 원본 폴더(설정·코드)다. Studio 는 배포처에서 일한다 — 배포처의 activities/ 에는
동기화가 사이트 설정을 이미 덮어 두었으므로 사본은 사이트 문서유형을 그대로 가진다(#561). processor.py 첫 줄(Studio 별명)만
바꾼다. slug 는 콘솔이 정한다(`parse_1` …, 규칙 ^(parse|chunk)(_[a-z0-9_]+)?$, 단계는 원형과 같아야 하고 원형·있는 이름은
거부). 삭제는 원형(parse·chunk)과 사이트 완성본이 덮어쓰는 slug 를 거부한다. 둘 다 **파일만 다룬다** — 커밋은 Studio·사용자
몫이다(작업 브랜치). 결과 `data.files` = 만든/지운 파일 경로 전부(저장소 루트 기준) — 콘솔이 이 목록을 한 커밋에 담는다.

`diff <slug>` (#496): 사본 폴더가 **복사된 뒤 원형이 얼마나 바뀌었는지** — git 이력에서 사본의 복사 커밋(폴더의
config.yaml 이 처음 들어온 커밋)을 찾아, 그때의 원형 폴더와 지금의 원형 폴더를 파일별 unified diff 로 stdout 에
찍는다(사본에 가져올 변경을 사람이 고른다 — 자동 동기화는 하지 않는다). 원형은 --from, 없으면 단계 원형(parse·chunk).
복사 커밋이 없으면(미커밋 사본) 그렇다고 알린다. 마지막 줄은 같은 결과 계약 — data = {slug, origin, copied_at,
changed, added, removed, unchanged}.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RESULT_PREFIX = "@@RESULT@@"


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):          # 인자 오류도 결과 계약(마지막 줄 + exit 1)으로 알린다
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -m genon.preprocessor.activities",
                description="전처리기 액티비티 단건 실행(워커와 같은 본문, Temporal 미경유)")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)
    run = sub.add_parser("run", help="액티비티 1개를 단건 실행한다")
    run.add_argument("--activity", required=True, help="slug(= activities/<slug>/ 폴더 이름)")
    run.add_argument("file", nargs="?", help="parse: 원본 문서 · chunk: 파싱 결과 JSON(--input 과 같다)")
    run.add_argument("--input", help="입력 파일(위치 인자 <file> 과 같다 — 두 저장소 명령 통일)")
    run.add_argument("--doc-type", help="문서 유형(params.doc_type 보다 우선)")
    run.add_argument("--params", default="{}", help="실행 파라미터 JSON(Studio 층②)")
    run.add_argument("-o", "--output", help="결과 JSON 파일 경로(기본 ./<파일명>.<slug>.json)")
    new = sub.add_parser("new", help="원형(또는 다른 사본) 폴더를 복사해 액티비티를 만든다")
    new.add_argument("--from", dest="origin", required=True, help="원본 slug")
    new.add_argument("--slug", required=True, help="새 slug(콘솔이 정한다, 예: parse_1)")
    new.add_argument("--title", help="processor.py 첫 줄 별명(기본: '<원본 별명> 사본')")
    remove = sub.add_parser("remove", help="사본 액티비티 폴더를 지운다(원형·사이트가 덮어쓰는 slug 는 거부)")
    remove.add_argument("--slug", required=True, help="지울 slug")
    diff = sub.add_parser("diff", help="사본 slug 가 복사된 뒤 원형이 바뀐 것을 보여 준다")
    diff.add_argument("slug", help="사본 slug(= activities/<slug>/ 폴더 이름)")
    diff.add_argument("--from", dest="origin", help="원형 slug(기본: 단계 원형 parse·chunk)")
    return p


SLUG_RE = re.compile(r"^(parse|chunk)(_[a-z0-9_]+)?$")


def _repo_relative(root: Path, paths: list[Path]) -> list[str]:
    """저장소 루트 기준 경로(콘솔이 git add 에 쓴다). git 저장소가 아니면 절대 경로."""
    try:
        repo = Path(_git(root, "rev-parse", "--show-toplevel").strip())
        return [p.resolve().relative_to(repo).as_posix() for p in paths]
    except (subprocess.CalledProcessError, ValueError, OSError):
        return [str(p.resolve()) for p in paths]


def new_activity(origin: str, slug: str, title: str | None = None, activities_dir: Path | None = None) -> list[str]:
    """원본 폴더를 새 slug 폴더로 복사한다 → 만든 파일 목록(저장소 루트 기준). 설정·코드 모두 저장소 원본 폴더에서.
    배포처(사이트 설정이 이미 activities/ 에 덮인 서빙본 사본)에서는 그 폴더가 곧 사이트 설정이다."""
    from genon.preprocessor.activities import _folders

    root = activities_dir or _folders.ACTIVITIES_DIR
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(f"slug 규칙에 맞지 않습니다: {slug!r} — ^(parse|chunk)(_[a-z0-9_]+)?$")
    if slug in _folders.STAGES:
        raise ValueError(f"{slug!r} 는 원형 이름입니다 — 사본 이름은 parse_…·chunk_… 로")
    if origin not in _folders.slugs(root):
        raise ValueError(f"원본 {origin!r} 가 없습니다 — 있는 것: {', '.join(_folders.slugs(root)) or '<없음>'}")
    if _folders.stage_of(slug) != _folders.stage_of(origin):
        raise ValueError(f"단계가 다릅니다 — {slug!r} 는 {_folders.stage_of(slug)}, 원본 {origin!r} 는 {_folders.stage_of(origin)}")
    if title is not None and ("\n" in title or "\r" in title):
        raise ValueError("--title 은 한 줄이어야 합니다(processor.py 첫 줄 주석)")
    dest = root / slug
    if dest.exists():
        raise ValueError(f"이미 있습니다: {dest}")
    source_cfg = source_code = root / origin                  # 저장소 원본 — 사이트 설정은 배포처 동기화가 덮는다(#561)
    code = {_folders.PROCESSOR_FILE, "__init__.py", "__main__.py"}       # 폴더 최상위의 액티비티 코드 — 저장소에서
    made: list[Path] = []
    dest.mkdir()
    try:
        for src in sorted(source_cfg.rglob("*")):                  # 설정·프롬프트·custom_field·플러그인(하위 폴더의 .py 포함)
            if src.is_dir() or "__pycache__" in src.parts or (src.parent == source_cfg and src.name in code):
                continue
            target = dest / src.relative_to(source_cfg)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
            made.append(target)
        for name in sorted(code):                                  # 코드 — 저장소 폴더에서
            if (source_code / name).is_file():
                shutil.copyfile(source_code / name, dest / name)
                made.append(dest / name)
        processor = dest / _folders.PROCESSOR_FILE
        lines = processor.read_text(encoding="utf-8").splitlines(keepends=True)
        origin_title = lines[0].lstrip("#").strip() if lines and lines[0].startswith("#") else origin
        lines[0:1 if lines and lines[0].startswith("#") else 0] = [f"# {title or origin_title + ' 사본'}\n"]
        processor.write_text("".join(lines), encoding="utf-8")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return _repo_relative(root, made)


def remove_activity(slug: str, activities_dir: Path | None = None) -> list[str]:
    """사본 폴더를 지운다 → 지운 파일 목록. 원형과 사이트 완성본이 덮어쓰는 slug 는 지우지 않는다."""
    from genon.preprocessor.activities import _folders

    root = activities_dir or _folders.ACTIVITIES_DIR
    if slug in _folders.STAGES:
        raise ValueError(f"{slug!r} 는 원형입니다 — 지우지 않습니다")
    if slug not in _folders.slugs(root):
        raise ValueError(f"액티비티 {slug!r} 가 없습니다 — 있는 것: {', '.join(_folders.slugs(root)) or '<없음>'}")
    site = _folders.site_activities_dir()
    if site is not None and (site / slug).is_dir():
        raise ValueError(f"{slug!r} 는 사이트 완성본({site / slug})이 덮어쓰는 액티비티입니다 — 사이트 쪽에서 정리하세요")
    target = root / slug
    files = [p for p in sorted(target.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]
    listed = _repo_relative(root, files)
    shutil.rmtree(target)
    return listed


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def diff_origin(slug: str, origin: str | None = None, activities_dir: Path | None = None) -> tuple[dict, str]:
    """사본 slug 의 복사 커밋 시점 원형 ↔ 지금 원형 → (요약, unified diff 본문)."""
    import difflib

    from genon.preprocessor.activities import _folders

    root = activities_dir or _folders.ACTIVITIES_DIR
    if slug not in _folders.slugs(root):
        raise ValueError(f"액티비티 {slug!r} 가 없습니다 — 있는 것: {', '.join(_folders.slugs(root)) or '<없음>'}")
    origin = origin or _folders.stage_of(slug)
    if origin == slug:
        raise ValueError(f"{slug!r} 는 원형입니다 — 사본 slug 를 주거나 --from 으로 원형을 지정하세요")
    if origin not in _folders.slugs(root):
        raise ValueError(f"원형 {origin!r} 폴더가 없습니다")
    repo = Path(_git(root, "rev-parse", "--show-toplevel").strip())
    rel = lambda p: p.resolve().relative_to(repo).as_posix()  # noqa: E731
    # pathspec 은 저장소 루트 기준이라 git 도 루트에서 돌린다(-C root 면 activities/ 기준으로 해석된다)
    first = _git(repo, "log", "--diff-filter=A", "--format=%H %cI", "--reverse", "--", rel(root / slug / "config.yaml"))
    summary = {"slug": slug, "origin": origin, "copied_at": None, "changed": [], "added": [], "removed": [], "unchanged": 0}
    if not first.strip():
        summary["note"] = "복사 커밋이 없습니다(미커밋 사본) — 커밋한 뒤 다시 보세요"
        return summary, ""
    commit, date = first.splitlines()[0].split(" ", 1)
    summary["copied_at"] = {"commit": commit[:12], "date": date}
    # -z: 한글 파일명을 따옴표·이스케이프 없이 그대로 받는다(core.quotepath 무관)
    listing = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", rel(root / origin))
    prefix = rel(root / origin) + "/"
    then_files = {ln[len(prefix):] for ln in listing.split("\0") if ln.startswith(prefix) and "__pycache__" not in ln}
    now_files = {p.relative_to(root / origin).as_posix() for p in (root / origin).rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts}
    body: list[str] = []
    for name in sorted(then_files | now_files):
        old = _git(repo, "show", f"{commit}:{prefix}{name}") if name in then_files else None
        new = (root / origin / name).read_text(encoding="utf-8") if name in now_files else None
        if old is None:
            summary["added"].append(name)
        elif new is None:
            summary["removed"].append(name)
        elif old == new:
            summary["unchanged"] += 1
            continue
        else:
            summary["changed"].append(name)
        body += difflib.unified_diff((old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True),
                                     fromfile=f"{origin}/{name}@{commit[:12]}", tofile=f"{origin}/{name}@now")
    return summary, "".join(body)


def _emit(payload: dict) -> None:
    sys.stdout.flush()
    print(f"{RESULT_PREFIX} {json.dumps(payload, ensure_ascii=False, default=str)}", flush=True)


def _failure(activity: str | None, started: float, exc: BaseException) -> dict:
    from genon.preprocessor.worker.registry import stage_of

    return {"ok": False, "stage": stage_of(activity or ""), "activity": activity,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "error": {"type": getattr(exc, "type", None) or type(exc).__name__,
                      "message": str(exc),
                      "non_retryable": bool(getattr(exc, "non_retryable", False))}}


def _input_path(args) -> str:
    if bool(args.file) == bool(args.input):
        raise _UsageError("입력 파일은 <file> 또는 --input 중 하나로 줍니다")
    return args.input or args.file


def _output_path(args, slug: str) -> Path:
    given = Path(_input_path(args)).name
    output = Path(args.output or f"{given}.{slug}.json").expanduser().absolute()
    if output.is_dir():
        raise _UsageError(f"-o 는 파일 경로여야 합니다(디렉터리): {output}")
    return output


def _publish(src: Path, dest: Path) -> None:
    """결과를 목적지에 원자적으로 — 같은 디렉터리의 임시 파일에 다 쓴 뒤 교체한다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def run(args) -> dict:
    """단건 실행 본체(자식 프로세스에서 돈다) → 액티비티 반환값 + output. 실패는 예외로 올린다."""
    from temporalio.testing import ActivityEnvironment

    from genon.preprocessor.worker import registry, runtime, settings

    entries = {e.slug: e for e in registry.scan()}
    entry = entries.get(args.activity)
    if entry is None:
        raise ValueError(f"액티비티 {args.activity!r} 가 없습니다 — 있는 것: {', '.join(sorted(entries)) or '<없음>'}")
    params = json.loads(args.params or "{}")
    if not isinstance(params, dict):
        raise ValueError("--params 는 JSON 객체여야 합니다")
    if args.doc_type:
        params["doc_type"] = args.doc_type
    given = Path(_input_path(args)).expanduser()
    if not given.is_file():
        raise FileNotFoundError(f"입력 파일이 없습니다: {_input_path(args)}")
    output = _output_path(args, entry.slug)

    work = Path(tempfile.mkdtemp(prefix="activity-run-"))
    try:
        inputs, artifacts = work / "in", work / "art"
        inputs.mkdir(), artifacts.mkdir()
        runtime.configure(settings.Settings(
            temporal_host="local", temporal_port=0, temporal_namespace="local", task_queue="local",
            processes=1, threads=1, nfs_root=str(inputs), artifact_root=str(artifacts),
            heartbeat_interval=10.0, port=0,
            llm_cache_root=(os.environ.get("LLM_CACHE_ROOT") or "").strip() or None))
        arg = {"output_ref": "out", "source_name": str(given.absolute()), "params": params}
        if entry.stage == "parse":
            shutil.copyfile(given, inputs / given.name)          # 확장자·파일명은 사용자가 준 이름
            arg["read_ref"] = {"root": "nfs", "path": given.name}
        else:
            shutil.copyfile(given, artifacts / "parsed.json")
            arg["parsed_ref"] = "parsed"
        env = ActivityEnvironment()
        env.info = dataclasses.replace(env.info, activity_type=entry.slug)
        result = env.run(registry.build([entry])[0], arg)
        _publish(artifacts / "out.json", output)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {**result, "output": str(output)}


def _child(args, result_file: str) -> int:
    """자식 프로세스: 결과(또는 실패)를 파일로 넘긴다. stdout 은 부모가 stderr 로 돌려 둔다."""
    started = time.monotonic()
    try:
        payload = {"ok": True, "data": run(args)}
    except Exception as exc:  # noqa: BLE001 — 계약상 어떤 실패든 결과 하나로 넘긴다
        payload = _failure(args.activity, started, exc)
    Path(result_file).write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    return 0 if payload["ok"] else 1


def _run_isolated(argv: list[str]) -> dict:
    """같은 명령을 자식 프로세스로 돌리고 결과를 받는다. 자식 stdout 은 이 프로세스의 stderr 로."""
    fd, result_file = tempfile.mkstemp(prefix="activity-result-", suffix=".json")
    os.close(fd)
    os.unlink(result_file)
    cmd = [sys.executable, "-m", "genon.preprocessor.activities", "_child", result_file, *argv]
    proc = subprocess.Popen(cmd, stdout=sys.stderr, stderr=sys.stderr)

    def _stop(signum, _frame):           # Studio 가 취소하면 자식도 끝낸다
        proc.terminate()
        raise KeyboardInterrupt(f"signal {signum}")

    old = {s: signal.signal(s, _stop) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        code = proc.wait()
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    try:
        return json.loads(Path(result_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError(f"단건 실행 프로세스가 결과 없이 끝났습니다(exit={code})") from None
    finally:
        Path(result_file).unlink(missing_ok=True)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if argv[:1] == ["_child"]:                       # 내부용 — _run_isolated 가 부른다
        return _child(_parser().parse_args(argv[2:]), argv[1])
    started = time.monotonic()
    activity = command = None
    try:
        args = _parser().parse_args(argv)
        command = args.command
        if args.command == "diff":
            activity = args.slug
            summary, body = diff_origin(args.slug, args.origin)
            if body:
                sys.stdout.write(body if body.endswith("\n") else body + "\n")
            _emit({"ok": True, "stage": None, "activity": activity, "command": "diff",
                   "duration_ms": int((time.monotonic() - started) * 1000), "data": summary})
            return 0
        if args.command in ("new", "remove"):
            from genon.preprocessor.activities import _folders

            activity = args.slug
            files = (new_activity(args.origin, args.slug, args.title) if args.command == "new"
                     else remove_activity(args.slug))
            _emit({"ok": True, "stage": _folders.stage_of(activity), "activity": activity, "command": args.command,
                   "duration_ms": int((time.monotonic() - started) * 1000), "data": {"files": files}})
            return 0
        activity = args.activity
        result = _run_isolated(argv)
    except SystemExit as exc:                        # --help
        return int(exc.code or 0)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 — 인자 오류·자식 이상 종료·취소도 결과 한 줄로
        _emit({**_failure(activity, started, exc), **({"command": command} if command else {})})
        return 1
    if not result.get("ok"):
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        _emit(result)
        return 1
    data = result["data"]
    _emit({"ok": True, "stage": data.get("stage"), "activity": activity,
           "duration_ms": int((time.monotonic() - started) * 1000), "data": data})
    return 0


if __name__ == "__main__":
    sys.exit(main())
