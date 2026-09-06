#!/usr/bin/env python
"""청크 텍스트 정제 3종 대조 실행 (#363).

같은 원천을 네 번 파싱·청킹해 산출을 나란히 잰다.

  baseline    정제 없음     설정을 끈 사본으로 돌린다
  yaml        설정만        운영 설정 그대로(chunking.text_cleanup 이 켜져 있다)
  post_chunk  훅만          설정은 끄고 hooks_post_chunk.py 의 Hooks 를 파사드에 얹는다
  both        설정 + 훅     운영 설정 + hooks_both.py — 공통은 설정, 예외만 훅

단정
  - yaml / post_chunk / both 에서 장식 마커와 미해독 엔티티가 0 이어야 한다.
  - 네 경우 모두 n_char 가 실제 본문 길이와 일치해야 한다(훅이 refresh_stats 를 부르는지).
  - both 는 내부용 안내 청크를 버리므로 baseline 보다 청크가 적어야 한다.

    ./run_cleanup_examples.sh
    ./run_cleanup_examples.sh --only yaml both --keep
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PREPROCESSOR_DIR = HERE.parents[1]
REPO_ROOT = PREPROCESSOR_DIR.parent
PC_DIR = PREPROCESSOR_DIR / "examples" / "parse_chunk"
BASE_CONFIG = PREPROCESSOR_DIR / "resource_dev" / "chunking_processor_config.yaml"
SAMPLE = PREPROCESSOR_DIR / "sample_files" / "monimo" / ".INC_235488_02_20260626103138.html.parsed"
DOC_TYPE = "cs_hpp"

# 지워졌는지 세는 대상. README 의 "지울 것" 과 같은 목록이다.
GLYPHS = re.compile(r"[■◈※☎▶●◆▲☞]")
ENTITIES = re.compile(r"&(?:gt|lt|amp|nbsp);")

CASES = ("baseline", "yaml", "post_chunk", "both")
HOOKS = {"post_chunk": "hooks_post_chunk.py", "both": "hooks_both.py"}

# parse_chunk_test.py 를 그대로 재사용하면서 파사드에만 훅을 얹는 자식 스크립트.
# 서브클래스 오버라이드는 파사드 파일을 직접 고치는 것과 같은 경로를 탄다
# (core 가 `type(self).post_chunk is not ChunkerCore.post_chunk` 로 판정하지 않고
#  항상 부르므로, 여기서는 클래스만 갈아 끼우면 된다).
CHILD = '''
import importlib.util, os, sys
sys.path.insert(0, os.environ["PC_DIR"])
import parse_chunk_verify  # noqa: F401  환경 변수 설정
import parse_chunk_test as t
hooks = os.environ.get("HOOKS_FILE")
if hooks:
    spec = importlib.util.spec_from_file_location("cleanup_hooks", hooks)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cleanup_hooks"] = mod
    spec.loader.exec_module(mod)
    methods = {n: v for n, v in vars(mod.Hooks).items()
               if n in ("pre_chunk", "post_chunk")}
    t.ChunkerProcessor = type("HookedChunker", (t.ChunkerProcessor,), methods)
os.chdir(os.environ["PC_DIR"])
sys.argv = ["parse_chunk_test.py"] + sys.argv[1:]
raise SystemExit(t.main() or 0)
'''


def build_off_config(work: Path) -> Path:
    """정제를 끈 설정 사본. 기준선과 `post_chunk` 만 쓰는 예시가 이것을 쓴다.

    운영 설정은 `text_cleanup` 이 켜져 있으므로(#363), "정제 없음" 을 보려면 끈 사본이
    필요하다. 예시용 설정을 통째로 따로 두지 않는 이유는 설정이 두 벌이 되면 실제
    설정이 바뀌어도 예시가 옛 값을 계속 보여 주기 때문이다.
    """
    cfg = yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
    cfg.setdefault("chunking", {})["text_cleanup"] = "off"
    out = work / "chunking_processor_config.off.yaml"
    out.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    return out


def check_rules_in_sync() -> None:
    """cleanup_rules.yaml 이 실제 운영 설정과 같은지 본다.

    붙여넣기용 블록과 실제 설정이 어긋나면 문서가 거짓이 된다.
    """
    shipped = (yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8"))
               .get("chunking", {}).get("text_cleanup"))
    example = yaml.safe_load((HERE / "cleanup_rules.yaml").read_text(encoding="utf-8"))
    if shipped != example.get("text_cleanup"):
        raise SystemExit(
            "cleanup_rules.yaml 과 실제 설정이 다릅니다.\n"
            f"  설정: {shipped}\n  예시: {example.get('text_cleanup')}")


def run(case: str, python: str, child: Path, cfg: Path, src: Path, out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PC_DIR=str(PC_DIR))
    if case in HOOKS:
        env["HOOKS_FILE"] = str(HERE / HOOKS[case])
    else:
        env.pop("HOOKS_FILE", None)
    cmd = [python, str(child), "--doc_type", DOC_TYPE, "--llm_cache"]
    if case in ("baseline", "post_chunk"):
        cmd += ["--chunker-config", str(cfg)]   # 정제 끈 사본
    cmd += [str(src), str(out_dir) + "/"]
    proc = subprocess.run(cmd, cwd=str(out_dir), capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        return "실행실패: " + " / ".join(x.strip() for x in tail)
    return ""


def measure(out_dir: Path, src: Path) -> dict:
    path = out_dir / (src.stem + ".chunks.json")
    if not path.exists():
        return {}
    chunks = json.loads(path.read_text(encoding="utf-8"))
    texts = [c.get("text") or "" for c in chunks]
    joined = "\n".join(texts)
    return {
        "n": len(chunks),
        "chars": sum(len(t) for t in texts),
        "glyphs": len(GLYPHS.findall(joined)),
        "entities": len(ENTITIES.findall(joined)),
        "stale": sum(1 for c in chunks if len(c.get("text") or "") != c.get("n_char")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None, choices=CASES)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    if not SAMPLE.exists():
        print(f"원천이 없습니다: {SAMPLE}")
        return 1
    cases = [c for c in CASES if not args.only or c in args.only]
    if "both" in cases and "baseline" not in cases:
        cases = ["baseline"] + cases          # both 판정에 기준선이 필요하다

    work = Path(tempfile.mkdtemp(prefix="text_cleanup_"))
    child = work / "_child.py"
    child.write_text(CHILD, encoding="utf-8")
    check_rules_in_sync()
    cfg = build_off_config(work)
    # 원천은 사본으로 넘긴다 — 파서가 파생 파일을 입력 옆에 만드는 경로가 있다.
    src = work / SAMPLE.name
    shutil.copy2(SAMPLE, src)

    got: dict = {}
    try:
        print(f"{'예시':<12}{'청크':<6}{'총자수':<8}{'장식마커':<9}{'엔티티':<8}{'n_char 불일치'}")
        print("-" * 62)
        for case in cases:
            err = run(case, args.python, child, cfg, src, work / case)
            if err:
                print(f"{case:<12}{err[:48]}")
                continue
            m = measure(work / case, src)
            got[case] = m
            print(f"{case:<12}{m['n']:<6}{m['chars']:<8}{m['glyphs']:<9}"
                  f"{m['entities']:<8}{m['stale']}")
        print("-" * 62)

        problems = []
        for case, m in got.items():
            if case != "baseline" and (m["glyphs"] or m["entities"]):
                problems.append(f"{case}: 장식마커 {m['glyphs']} · 엔티티 {m['entities']} 남음")
            if m["stale"]:
                problems.append(f"{case}: n_char 불일치 {m['stale']}/{m['n']}건"
                                " (refresh_stats 를 안 불렀다)")
        if "both" in got and "baseline" in got and got["both"]["n"] >= got["baseline"]["n"]:
            problems.append("both: 내부용 안내 청크가 버려지지 않았다"
                            f" ({got['baseline']['n']} → {got['both']['n']})")
        for line in problems:
            print("  실패:", line)
        print("통과" if not problems else f"실패 {len(problems)}건")
        return 1 if problems else 0
    finally:
        if args.keep:
            print(f"\n산출 보존: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
