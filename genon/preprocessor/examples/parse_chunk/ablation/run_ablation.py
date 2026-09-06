#!/usr/bin/env python
"""설정 최소화 ablation (#363 08-A / 08-B).

"프로세서가 작아졌다" 와 "고객이 쓸 수 있다" 는 다른 명제다. 06 드릴이 **신규** 문서로
뒤쪽을 확인했다면, 이 스크립트는 **기존** doc_type 으로 확인한다.

묻는 것: 출고 custom_fields yaml 을 초기 개발 단계 수준으로 깎았을 때, 그 차이를
facade 훅과 toolbox 로 메울 수 있는가.

절차
  1. 기준선   --step base   출고 설정 그대로 실행
  2. 최소화   --step min    고급 키를 제거한 사본으로 실행
  3. 대조     --step diff   두 산출의 차이를 필드 단위로 낸다
  (기본은 base -> min -> diff 순차 실행)

  4. 사람이 그 차이를 훅으로 메우고 다시 --step min diff 로 확인한다.
     메우는 데 든 줄 수와 toolbox 밖 import 여부가 이 검증의 진짜 산출이다.

배포 설정을 건드리지 않는다. `resource_dev/` 를 임시 디렉터리로 복사한 뒤 그 사본만
최소화하고 `--config` 로 넘긴다.

LLM 캐시 스코프는 두 실행이 **같다**. 스코프를 나누면 2회차가 LLM 을 다시 불러 그 답의
흔들림까지 차이로 잡힌다(00 결정 2, 실측 58개 산출물). 프롬프트가 같으면 재사용되고
최소화로 프롬프트가 바뀌면 그때만 다시 부른다 — 그것이 재려는 차이다.

사용:
    ./run_ablation.py --only card cs_slf cs_ssf      # 1군(조기 경보)
    ./run_ablation.py --step diff                    # 이미 돌린 산출만 다시 대조
    ./run_ablation.py --keep                         # 산출물 보존
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
PARSE_CHUNK_DIR = SCRIPT_DIR.parent
PREPROCESSOR_DIR = SCRIPT_DIR.parents[2]

if str(PARSE_CHUNK_DIR) not in sys.path:
    sys.path.insert(0, str(PARSE_CHUNK_DIR))

import parse_chunk_verify as verify  # noqa: E402  (DYLD 환경도 여기서 세워진다)

# ── 제거 계층 ────────────────────────────────────────────────────────────────
# 근거는 docs/plans/facade-slimming/08-customer-facade.md "기본 기능 집합" 절이다.
# 최초 구현 시점(git log -S)과 사용 폭(출고 17파일) 두 실측으로 갈랐다.
#
# 경로로 지정한다 — 이름으로 지우면 `source.kind: sections`(값)와
# `source.sections`(키)처럼 같은 낱말이 다른 뜻인 자리를 잘못 건드린다.
ADVANCED_PATHS = [
    ("source", "records_at"),      # 2026-09-03 · 3파일
    ("source", "on_missing"),      # 2026-08-26 · 6파일
    ("source", "ignore_keys"),     # 2026-08-27 · 1파일
    ("source", "merge_rows"),      # 2026-09-03 · 1파일 (order_by 를 품는다)
    ("source", "sections"),        # 2026-08-27 · 1파일 (json_semantic 섹션 라벨)
    # 원천 전처리는 기능별로 쪼개서 잰다. 뭉뚱그리면 원인을 못 가른다.
    ("source", "pre", "markdown", "text_fence"),
    ("source", "pre", "markdown", "marker_headings"),
    ("source", "pre", "html", "marker_headings"),
    ("body", "once"),
    ("body", "mirror_to"),         # 2026-09-03 · 1파일
]
BOUNDARY_PATHS = [
    ("body", "split"),             # 2026-06-16
    ("body", "repeat"),
    ("body", "labels"),            # 2026-08-30
    ("source", "require"),
]
TIERS = {"advanced": ADVANCED_PATHS, "boundary": ADVANCED_PATHS + BOUNDARY_PATHS}

# 같은 버전 2회 실행에서도 흔들리는 필드(00 노이즈 실측). 대조에서 뺀다.
NOISE_FIELDS = {"reg_date"}

# 폐기된 doc_type — 고객이 더 이상 쓰지 않는다(2026-09-06 확인).
RETIRED = {"research_report"}


def resource_dir() -> Path:
    dev = PREPROCESSOR_DIR / "resource_dev"
    return dev if dev.exists() else PREPROCESSOR_DIR / "resource"


def cases_for(only: list[str] | None) -> list[tuple]:
    """(doc_type, 샘플, 비고, 이 샘플을 지배하는 custom_field yaml stem).

    doc_type 하나에 yaml 이 둘인 경우가 있어(faq: xlsx->faq / json->faq_json,
    product_hpp: md->llm / json->json_semantic) 확장자로 고른다. 이것을 빼먹으면
    "이 doc_type 은 최소화 대상이 아니다" 라는 보고가 틀린다.
    """
    blocks = verify.load_custom_field_blocks()
    out = []
    for doc_type, path, note in verify.CASES:
        if doc_type in RETIRED:
            continue
        if only and doc_type not in only:
            continue
        src = Path(path)
        block = verify.pick_block(blocks, doc_type, src.suffix) or {}
        stem = str(block.get("config_file") or "").replace("custom_field_", "").replace(".yaml", "")
        out.append((doc_type, src, note, stem))
    return out


def prune(node, paths: list[tuple]) -> list[str]:
    """설정 트리에서 지정 경로를 지운다. 지운 경로 목록을 돌려준다.

    경로는 임의 깊이다 — `source.pre` 처럼 뭉뚱그리면 그 안의 서로 다른 기능
    (front_matter 승격 / text_fence / marker_headings)이 한꺼번에 사라져 무엇이
    차이를 만들었는지 못 가른다(08-B 실측).
    """
    removed: list[str] = []
    for path in paths:
        cur = node
        for seg in path[:-1]:
            if not isinstance(cur, dict) or seg not in cur:
                cur = None
                break
            cur = cur[seg]
        if isinstance(cur, dict) and path[-1] in cur:
            cur.pop(path[-1])
            removed.append(".".join(path))
    return removed


def build_min_config(work: Path, tier: str) -> tuple[Path, dict]:
    """배포 설정 사본을 만들고 custom_field_*.yaml 을 최소화한다."""
    src = resource_dir()
    dst = work / "resource"
    shutil.copytree(src, dst)
    report: dict[str, list[str]] = {}
    for cfg in sorted(dst.glob("custom_field_*.yaml")):
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        removed = prune(data, TIERS[tier])
        if removed:
            cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")
            report[cfg.stem.replace("custom_field_", "")] = removed
    return dst / "parser_processor_config.yaml", report


def build_base_config(work: Path) -> Path:
    dst = work / "resource_base"
    shutil.copytree(resource_dir(), dst)
    return dst / "parser_processor_config.yaml"


def run(python: str, doc_type: str, src: Path, out_dir: Path,
        config: Path, cache_root: Path) -> tuple[bool, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # --output-format 을 주지 않는다. parse_chunk_verify 와 같은 조건이어야 한다 —
    # json 을 강제하면 청커가 parse-format 경로로 빠져 문서 단위 custom_fields 가
    # 실리지 않는다(실측: cs_hpp 5청크/필드 채움 -> 18청크/0채움).
    extra = ["--config", str(config),
             "--llm_cache", "--interim_root", str(cache_root),
             "--workflow_id", "ablation", "--run_id", "shared"]
    ok, err, _ = verify.run_case(python, doc_type, src, out_dir, extra)
    return ok, err


def load_chunks(out_dir: Path, src: Path) -> list | None:
    p = out_dir / (src.stem + ".chunks.json")
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def compare(base: list | None, mini: list | None) -> dict:
    """청크 목록 두 벌을 필드 단위로 대조한다."""
    if base is None:
        return {"verdict": "기준선 없음", "detail": "base 산출물이 없다 — 검증 불가"}
    if mini is None:
        return {"verdict": "최소판 실패", "detail": "min 산출물이 없다(기동 실패 가능)"}
    if len(base) != len(mini):
        return {"verdict": "차이", "detail": f"청크 수 {len(base)} -> {len(mini)}"}

    fields: dict[str, int] = {}
    for b, m in zip(base, mini):
        for key in set(b) | set(m):
            if key in NOISE_FIELDS:
                continue
            if b.get(key) != m.get(key):
                fields[key] = fields.get(key, 0) + 1
    if not fields:
        return {"verdict": "동일", "detail": f"청크 {len(base)}건 차이 0"}
    order = sorted(fields.items(), key=lambda kv: -kv[1])
    return {"verdict": "차이", "detail": ", ".join(f"{k}({n})" for k, n in order)}


def empty_fields(chunks: list | None) -> list[str]:
    """전 청크에서 비어 있는 필드. 기준선에서 비면 그 필드는 대조 가치가 없다."""
    if not chunks:
        return []
    keys = set()
    for c in chunks:
        keys |= set(c)
    return sorted(k for k in keys - NOISE_FIELDS
                  if all(c.get(k) in (None, "", [], {}) for c in chunks))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None, help="doc_type (card cs_slf …)")
    ap.add_argument("--step", nargs="*", default=["base", "min", "diff"],
                    choices=["base", "min", "diff"])
    ap.add_argument("--tier", default="advanced", choices=list(TIERS))
    ap.add_argument("--out", default=None, help="산출 루트(기본: ablation/result)")
    ap.add_argument("--keep", action="store_true", help="설정 사본 보존")
    args = ap.parse_args()

    python = sys.executable
    out_root = Path(args.out) if args.out else SCRIPT_DIR / "result"
    cache_root = out_root / "_llm_cache"
    out_root.mkdir(parents=True, exist_ok=True)

    cases = cases_for(args.only)
    if not cases:
        print(f"--only {args.only} 에 해당하는 케이스가 없습니다.")
        return 1

    work = Path(tempfile.mkdtemp(prefix="ablation_"))
    try:
        base_cfg = build_base_config(work)
        min_cfg, removed = build_min_config(work, args.tier)

        print(f"제거 계층: {args.tier} · 대상 케이스 {len(cases)}건 · 설정 사본 {work}")
        for name, keys in sorted(removed.items()):
            print(f"  최소화 {name:<22} - {', '.join(keys)}")
        untouched = {st for _, _, _, st in cases if st} - set(removed)
        if untouched:
            print(f"  제거할 고급 키가 없는 설정: {', '.join(sorted(untouched))}"
                  f" — 훅 0줄로 차이 0 이어야 정상")
        print()

        for phase, cfg in (("base", base_cfg), ("min", min_cfg)):
            if phase not in args.step:
                continue
            for doc_type, src, note, stem in cases:
                if not src.exists():
                    print(f"  SKIP  {phase:<4} {doc_type:<16} {src.name} (샘플 없음)")
                    continue
                out_dir = out_root / phase / doc_type
                ok, err = run(python, doc_type, src, out_dir, cfg, cache_root)
                print(f"  {'OK  ' if ok else 'FAIL'}  {phase:<4} {doc_type:<16} "
                      f"{src.name}{'' if ok else '  ' + err}")
            print()

        if "diff" not in args.step:
            return 0

        print(f"{'doc_type':<14} {'설정':<22} {'샘플':<40} {'판정':<10} 상세")
        print("-" * 120)
        worst = 0
        for doc_type, src, note, stem in cases:
            if not src.exists():
                continue
            base = load_chunks(out_root / "base" / doc_type, src)
            mini = load_chunks(out_root / "min" / doc_type, src)
            res = compare(base, mini)
            blank = empty_fields(base)
            if res["verdict"] != "동일":
                worst = max(worst, 1)
            note2 = res["detail"]
            if blank:
                note2 += f"  [기준선 공백: {', '.join(blank[:4])}]"
            print(f"{doc_type:<14} {stem[:20]:<22} {src.name[:38]:<40} {res['verdict']:<10} {note2}")
        return worst
    finally:
        if args.keep:
            print(f"\n설정 사본 보존: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
