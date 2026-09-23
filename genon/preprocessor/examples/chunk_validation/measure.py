#!/usr/bin/env python
"""이상 청크 판정(chunking.validation) 실측 보고서.

저장소에 남아 있는 실제 청킹 결과(*.chunks.json)에 판정기를 돌려, 정상 청크에서 지표가
어디까지 올라가는지와 임계값 후보별 제외 건수를 보고한다. 기준을 정하거나 바꿀 때 근거로
쓰고, 고객 자료로 `report` 모드 관측을 할 때도 같은 형식으로 비교한다.

출력(마크다운)
  1 코퍼스 규모       파일 수, 청크 수, 본문 중복 제거 후 청크 수
  2 판정 샘플         tests/fixtures/chunk_quality/cases.yaml 기대 사유 일치 여부
  3 지표 분포         p50/p90/p99/최대(반복 지표는 2회 이상부터 센다)
  4 임계값 여유       정상 청크 최대값 대비 채택 임계값. 2배 미만이면 표시한다
  5 후보별 제외 건수  min_chars·repeat_min_count 후보마다 제외 판정 건수
  6 제외 판정 전수    파일·순번·사유·근거·본문 앞 80자
  7 통과 청크 표본    무작위 50건(시드 고정)

청크 종류는 vector_meta 로 추정한다. chunk_bboxes 가 "." 이면 행, 비어 있으면 평문,
그 밖은 문서형이다. 본문 앞의 `HEADER: …` 줄까지는 코어가 붙인 접두로 보고 판정에서 뺀다.

사용:
  .venv/bin/python examples/chunk_validation/measure.py                  # 기본 코퍼스
  .venv/bin/python examples/chunk_validation/measure.py --extra <dir> …   # 청킹 결과 추가
  .venv/bin/python examples/chunk_validation/measure.py --out report.md
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

import yaml

PREPROCESSOR_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PREPROCESSOR_DIR.parents[1]))

from genon.preprocessor.processing.chunking import chunk_quality as cq  # noqa: E402

DEFAULT_ROOTS = [PREPROCESSOR_DIR / "examples" / "parse_chunk"]
CASES = PREPROCESSOR_DIR / "tests" / "fixtures" / "chunk_quality" / "cases.yaml"
_HEADER_RE = re.compile(r"^HEADER: [^\n]*\n?", re.M)
_METRICS = ("content", "run", "line", "phrase", "broken", "broken_share", "dup_share")


def build_text(spec) -> str:
    # cases.yaml 의 생성 규칙. tests/unit/test_chunk_quality_unit.py 와 같은 해석이다.
    out = []
    for piece in spec if isinstance(spec, list) else [spec]:
        if isinstance(piece, str):
            out.append(piece)
        elif "repeat" in piece:
            out.append(piece["repeat"] * piece["times"])
        else:
            out.append("".join(chr(0xAC00 + i) for i in range(piece["distinct"])))
    return "".join(out)


def _kind(vector_meta: dict) -> str:
    bboxes = vector_meta.get("chunk_bboxes")
    if bboxes == ".":
        return "row"
    return "text" if bboxes in (None, "") else "docling"


def load_chunks(roots: list) -> tuple:
    """(파일 수, 전체 청크 수, 중복 제거한 청크 목록)."""
    files, total, seen, chunks = 0, 0, set(), []
    for root in roots:
        for path in sorted(Path(root).rglob("*.chunks.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, list):
                continue
            files += 1
            for index, item in enumerate(data):
                text = item.get("text") if isinstance(item, dict) else None
                if not isinstance(text, str):
                    continue
                total += 1
                if text in seen:
                    continue
                seen.add(text)
                header = _HEADER_RE.search(text)
                chunks.append({
                    "file": str(path.relative_to(PREPROCESSOR_DIR)) if path.is_relative_to(
                        PREPROCESSOR_DIR) else str(path),
                    "index": index, "text": text, "kind": _kind(item),
                    "prefix_len": header.end() if header else 0,
                })
    return files, total, chunks


def _pct(values: list, q: float):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))] if ordered else 0


def _fmt(value) -> str:
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def report(roots: list, sample_size: int = 50) -> str:
    cfg = cq.Config()
    files, total, chunks = load_chunks(roots)
    lines = [
        "# 이상 청크 판정 실측 보고서", "",
        f"설정: 모듈 기본값 {cfg}", "",
        "## 1. 코퍼스", "",
        f"- 파일 {files}개, 청크 {total}건, 본문 중복 제거 후 {len(chunks)}건",
        f"- 입력: {', '.join(str(r) for r in roots)}", "",
    ]

    lines += ["## 2. 판정 샘플(cases.yaml)", ""]
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))["cases"]
    mismatched = []
    for case in cases:
        prefix = case.get("prefix", "")
        verdict = cq.judge(prefix + build_text(case["text"]), kind=case.get("kind", "docling"),
                           code_like=case.get("code_like", False), prefix_len=len(prefix),
                           cfg=cfg)
        got = verdict.reason if verdict else "keep"
        if got != case["expect"]:
            mismatched.append(f"{case['id']}(기대 {case['expect']}, 실제 {got})")
    lines += [f"- {len(cases)}건 중 기대 일치 {len(cases) - len(mismatched)}건",
              f"- 불일치: {', '.join(mismatched) or '없음'}", ""]

    measured = [cq.measure(c["text"], prefix_len=c["prefix_len"], repeat_min_count=2)
                for c in chunks]
    lines += ["## 3. 지표 분포(정상 코퍼스)", "",
              "| 지표 | p50 | p90 | p99 | 최대 |", "|---|---|---|---|---|"]
    for metric in _METRICS:
        values = [m[metric] for m in measured]
        lines.append(f"| {metric} | " + " | ".join(
            _fmt(_pct(values, q)) for q in (0.5, 0.9, 0.99)) + f" | {_fmt(max(values, default=0))} |")
    lines.append("")

    observed = {metric: max((m[metric] for m in measured), default=0)
                for metric in ("run", "line", "phrase", "broken")}
    body_min = min((m["content"] for c, m in zip(chunks, measured)
                    if c["kind"] != "row" and not m["has_table"] and not m["blank"]), default=0)
    lines += ["## 4. 임계값 여유", "", "| 지표 | 정상 최대 | 임계값 | 여유 |", "|---|---|---|---|"]
    for metric in ("run", "line", "phrase"):
        margin = cfg.repeat_min_count / observed[metric] if observed[metric] else float("inf")
        flag = "" if margin >= 2 else " (2배 미만)"
        lines.append(f"| {metric} | {observed[metric]} | {cfg.repeat_min_count} | "
                     f"{margin:.1f}배{flag} |")
    lines.append(f"| broken | {observed['broken']} | {cfg.broken_min_count} + 점유율 "
                 f"{cfg.broken_max_share} | - |")
    lines.append(f"| 내용 문자 최솟값(문서·평문, 표 제외) | {body_min} | 하한 {cfg.min_chars} | "
                 f"{'경계' if body_min == cfg.min_chars else ('여유' if body_min > cfg.min_chars else '미달')} |")
    lines.append("")

    lines += ["## 5. 임계값 후보별 제외 건수", "", "| 설정 | 제외 |", "|---|---|"]
    for key, candidates in (("min_chars", (1, 2, 3, 4, 5, 8, 10)),
                            ("repeat_min_count", (5, 6, 8, 10, 15))):
        for value in candidates:
            alt = cq.Config(**{key: value})
            count = sum(1 for c in chunks if cq.judge(
                c["text"], kind=c["kind"], prefix_len=c["prefix_len"], cfg=alt) is not None)
            lines.append(f"| {key}={value}{' (채택)' if getattr(cfg, key) == value else ''} | {count} |")
    lines.append("")

    rejected, passed = [], []
    for chunk in chunks:
        verdict = cq.judge(chunk["text"], kind=chunk["kind"], prefix_len=chunk["prefix_len"],
                           cfg=cfg)
        (rejected if verdict else passed).append((chunk, verdict))
    lines += [f"## 6. 제외 판정 전수({len(rejected)}건)", ""]
    if rejected:
        lines += ["| 파일 | 순번 | 사유 | 근거 | 본문 앞 80자 |", "|---|---|---|---|---|"]
        for chunk, verdict in rejected:
            head = chunk["text"][chunk["prefix_len"]:][:80].replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {chunk['file']} | {chunk['index']} | {verdict.reason} | "
                         f"{verdict.describe()} | {head} |")
    else:
        lines.append("- 없음")
    lines.append("")

    lines += [f"## 7. 통과 청크 표본({min(sample_size, len(passed))}건, 시드 0)", ""]
    for chunk, _ in random.Random(0).sample(passed, min(sample_size, len(passed))):
        head = chunk["text"][chunk["prefix_len"]:][:80].replace("\n", " ")
        lines.append(f"- [{chunk['kind']}] {chunk['file']}#{chunk['index']}: {head}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extra", nargs="*", default=[], help="*.chunks.json 을 더 찾을 디렉터리")
    ap.add_argument("--out", default=None, help="보고서 저장 경로(미지정 시 stdout)")
    args = ap.parse_args()
    text = report(DEFAULT_ROOTS + [Path(p) for p in args.extra])
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"보고서: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
