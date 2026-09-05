#!/usr/bin/env python
"""신규 문서 실전 드릴(#363 06) 실행기.

"프로세서가 작아졌다" 와 "고객이 새 문서를 스스로 처리할 수 있다" 는 다른 명제다.
이 스크립트는 뒤쪽을 실제 문서로 확인한다.

절차(계획 06 참조)
  1. 그대로 넣어 본다              → --step raw
  2. 설정만으로 시도한다            → --step config  (기본)
  3. 안 되면 facade 코드를 고친다   → 사람이 하고, 다시 --step config 로 확인
  4. 청커 산출까지 확인한다         → 이 스크립트가 항상 함께 본다
  5. 골든 대조                     → parse_chunk_golden.py --check (별도)
  6. 기록하고 수정을 되돌린다        → docs/plans/facade-slimming/06-drill-results.md

배포 설정을 건드리지 않는다. `resource_dev/`(없으면 `resource/`)를 임시 디렉터리로 복사한
뒤 드릴 설정만 덧붙여 등록하고, 그 사본을 `--config` 로 넘긴다. 그래서 이 스크립트를
돌려도 골든이 흔들리지 않는다.

사용:
    ./run_drill.py                 # 전체
    ./run_drill.py --only b1 b4
    ./run_drill.py --step raw      # 설정 없이 그대로
    ./run_drill.py --keep          # 산출물 보존
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
FIXTURE_DIR = PREPROCESSOR_DIR / "sample_files" / "drill"
CONFIG_DIR = SCRIPT_DIR / "configs"

if str(PARSE_CHUNK_DIR) not in sys.path:
    sys.path.insert(0, str(PARSE_CHUNK_DIR))

import parse_chunk_verify as verify  # noqa: E402  (DYLD 환경도 여기서 세워진다)

# (키, 픽스처, doc_type, 드릴 설정 파일, 노리는 변형, 계획의 예상)
CASES = [
    ("b1",  "b1_name_collision.json", "drill_b1",  "custom_field_drill_b1.yaml",
     "동명 키 충돌 — 필요한 것은 meta.title", "설정 불가 → 코드"),
    ("b2",  "b2_dynamic_keys.json",   "drill_b2",  "custom_field_drill_b2.yaml",
     "동적 키(상품코드가 key)", "json_semantic 이면 설정 0줄"),
    ("b3",  "b3_nested_records.json", "drill_b3",  "custom_field_drill_b3.yaml",
     "2단 중첩 레코드 groups[].items[]", "records 이름 검색으로 될 수도"),
    ("b4",  "b4_conditional.json",    "drill_b4",  "custom_field_drill_b4.yaml",
     "형제 type 값에 따라 본문이 갈림", "배타적이면 template 로 우회"),
    ("b5",  "b5_type_drift.json",     "drill_b5",  "custom_field_drill_b5.yaml",
     "문자열/숫자/배열/null 혼재", "값 파이프라인으로 흡수"),
    ("b6",  "b6_html_table.json",     "drill_b6",  "custom_field_drill_b6.yaml",
     "표가 HTML 문자열", "transform: html_text 로 됨"),
    ("b7",  "b7_mixed.json",          "drill_b7",  "custom_field_drill_b7.yaml",
     "문서형 메타 + 레코드 배열 혼합", "레코드 밖 공통 필드가 문제"),
    ("b8",  "b8_deep_nesting.json",   "drill_b8",  "custom_field_drill_b8.yaml",
     "depth 8", "이름 검색이 버티는지"),
    ("b11_bom",   "b11_bom.json",     "drill_b11", "custom_field_drill_b11.yaml",
     "UTF-8 BOM", "코드 필요(1줄)"),
    ("b11_cp949", "b11_cp949.json",   "drill_b11", "custom_field_drill_b11.yaml",
     "CP949", "코드 필요(1줄)"),
    ("b12", "b12_feed.jsonl",         "drill_b12", "custom_field_drill_b12.yaml",
     "JSONL/NDJSON", "코드 필요"),
    ("b13", "b13_null_missing.json",  "drill_b13", "custom_field_drill_b13.yaml",
     "null 값과 결측 키", "default/require 상호작용"),
    ("b14", "b14_escaped_json.json",  "drill_b14", "custom_field_drill_b14.yaml",
     "이스케이프된 중첩 JSON", "transform: text 가 판별하는지"),
    ("b15", "b15_empty_records.json", "drill_b15", "custom_field_drill_b15.yaml",
     "records 는 있는데 요소 0", "에러 경로가 갈림"),
    ("b17", "b17_many_records.json",  "drill_b17", "custom_field_drill_b17.yaml",
     "레코드 5,000건", "비용·지연 축"),
]

# 설정에서 만들기로 선언한 목표필드가 청크에 실렸는지 본다.
# (계획 4단계 "청커 산출까지 확인한다" — 신규 doc_type 은 골든이 없으므로
#  parse_chunk_verify 의 단정 방식을 빌린다.)
EXPECT_FIELDS = {
    "b1":  ["TITLE", "SUMMARY"],
    "b3":  ["TITLE", "BODY"],
    "b4":  ["SUBJECT", "BODY"],
    "b5":  ["NAME"],
    "b6":  ["CARD_NAME", "DETAIL_TEXT"],
    "b7":  ["QUESTION", "ANSWER"],
    "b11_bom": ["NAME", "BODY"],
    "b11_cp949": ["NAME", "BODY"],
    "b12": ["SUBJECT", "BODY"],
    "b13": ["NAME", "MEMO"],
    "b14": ["ITEM_NAME", "DETAIL_TEXT"],
    "b17": ["NAME", "BODY"],
}


def resource_dir() -> Path:
    dev = PREPROCESSOR_DIR / "resource_dev"
    return dev if dev.exists() else PREPROCESSOR_DIR / "resource"


def build_drill_config(work: Path, cases: list) -> Path:
    """배포 설정 사본에 드릴 설정만 덧붙여 등록한다.

    원본을 건드리지 않는 것이 중요하다 — B16(doc_type 충돌)이 보여주듯 등록 하나로
    기존 doc_type 을 죽일 수 있고, 그러면 골든이 통째로 흔들린다.
    """
    src = resource_dir()
    shutil.copytree(src, work / "resource")
    for cfg in CONFIG_DIR.glob("custom_field_drill_*.yaml"):
        shutil.copy(cfg, work / "resource" / cfg.name)

    parser_cfg = work / "resource" / "parser_processor_config.yaml"
    cfg = yaml.safe_load(parser_cfg.read_text(encoding="utf-8"))
    # B12: .jsonl 을 .json 으로 보게 한다. 확장자 자체는 설정 한 줄로 받을 수 있고,
    # 그 뒤 내용(줄 단위 JSON)이 설정으로 되는지가 진짜 시험 대상이다.
    cfg.setdefault("formats", {}).setdefault("extension_aliases", {})[".jsonl"] = ".json"
    enrichment = cfg.setdefault("enrichment", [])
    seen = set()
    for key, _fx, doc_type, cfg_file, _note, _expect in cases:
        if (doc_type, cfg_file) in seen:
            continue
        seen.add((doc_type, cfg_file))
        block = yaml.safe_load((CONFIG_DIR / cfg_file).read_text(encoding="utf-8"))
        kind = (block.get("source") or {}).get("kind", "records")
        extractor = {"records": "json_mapping", "sections": "json_semantic",
                     "rows": "tabular_mapping"}.get(kind, "json_mapping")
        enrichment.append({"custom_fields": {
            "enable": True, "doc_type": doc_type,
            "extractor": extractor, "config_file": cfg_file,
        }})
    parser_cfg.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")
    return parser_cfg


def run_case(python: str, fixture: Path, doc_type: str | None, out_dir: Path,
             config: Path | None) -> tuple[bool, str, str]:
    extra = ["--output-format", "json"]
    if config is not None:
        extra += ["--config", str(config)]
    return verify.run_case(python, doc_type, fixture, out_dir, extra)


def inspect(out_dir: Path, fixture: Path, expect_fields: list[str]) -> dict:
    chunks_path = out_dir / (fixture.stem + ".chunks.json")
    parse_path = out_dir / (fixture.stem + ".parse.json")
    info: dict = {"chunks": 0, "elements": 0, "categories": [], "missing": [], "sample": ""}
    if parse_path.exists():
        payload = json.loads(parse_path.read_text(encoding="utf-8"))
        elements = payload.get("elements") or []
        info["elements"] = len(elements)
        info["categories"] = sorted({e.get("category", "") for e in elements})
    if not chunks_path.exists():
        return info
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    info["chunks"] = len(chunks)
    if chunks:
        info["sample"] = (chunks[0].get("text") or "").replace("\n", " / ")[:160]
        for field in expect_fields:
            empty = [c for c in chunks if c.get(field) in (None, "", [], {})]
            if empty:
                info["missing"].append(f"{field} {len(empty)}/{len(chunks)}")
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None, help="케이스 키(b1 b4 …)")
    ap.add_argument("--step", choices=["raw", "config"], default="config",
                    help="raw=설정 없이 그대로 | config=드릴 설정으로(기본)")
    ap.add_argument("--out", default=None, help="산출 디렉터리(미지정 시 임시)")
    ap.add_argument("--keep", action="store_true", help="산출물을 지우지 않는다")
    ap.add_argument("--python", default=None, help="parse_chunk_test.py 실행 인터프리터")
    args = ap.parse_args()

    python = args.python or str(PREPROCESSOR_DIR / ".venv" / "bin" / "python")
    cases = [c for c in CASES if not args.only or c[0] in args.only]
    if not cases:
        print(f"--only {args.only} 에 해당하는 케이스가 없습니다.")
        return 2

    out_root = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="drill_"))
    out_root.mkdir(parents=True, exist_ok=True)
    work = out_root / "_config"
    config = None
    if args.step == "config":
        work.mkdir(parents=True, exist_ok=True)
        config = build_drill_config(work, cases)
        print(f"드릴 설정: {config}\n")

    rows = []
    for key, fixture_name, doc_type, _cfg_file, note, expected in cases:
        fixture = FIXTURE_DIR / fixture_name
        if not fixture.exists():
            rows.append((key, "SKIP", "픽스처 없음", "", note)); continue
        out_dir = out_root / key
        out_dir.mkdir(parents=True, exist_ok=True)
        dt = doc_type if args.step == "config" else None
        print(f"[{key}] {fixture_name} …", flush=True)
        ok, err, _output = run_case(python, fixture, dt, out_dir, config)
        if not ok:
            rows.append((key, "ERROR", err[:110], "", note)); continue
        info = inspect(out_dir, fixture, EXPECT_FIELDS.get(key, []))
        detail = (f"element {info['elements']}({','.join(info['categories']) or '-'}) "
                  f"/ chunk {info['chunks']}")
        if info["missing"]:
            status = "PARTIAL"
            detail += " / 빈 필드: " + ", ".join(info["missing"])
        elif info["chunks"] == 0:
            status = "EMPTY"
        else:
            status = "OK"
        rows.append((key, status, detail, info["sample"], note))

    width = max((len(r[0]) for r in rows), default=8)
    print()
    print(f"{'케이스'.ljust(width)}  {'결과':8s} 상세")
    print("-" * 100)
    for key, status, detail, sample, note in rows:
        print(f"{key.ljust(width)}  {status:8s} {detail}")
        print(f"{' '.ljust(width)}           변형: {note}")
        if sample:
            print(f"{' '.ljust(width)}           첫 청크: {sample}")
    print("-" * 100)
    ok_n = sum(1 for r in rows if r[1] == "OK")
    print(f"OK {ok_n} / 전체 {len(rows)}")

    if args.keep or args.out:
        print(f"산출물: {out_root}")
    else:
        shutil.rmtree(out_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
