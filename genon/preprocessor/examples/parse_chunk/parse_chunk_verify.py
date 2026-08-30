#!/usr/bin/env python
"""custom_fields doc_type 별 파싱→청킹 자동 검증.

parse_chunk_test.sh 는 손으로 돌려보는 놀이터고, 이 스크립트는 "항상 돌리는" 검증이다.
doc_type 마다 샘플을 파싱·청킹한 뒤, 그 doc_type 의 custom_field yaml 이 약속한 것이
실제 청크에 실렸는지 단정한다.

무엇을 단정하나
  - 청크가 1개 이상 나온다
  - 모든 청크에 doc_type 스탬프가 있다
  - yaml 의 required(필수 목표필드)가 모든 청크에 있고 비어 있지 않다
  - yaml 의 constants 값이 청크에 그대로 실렸다
  - llm_fields / output_fields 는 실패해도 통과로 본다(on_error:null 정책). 다만
    null 비율을 리포트해 모델서빙이 죽었는지 눈에 보이게 한다.
  - 케이스별 추가 단정(EXTRA_CHECKS): 위 공통 규칙으로는 잡히지 않는 회귀를 고정한다.

doc_type → extractor/config 매핑은 resource_dev/parser_processor_config.yaml 에서
직접 읽는다. 설정이 늘어나면 이 스크립트를 고치지 않아도 따라간다.

사용:
    python parse_chunk_verify.py                 # 전체
    python parse_chunk_verify.py --only faq menu # 일부 doc_type 만
    python parse_chunk_verify.py --keep          # 산출물 보존(기본은 임시 디렉터리)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
PREPROCESSOR_DIR = SCRIPT_DIR.parents[1]
RESOURCE_DIR = PREPROCESSOR_DIR / "resource_dev"
REPO_ROOT = PREPROCESSOR_DIR.parents[1]
SAMPLES = PREPROCESSOR_DIR / "sample_files"
MONIMO = SAMPLES / "monimo"

# (doc_type, 샘플 경로, 비고). 샘플이 없으면 SKIP 으로 처리한다.
# parse_chunk_test.sh 의 MONIMO_CASES 와 같은 목록이며, 저장소 안에 있는 샘플만 쓴다.
CASES = [
    ("menu",          MONIMO / "monimo_menu_sample.xlsx",              "행 1개 = 청크 1개"),
    ("term",          MONIMO / "monimo_term_sample.xlsx",              "행 1개 = 청크 1개"),
    ("faq",           MONIMO / "monimo_faq_sample.xlsx",               "tabular_mapping"),
    ("faq",           MONIMO / "monimo_faq_json_sample.json",          "json_mapping"),
    ("monimo_event",  SAMPLES / "json" / "monimo_event_sample.json",   "협의용 한글 키 표기"),
    ("monimo_event",  MONIMO / "monimo_event_real_sample.json",        "실 payload 스키마"),
    ("monimo_event",  MONIMO / "monimo_event_table_sample.json",       "5열 표 빈 셀 보존"),
    ("monimo_news",   MONIMO / "monimo_news_sample.json",              "json_mapping"),
    ("cs_slf",        MONIMO / "monimo_cs_slf_sample.xlsx",            "tabular_mapping"),
    ("cs_ssf",        MONIMO / "monimo_cs_ssf_sample.xlsx",            "tabular_mapping"),
    ("cs_sss",        MONIMO / "monimo_cs_sss_sample.json",            "json_mapping"),
    ("cs_hpp",        MONIMO / "monimo_cs_hpp_sample.html",            "llm(문서 단위)"),
    # 파일명이 점으로 시작하고 본문이 fragment 인 실 원천(#349 재현). rename 금지.
    ("cs_hpp",        MONIMO / ".INC_235488_02_20260626103138.html",   "section 1개"),
    ("cs_hpp",        MONIMO / ".INC_235489_01_20260626103139.html",   "section 3개"),
    ("product_slf",   MONIMO / "monimo_product_slf_sample.md",         "llm + markdown front matter"),
    ("product_ssf",   MONIMO / "monimo_product_ssf_sample.md",         "llm + markdown front matter"),
    ("product_hpp",   MONIMO / "monimo_product_hpp_wcms_sample.json",  "json_semantic(풀 캡처)"),
    ("product_hpp",   MONIMO / "monimo_product_hpp_sample.json",       "json_semantic(최소)"),
    ("stock_insight", MONIMO / "monimo_stock_insight_sample.xlsx",     "tabular_mapping"),
    ("link",          MONIMO / "monimo_link_sample.json",              "json_mapping"),
    # 개인 작업 디렉터리(gitignore)에 있는 실 원천. 없는 머신에서는 SKIP 된다.
    ("card",          REPO_ROOT / "shkim_labs" / "20260803_monimo" / "01_card" / "card01.flat.html",
                                                                      "llm(카드 12필드)"),
]


def check_front_matter(chunks: list) -> list[str]:
    """markdown front matter 승격/제외 회귀(#360).

    front_matter 블록은 document_type/source_file/source_pages/author/created_at 만
    metadata 로 올리고 front matter 전체는 청크 텍스트에서 뺀다. front matter 만으로
    이루어진 청크가 사라져 8청크 → 7청크가 된다. LLM 이 죽어도 이 단정은 유지된다.
    """
    problems = []
    c = chunks[0]
    if len(chunks) != 7:
        problems.append(f"청크 7건 기대, 실제 {len(chunks)}건(front matter 청크 제거 실패)")
    if c.get("source_pages") != 9:
        problems.append(f"source_pages int 보존 실패: {c.get('source_pages')!r}")
    if c.get("created_date") != 20260112:
        problems.append(f"created_at → date_int transform 실패: {c.get('created_date')!r}")
    leaked = [i for i, x in enumerate(chunks)
              if "conversion_note" in (x.get("text") or "") or "source_file:" in (x.get("text") or "")]
    if leaked:
        problems.append(f"front matter 가 청크 텍스트에 남음 {len(leaked)}/{len(chunks)}건")
    return problems


def check_card_annual_fee(chunks: list) -> list[str]:
    """front matter 타입 보존은 front matter 키에만 적용된다.

    card 의 annual_fee_amount 는 예전대로 문자열 '18000' 이어야 한다
    (이미 적재된 컬렉션의 property 타입 호환).
    """
    got = chunks[0].get("annual_fee_amount")
    if got != "18000":
        return [f"annual_fee_amount 문자열 '18000' 기대, 실제 {got!r}"]
    return []


# (doc_type, 샘플 파일명) → 추가 단정 함수
EXTRA_CHECKS = {
    ("product_slf", "monimo_product_slf_sample.md"): check_front_matter,
    ("card", "card01.flat.html"): check_card_annual_fee,
}

# 입력 확장자로 extractor 를 고른다. 같은 doc_type 에 블록이 둘인 경우가 있다
# (faq: xlsx→tabular_mapping / json→json_mapping, product_hpp: md→llm / json→json_semantic).
EXTRACTOR_BY_SUFFIX = {
    ".xlsx": {"tabular_mapping"},
    ".csv": {"tabular_mapping"},
    ".json": {"json_mapping", "json_semantic"},
    ".md": {"llm"},
    ".html": {"llm"},
    ".htm": {"llm"},
}


def load_custom_field_blocks() -> list[dict]:
    """parser_processor_config.yaml 의 enable 된 custom_fields 블록 목록."""
    cfg = yaml.safe_load((RESOURCE_DIR / "parser_processor_config.yaml").read_text(encoding="utf-8"))
    out = []
    for item in cfg.get("enrichment") or []:
        if not isinstance(item, dict):
            continue
        block = item.get("custom_fields")
        for b in (block if isinstance(block, list) else [block] if block else []):
            if isinstance(b, dict) and b.get("enable") and b.get("doc_type"):
                out.append(b)
    return out


def pick_block(blocks: list[dict], doc_type: str, suffix: str) -> dict | None:
    cands = [b for b in blocks if b.get("doc_type") == doc_type]
    if len(cands) <= 1:
        return cands[0] if cands else None
    allowed = EXTRACTOR_BY_SUFFIX.get(suffix.lower(), set())
    narrowed = [b for b in cands if b.get("extractor") in allowed]
    return narrowed[0] if narrowed else cands[0]


def expected_from_yaml(block: dict) -> tuple[list[str], dict, list[str]]:
    """(필수 필드, 상수 필드, LLM 산출 필드)."""
    path = RESOURCE_DIR / str(block.get("config_file") or "")
    if not path.exists():
        return [], {}, []
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    required = list(spec.get("required") or spec.get("required_shared_fields") or [])
    constants = dict(spec.get("constants") or {})
    llm: list[str] = []
    # llm_fields 는 블록 목록이고 각 블록이 output_fields 를 갖는다.
    raw = spec.get("llm_fields")
    for blk in (raw if isinstance(raw, list) else [raw] if raw else []):
        if isinstance(blk, dict):
            llm += list(blk.get("output_fields") or [])
    # llm extractor(card/cs_hpp/product_*) 는 최상위 output_fields 를 쓴다.
    if isinstance(spec.get("output_fields"), (list, dict)):
        llm += list(spec["output_fields"])
    return required, constants, [f for f in dict.fromkeys(llm) if isinstance(f, str)]


def run_case(python: str, doc_type: str, src: Path, out_dir: Path) -> tuple[bool, str]:
    cmd = [python, str(SCRIPT_DIR / "parse_chunk_test.py"),
           "--doc_type", doc_type, str(src), str(out_dir) + "/"]
    proc = subprocess.run(cmd, cwd=SCRIPT_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        return False, "실행 실패: " + " / ".join(tail)
    return True, ""


def verify(doc_type: str, src: Path, out_dir: Path, block: dict) -> list[str]:
    """실패 사유 목록(빈 목록이면 통과)."""
    problems: list[str] = []
    chunks_path = out_dir / (src.stem + ".chunks.json")
    if not chunks_path.exists():
        return [f"산출물 없음: {chunks_path.name}"]
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    if not chunks:
        return ["청크 0건"]

    stamped = [c for c in chunks if c.get("doc_type") == doc_type]
    if len(stamped) != len(chunks):
        problems.append(f"doc_type 스탬프 누락 {len(chunks)-len(stamped)}/{len(chunks)}건")

    required, constants, _llm = expected_from_yaml(block)
    for field in required:
        missing = [i for i, c in enumerate(chunks)
                   if c.get(field) in (None, "", [], {})]
        if missing:
            problems.append(f"required '{field}' 비어있음 {len(missing)}/{len(chunks)}건")
    for field, value in constants.items():
        bad = [i for i, c in enumerate(chunks) if c.get(field) != value]
        if bad:
            problems.append(f"constants '{field}' 불일치 {len(bad)}/{len(chunks)}건 "
                            f"(기대 {value!r}, 실제 {chunks[bad[0]].get(field)!r})")

    extra = EXTRA_CHECKS.get((doc_type, src.name))
    if extra is not None:
        problems += extra(chunks)
    return problems


def llm_null_rate(chunks: list, llm_fields: list[str]) -> str:
    if not llm_fields:
        return "-"
    total = len(chunks) * len(llm_fields)
    nulls = sum(1 for c in chunks for f in llm_fields if c.get(f) in (None, "", [], {}))
    return f"{nulls}/{total}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None, help="검증할 doc_type 만 지정")
    ap.add_argument("--keep", action="store_true", help="산출물을 지우지 않는다")
    ap.add_argument("--out", default=None, help="산출 디렉터리(미지정 시 임시 디렉터리)")
    ap.add_argument("--python", default=sys.executable, help="parse_chunk_test.py 실행 인터프리터")
    args = ap.parse_args()

    blocks = load_custom_field_blocks()
    out_root = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="parse_chunk_verify_"))
    out_root.mkdir(parents=True, exist_ok=True)

    cases = [c for c in CASES if not args.only or c[0] in args.only]
    rows, failed, skipped = [], 0, 0

    for doc_type, src, note in cases:
        label = f"{doc_type}:{src.name}"
        if not src.exists():
            rows.append((label, "SKIP", "샘플 없음", "-", "-")); skipped += 1; continue
        block = pick_block(blocks, doc_type, src.suffix)
        if block is None:
            rows.append((label, "SKIP", "설정에 doc_type 없음", "-", "-")); skipped += 1; continue

        out_dir = out_root / doc_type
        out_dir.mkdir(parents=True, exist_ok=True)
        ok, err = run_case(args.python, doc_type, src, out_dir)
        if not ok:
            rows.append((label, "FAIL", err, "-", "-")); failed += 1; continue

        problems = verify(doc_type, src, out_dir, block)
        chunks_path = out_dir / (src.stem + ".chunks.json")
        chunks = json.loads(chunks_path.read_text(encoding="utf-8")) if chunks_path.exists() else []
        _req, _const, llm = expected_from_yaml(block)
        n_chunks = str(len(chunks))
        nulls = llm_null_rate(chunks, llm)
        if problems:
            rows.append((label, "FAIL", "; ".join(problems), n_chunks, nulls)); failed += 1
        else:
            rows.append((label, "PASS", note, n_chunks, nulls))

    width = max((len(r[0]) for r in rows), default=20)
    print()
    print(f"{'케이스'.ljust(width)}  {'결과':6s} {'청크':>5s} {'LLM null':>9s}  비고")
    print("-" * (width + 40))
    for label, status, note, n, nulls in rows:
        print(f"{label.ljust(width)}  {status:6s} {n:>5s} {nulls:>9s}  {note}")
    print("-" * (width + 40))
    passed = sum(1 for r in rows if r[1] == "PASS")
    print(f"PASS {passed} / FAIL {failed} / SKIP {skipped}   (총 {len(rows)})")
    if not args.keep and args.out is None:
        shutil.rmtree(out_root, ignore_errors=True)
    else:
        print(f"산출물: {out_root}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
