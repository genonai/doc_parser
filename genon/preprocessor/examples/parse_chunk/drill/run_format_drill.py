#!/usr/bin/env python
"""xlsx / md / html 신규 문서 드릴 (#363 08-4).

06 이 JSON 으로 물은 것을 나머지 세 포맷에 묻는다 — **설정으로 안 되는 원천을 고객이
facade 훅으로 처리할 수 있는가.**

절차
  1. 그대로 넣어 본다        --step raw
  2. 훅을 넣고 다시 본다      사람이 facade/parser_processor.py 를 고치고 --step raw 재실행
  3. 판정                    각 케이스의 expect 문자열이 청크 본문에 있는가

설정(custom_fields)을 쓰지 않는다. 이 드릴이 재는 것은 훅이지 설정이 아니다.

    ./run_format_drill.py
    ./run_format_drill.py --only x1 m1
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PARSE_CHUNK_DIR = SCRIPT_DIR.parent
PREPROCESSOR_DIR = SCRIPT_DIR.parents[2]
FIXTURE_DIR = PREPROCESSOR_DIR / "sample_files" / "drill"

if str(PARSE_CHUNK_DIR) not in sys.path:
    sys.path.insert(0, str(PARSE_CHUNK_DIR))

import parse_chunk_verify as verify  # noqa: E402

# (키, 픽스처, 변형, 있어야 하는 것, 없어야 하는 것, 최소 청크 수)
#
# 본문에 글자가 있는지만 보면 약한 단정이 된다 — 마커 승격은 글자를 바꾸지 않는다.
# 구조가 살아났는지는 **청크가 갈렸는지**로 본다(승격 전 1청크 / 후 3청크).
CASES = [
    ("x1", "x1_junk_header.xlsx", "상단 2행이 로고·안내문",
     ["강남", "서울 강남구"], ["(주)샘플 고객센터"], 1),
    ("x2", "x2_value_only.xlsx", "값만 정규화 + 진짜 병합 헤더",
     ["연락처_전화", "021112222"], [], 1),
    ("x3", "x3_multi_sheet.xlsx", "시트 2개 중 '본문' 만",
     ["가입 안내"], ["이 파일은 내부용입니다"], 1),
    ("m1", "m1_marker_headings.md", "■ ▶ 마커로만 계층",
     ["가입 자격", "우대 금리"], [], 3),
    ("m2", "m2_text_fence.md", "```text 펜스",
     ["권리와 의무를 정함을 목적으로 한다"], [], 1),
    ("h1", "h1_srcdoc.html", "iframe srcdoc 안 본문",
     ["국내전용 1만원"], [], 1),
    ("h2", "h2_hidden_accordion.html", "숨긴 아코디언 안 답변",
     ["영업일 기준 2~3일"], [], 1),
]


def run(python: str, fixture: Path, doc_type: str | None, out_dir: Path) -> tuple[bool, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ok, err, _ = verify.run_case(python, doc_type, fixture, out_dir, [])
    return ok, err


def chunks_of(out_dir: Path, fixture: Path) -> list[str]:
    p = out_dir / (fixture.stem + ".chunks.json")
    if not p.exists():
        return []
    return [c.get("text") or "" for c in json.loads(p.read_text(encoding="utf-8"))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--no-hook", action="store_true",
                    help="doc_type 을 넘기지 않는다. 훅이 전부 doc_type 게이팅이라 "
                         "이것이 곧 '훅 없이 그대로' 기준선이다")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    cases = [c for c in CASES if not args.only or c[0] in args.only]
    out_root = Path(tempfile.mkdtemp(prefix="format_drill_"))
    print(f"{'키':<5} {'판정':<7} {'변형':<26} 상세")
    print("-" * 100)
    failed = 0
    try:
        for key, name, note, want, avoid, min_chunks in cases:
            fixture = FIXTURE_DIR / name
            if not fixture.exists():
                print(f"{key:<5} {'SKIP':<7} {note:<26} 픽스처 없음 (make_format_fixtures.py)")
                continue
            doc_type = None if args.no_hook else f"drill_{key}"
            ok, err = run(sys.executable, fixture, doc_type, out_root / key)
            if not ok:
                print(f"{key:<5} {'ERROR':<7} {note:<26} {err[:60]}"); failed += 1; continue
            chunks = chunks_of(out_root / key, fixture)
            text = "\n".join(chunks)
            detail = []
            missing = [w for w in want if w not in text]
            leaked = [a for a in avoid if a in text]
            if missing:
                detail.append(f"없음: {', '.join(missing)}")
            if leaked:
                detail.append(f"섞임: {', '.join(leaked)}")
            if len(chunks) < min_chunks:
                detail.append(f"청크 {len(chunks)}건(최소 {min_chunks})")
            if detail:
                print(f"{key:<5} {'FAIL':<7} {note:<26} {' / '.join(detail)}"); failed += 1
            else:
                print(f"{key:<5} {'OK':<7} {note:<26} 청크 {len(chunks)}건 · {len(text)}자")
        print("-" * 100)
        print(f"OK {len(cases) - failed} / 전체 {len(cases)}")
        return 1 if failed else 0
    finally:
        if args.keep:
            print(f"\n산출 보존: {out_root}")
        else:
            shutil.rmtree(out_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
