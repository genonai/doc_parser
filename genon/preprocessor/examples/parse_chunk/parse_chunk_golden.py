#!/usr/bin/env python
"""파서/청커 산출의 골든 회귀 하네스.

parse_chunk_verify.py 가 "설정이 약속한 필드가 실렸는가" 를 단정한다면, 이쪽은
"리팩터링 전후로 산출이 한 글자라도 달라졌는가" 를 본다. 리팩터링의 1차 관문이다.

무엇을 고정하나
  - 파서 출력(<stem>.docling.json 또는 <stem>.parse.json)
  - 청커 출력(<stem>.chunks.json = 적재되는 벡터)
  둘 다 고정한다. 청크만 보면 파서 단계의 회귀가 청킹에서 상쇄돼 보일 수 있다.

출력 포맷 축
  parser 의 _build_docling_response 는 output.format 으로 분기한다. docling 만 돌리면
  parse-format 직렬화 경로(_docling_to_parse_format 계열)가 한 번도 실행되지 않으므로
  케이스마다 docling / json 두 벌을 기록한다.

모드
  --record  전체 케이스를 실행해 골든으로 저장
  --check   실행 후 골든과 대조. 차이가 있으면 비-0 종료
  --noise   같은 코드로 2회 실행해 서로 대조(정규화 규칙 점검용)

사용:
    ./parse_chunk_golden.py --record
    ./parse_chunk_golden.py --check
    ./parse_chunk_golden.py --noise
    ./parse_chunk_golden.py --check --only cs_hpp product_hpp
    ./parse_chunk_golden.py --record --cases my_cases.yaml --golden ~/my_golden

케이스 목록은 --cases 로 외부에서 주입할 수 있다(미지정 시 아래 GOLDEN_CASES).
고객은 자기 문서 목록으로 --record 해서 자기 골든을 만든다 — 벤더 골든은 배포되지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# parse_chunk_verify 는 케이스 목록·설정 해석·실행기를 모두 갖고 있다. 케이스를 두 곳에
# 적으면 곧 갈라지므로 여기서는 그것을 가져다 쓴다. import 만으로 DYLD 환경도 세워진다.
import golden_normalize as gn  # noqa: E402
import parse_chunk_verify as verify  # noqa: E402

PREPROCESSOR_DIR = verify.PREPROCESSOR_DIR
SAMPLES = verify.SAMPLES

DEFAULT_FORMATS = ("docling", "json")

# 골든 전용 추가 케이스. verify 의 CASES 는 custom_fields doc_type 검증용이라 포맷 분포가
# json/xlsx/html/md 에 몰려 있는데, 02(docling 런타임 이동)와 03c(직렬화 이동)가 건드리는
# 것은 정확히 PDF/docx/hwp 가 지나가는 경로다. doc_type 은 없다(None).
GOLDEN_EXTRA_CASES = [
    (None, SAMPLES / "docx_sample.docx", "MsWord 백엔드"),
    (None, SAMPLES / "tablecell.docx", "MsWord 백엔드 + 표 셀"),
    (None, SAMPLES / "html_tables.html", "표 직렬화 난이도"),
    (None, SAMPLES / "pdf_sample.pdf", "docling 런타임 주 경로(layout 엔드포인트 필요)"),
]

# 기본 목록에서 빼는 케이스. 넣으려면 --include-unstable 이고, 뺐다는 사실은 실행할 때마다
# 출력한다(조용한 SKIP 금지). 골든은 "차이 0" 이 판정 기준이라 흔들리는 케이스가 하나라도
# 섞이면 회귀와 잡음을 구분할 수 없다.
UNSTABLE_CASES = [
    (None, SAMPLES / "hwp_sample_table.hwp",
     "머신 종속: 레거시 HWP 백엔드가 외부 실행파일 convtext 를 부른다. "
     "없으면 FileNotFoundError, hwp_sdk 가 있는 머신은 SDK 경로로 가서 결과가 달라진다"),
    (None, SAMPLES / "pptx_sample.pptx",
     "비결정적: PPT→PDF 변환본이 실행마다 달라져 layout VLM 캐시가 매번 miss 하고 "
     "(실측 17건) 산출이 흔들린다. docling PDF 경로는 pdf_sample.pdf 가 덮는다"),
]

# verify.CASES 중 골든에서 빼는 것. 머신 종속 경로(gitignore 된 개인 디렉터리)라
# 있는 머신과 없는 머신의 골든이 갈라진다.
EXCLUDED_SOURCES = {"card01.flat.html"}

_CACHE_SUMMARY_RE = re.compile(r"\[llm_cache\]\s+hit=(\d+)\s+miss=(\d+)\s+save_fail=(\d+)")


# ──────────────────────────────────────────────────────────────────────────────
# 케이스
# ──────────────────────────────────────────────────────────────────────────────

def builtin_cases(include_unstable: bool = False) -> list[dict]:
    cases: list[dict] = []
    for doc_type, src, note in verify.CASES:
        if src.name in EXCLUDED_SOURCES:
            continue
        cases.append({"doc_type": doc_type, "path": src, "note": note})
    for doc_type, src, note in GOLDEN_EXTRA_CASES:
        cases.append({"doc_type": doc_type, "path": src, "note": note})
    if include_unstable:
        for doc_type, src, note in UNSTABLE_CASES:
            cases.append({"doc_type": doc_type, "path": src, "note": note})
    return cases


def load_cases(path: Path | None, include_unstable: bool = False) -> list[dict]:
    """외부 케이스 주입. [{doc_type, path, note, formats}] 목록의 yaml/json."""
    if path is None:
        return builtin_cases(include_unstable)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"케이스 파일은 목록이어야 합니다: {path}")
    cases = []
    for entry in raw:
        if not isinstance(entry, dict) or "path" not in entry:
            raise SystemExit(f"케이스 항목에 path 가 없습니다: {entry!r}")
        src = Path(str(entry["path"])).expanduser()
        if not src.is_absolute():
            src = (path.parent / src).resolve()
        cases.append({
            "doc_type": entry.get("doc_type"),
            "path": src,
            "note": entry.get("note", ""),
            "formats": tuple(entry["formats"]) if entry.get("formats") else None,
        })
    return cases


def case_key(case: dict, fmt: str) -> str:
    """골든 안에서 케이스를 가리키는 경로. doc_type 이 없으면 _nodoctype 로 묶는다."""
    return f"{fmt}/{case['doc_type'] or '_nodoctype'}/{case['path'].name}"


# ──────────────────────────────────────────────────────────────────────────────
# 엔드포인트 사전 점검
# ──────────────────────────────────────────────────────────────────────────────

def _collect_urls(obj, out: set[str]) -> None:
    if isinstance(obj, dict):
        if obj.get("enable") is False:
            return
        for key, value in obj.items():
            if key in {"url", "endpoint", "ocr_endpoint"} and isinstance(value, str) and value.startswith("http"):
                out.add(value)
            else:
                _collect_urls(value, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_urls(item, out)


def probe_endpoints() -> dict[str, str]:
    """설정에 적힌 LLM/모델 엔드포인트가 닿는지 본다.

    HTTP 상태가 무엇이든(401/400 포함) 응답이 오면 도달로 본다. 인증 실패와 미도달을
    구분하는 것이 목적이 아니라 "사내망 밖에서 골든을 찍는" 사고를 막는 것이 목적이다.
    """
    resource_dir = verify.RESOURCE_DIR
    urls: set[str] = set()
    for name in ["parser_processor_config.yaml", *[p.name for p in sorted(resource_dir.glob("custom_field_*.yaml"))]]:
        path = resource_dir / name
        if not path.exists():
            continue
        try:
            _collect_urls(yaml.safe_load(path.read_text(encoding="utf-8")), urls)
        except Exception as exc:  # 설정을 못 읽으면 점검 대상에서만 빠진다
            print(f"  [warn] 설정을 읽지 못했습니다: {path.name} ({exc})")
    result: dict[str, str] = {}
    for url in sorted(urls):
        req = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result[url] = f"http {resp.status}"
        except urllib.error.HTTPError as exc:
            result[url] = f"http {exc.code}"
        except Exception as exc:
            result[url] = f"unreachable: {type(exc).__name__}"
    return result


# ──────────────────────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────────────────────────────

def cache_extra_args(cache_root: Path, run_id: str) -> list[str]:
    return ["--llm_cache", "--interim_root", str(cache_root),
            "--workflow_id", "golden", "--run_id", run_id]


def run_one(python: str, case: dict, fmt: str, out_root: Path, cache_root: Path,
            run_id: str) -> dict:
    """케이스 1건을 한 포맷으로 실행하고 산출 파일 경로와 실행 사실을 돌려준다."""
    out_dir = out_root / fmt / (case["doc_type"] or "_nodoctype")
    out_dir.mkdir(parents=True, exist_ok=True)
    extra = ["--output-format", fmt, *cache_extra_args(cache_root, run_id)]
    ok, err, output = verify.run_case(python, case["doc_type"], case["path"], out_dir, extra)
    hits = misses = 0
    for hit, miss, _save_fail in _CACHE_SUMMARY_RE.findall(output):
        hits += int(hit)
        misses += int(miss)
    artifacts = {}
    if ok:
        stem = case["path"].stem
        for suffix in (".docling.json", ".parse.json", ".chunks.json"):
            path = out_dir / (stem + suffix)
            if path.exists():
                artifacts[suffix] = path
    return {"ok": ok, "err": err, "artifacts": artifacts,
            "cache_hits": hits, "cache_misses": misses}


def read_artifacts(artifacts: dict[str, Path]) -> dict[str, object]:
    return {suffix: json.loads(path.read_text(encoding="utf-8"))
            for suffix, path in artifacts.items()}


def run_all(python: str, cases: list[dict], formats: tuple[str, ...], out_root: Path,
            cache_root: Path, run_id: str) -> dict[str, dict]:
    """전체 케이스 × 포맷 실행. 반환은 case_key → 실행 결과."""
    results: dict[str, dict] = {}
    total = len(cases) * len(formats)
    idx = 0
    for case in cases:
        case_formats = case.get("formats") or formats
        for fmt in formats:
            idx += 1
            key = case_key(case, fmt)
            if fmt not in case_formats:
                results[key] = {"status": "SKIP", "reason": "케이스가 이 포맷을 쓰지 않음"}
                continue
            if not case["path"].exists():
                results[key] = {"status": "SKIP", "reason": f"샘플 없음: {case['path']}"}
                print(f"[{idx}/{total}] {key} … SKIP (샘플 없음)")
                continue
            print(f"[{idx}/{total}] {key} …", flush=True)
            run = run_one(python, case, fmt, out_root, cache_root, run_id)
            if not run["ok"]:
                results[key] = {"status": "FAIL", "reason": run["err"]}
                print(f"    FAIL {run['err']}")
                continue
            if not run["artifacts"]:
                results[key] = {"status": "FAIL", "reason": "산출물 없음"}
                print("    FAIL 산출물 없음")
                continue
            results[key] = {
                "status": "OK",
                "data": read_artifacts(run["artifacts"]),
                "cache_hits": run["cache_hits"],
                "cache_misses": run["cache_misses"],
                "doc_type": case["doc_type"],
                "source": str(case["path"]),
            }
    return results


# ──────────────────────────────────────────────────────────────────────────────
# LLM null 비율
# ──────────────────────────────────────────────────────────────────────────────

def llm_null_rate(doc_type: str | None, source: Path, chunks: list) -> str:
    """청크에 실린 llm 필드 중 빈 값 비율. LLM 이 죽으면 soft-fail 로 조용히 null 이 된다."""
    if not doc_type or not chunks:
        return "-"
    try:
        blocks = _custom_field_blocks()
        block = verify.pick_block(blocks, doc_type, source.suffix)
        if block is None:
            return "-"
        _req, _const, llm = verify.expected_from_yaml(block)
        return verify.llm_null_rate(chunks, llm)
    except Exception:
        return "-"


_BLOCKS_CACHE: list[dict] | None = None


def _custom_field_blocks() -> list[dict]:
    global _BLOCKS_CACHE
    if _BLOCKS_CACHE is None:
        _BLOCKS_CACHE = verify.load_custom_field_blocks()
    return _BLOCKS_CACHE


def _null_rate_is_total(rate: str) -> bool:
    """'12/12' 처럼 전부 null 인가. LLM 경로가 통째로 죽었다는 신호다."""
    if "/" not in rate:
        return False
    nulls, total = rate.split("/", 1)
    return nulls.isdigit() and total.isdigit() and int(total) > 0 and nulls == total


# ──────────────────────────────────────────────────────────────────────────────
# 골든 입출력
# ──────────────────────────────────────────────────────────────────────────────

def golden_path(golden_dir: Path, key: str, suffix: str) -> Path:
    return golden_dir / key / f"artifact{suffix}"


def write_golden(golden_dir: Path, results: dict[str, dict], meta: dict) -> None:
    if golden_dir.exists():
        shutil.rmtree(golden_dir)
    golden_dir.mkdir(parents=True, exist_ok=True)
    for key, result in results.items():
        if result["status"] != "OK":
            continue
        for suffix, data in result["data"].items():
            path = golden_path(golden_dir, key, suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(gn.dumps(gn.normalize(data)) + "\n", encoding="utf-8")
    (golden_dir / "golden_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_golden(golden_dir: Path, key: str) -> dict[str, str] | None:
    case_dir = golden_dir / key
    if not case_dir.is_dir():
        return None
    return {p.name.replace("artifact", "", 1): p.read_text(encoding="utf-8")
            for p in sorted(case_dir.glob("artifact.*"))}


# ──────────────────────────────────────────────────────────────────────────────
# 모드
# ──────────────────────────────────────────────────────────────────────────────

def build_meta(results: dict[str, dict], endpoints: dict[str, str]) -> dict:
    cases_meta = {}
    for key, result in results.items():
        if result["status"] != "OK":
            cases_meta[key] = {"status": result["status"], "reason": result.get("reason", "")}
            continue
        chunks = result["data"].get(".chunks.json") or []
        cases_meta[key] = {
            "status": "OK",
            "chunks": len(chunks),
            "llm_null_rate": llm_null_rate(result["doc_type"], Path(result["source"]), chunks),
            "cache_hits": result["cache_hits"],
            "cache_misses": result["cache_misses"],
        }
    return {"endpoints": endpoints, "cases": cases_meta}


def mode_record(args, cases, formats, out_root, cache_root, golden_dir) -> int:
    print("엔드포인트 사전 점검")
    endpoints = probe_endpoints()
    for url, status in endpoints.items():
        print(f"  {status:24s} {url}")
    unreachable = [u for u, s in endpoints.items() if s.startswith("unreachable")]
    if unreachable and not args.allow_offline:
        print("\n닿지 않는 엔드포인트가 있습니다. 이 상태로 찍은 골든은 LLM 경로가 "
              "빈 값으로 고정돼 회귀를 못 잡습니다.")
        print("사내망에서 다시 돌리거나, 그 사실을 알고도 찍으려면 --allow-offline 를 줍니다.")
        return 2

    results = run_all(args.python, cases, formats, out_root, cache_root, "base")
    failed = [k for k, r in results.items() if r["status"] == "FAIL"]
    meta = build_meta(results, endpoints)

    total_null = [k for k, m in meta["cases"].items()
                  if m.get("status") == "OK" and _null_rate_is_total(m.get("llm_null_rate", "-"))]
    if total_null and not args.allow_llm_nulls:
        print("\nllm 필드가 전부 비어 있는 케이스가 있습니다 — LLM 경로가 검증되지 않은 채 "
              "골든이 찍힙니다:")
        for key in total_null:
            print(f"  {key}  llm_null={meta['cases'][key]['llm_null_rate']}")
        print("알고도 찍으려면 --allow-llm-nulls 를 줍니다.")
        return 2

    write_golden(golden_dir, results, meta)
    print_summary(meta, failed)
    print(f"\n골든 저장: {golden_dir}")
    return 1 if failed else 0


def mode_check(args, cases, formats, out_root, cache_root, golden_dir) -> int:
    if not (golden_dir / "golden_meta.json").exists():
        print(f"골든이 없습니다: {golden_dir}\n먼저 --record 로 기준선을 찍습니다.")
        return 2
    base_meta = json.loads((golden_dir / "golden_meta.json").read_text(encoding="utf-8"))

    results = run_all(args.python, cases, formats, out_root, cache_root, "base")
    meta = build_meta(results, base_meta.get("endpoints", {}))

    diffs, missing, failed = [], [], []
    for key, result in results.items():
        if result["status"] == "SKIP":
            continue
        if result["status"] == "FAIL":
            failed.append((key, result["reason"]))
            continue
        golden = read_golden(golden_dir, key)
        if golden is None:
            missing.append(key)
            continue
        for suffix, data in result["data"].items():
            expected = golden.get(suffix)
            actual = gn.dumps(gn.normalize(data)) + "\n"
            if expected is None:
                diffs.append((f"{key}{suffix}", ["골든에 이 산출물이 없습니다"]))
            elif expected != actual:
                diffs.append((f"{key}{suffix}", _unified(expected, actual, key, suffix)))
        for suffix in golden:
            if suffix not in result["data"]:
                diffs.append((f"{key}{suffix}", ["현재 실행이 이 산출물을 만들지 않았습니다"]))

    print()
    for key, lines in diffs:
        print(f"DIFF {key}")
        for line in lines[: args.diff_lines]:
            print(f"    {line}")
        if len(lines) > args.diff_lines:
            print(f"    … ({len(lines) - args.diff_lines}줄 더)")
    for key, reason in failed:
        print(f"FAIL {key}  {reason}")
    for key in missing:
        print(f"MISS {key}  골든에 없는 케이스(골든을 다시 찍어야 합니다)")

    print_summary(meta, [k for k, _ in failed])
    warn_meta_drift(base_meta, meta)
    misses = sum(m.get("cache_misses", 0) for m in meta["cases"].values() if isinstance(m, dict))
    if misses:
        print(f"LLM 재호출 발생: 캐시 miss {misses}건 — 파서 산출 변화가 프롬프트를 바꿨을 수 "
              f"있습니다(차이의 일부는 회귀가 아니라 재호출 결과일 수 있음)")
    if diffs or failed or missing:
        print(f"\n결과: 차이 {len(diffs)} / 실패 {len(failed)} / 골든없음 {len(missing)}")
        return 1
    print("\n결과: 차이 0")
    return 0


def mode_noise(args, cases, formats, out_root, cache_root, _golden_dir) -> int:
    # 두 회차가 같은 캐시 스코프(run_id="base")를 쓴다 — record/check 와 같은 조건이다.
    # 회차마다 스코프를 나누면 2회차가 LLM 을 다시 불러 그 답의 흔들림까지 노이즈로 잡히는데,
    # 골든 대조는 캐시를 켜고 도는 것이 전제라 그것은 측정 대상이 아니다.
    print("1회차")
    run_a = run_all(args.python, cases, formats, out_root / "run_a", cache_root, "base")
    print("\n2회차")
    run_b = run_all(args.python, cases, formats, out_root / "run_b", cache_root, "base")

    raw_diff, norm_diff, candidates = [], [], {}
    for key, a in run_a.items():
        b = run_b.get(key)
        if a["status"] != "OK" or not b or b["status"] != "OK":
            continue
        for suffix, data_a in a["data"].items():
            data_b = b["data"].get(suffix)
            if data_b is None:
                norm_diff.append(f"{key}{suffix} (2회차 산출 없음)")
                continue
            if gn.dumps(data_a) != gn.dumps(data_b):
                raw_diff.append(f"{key}{suffix}")
                for name in gn.noise_candidates(data_a, data_b):
                    leaf = name.split(" ", 1)[0]
                    candidates[leaf] = candidates.get(leaf, 0) + 1
            if gn.dumps(gn.normalize(data_a)) != gn.dumps(gn.normalize(data_b)):
                lines = gn.diff_lines(gn.normalize(data_a), gn.normalize(data_b),
                                      "run_a", "run_b")
                norm_diff.append(f"{key}{suffix}")
                for line in lines[: args.diff_lines]:
                    print(f"    {line}")

    print("\n정규화 전 차이가 난 산출물: " + (", ".join(raw_diff) if raw_diff else "없음"))
    print("정규화 대상 후보(실측): " +
          (", ".join(f"{k}({v})" for k, v in sorted(candidates.items(), key=lambda kv: -kv[1]))
           if candidates else "없음"))
    print(f"현재 VOLATILE_FIELDS: {gn.VOLATILE_FIELDS}")
    if norm_diff:
        print("\n정규화 후에도 남은 차이 — 이대로 골든을 찍으면 회귀와 잡음을 구분할 수 없습니다:")
        for item in norm_diff:
            print(f"  {item}")
        return 1
    print("\n정규화 후 차이 0 — 골든을 찍을 수 있습니다.")
    return 0


def _unified(expected: str, actual: str, key: str, suffix: str) -> list[str]:
    import difflib
    return [l for l in difflib.unified_diff(
        expected.splitlines(), actual.splitlines(),
        fromfile=f"golden/{key}{suffix}", tofile=f"current/{key}{suffix}", n=2, lineterm="")]


def warn_meta_drift(base: dict, current: dict) -> None:
    for key, cur in current["cases"].items():
        old = base["cases"].get(key)
        if not isinstance(old, dict) or not isinstance(cur, dict):
            continue
        if old.get("llm_null_rate", "-") != cur.get("llm_null_rate", "-"):
            print(f"WARN {key}  llm_null_rate {old.get('llm_null_rate')} → "
                  f"{cur.get('llm_null_rate')} (차이 0 이어도 LLM 경로가 달라졌습니다)")


def print_summary(meta: dict, failed: list[str]) -> None:
    ok = [k for k, m in meta["cases"].items() if isinstance(m, dict) and m.get("status") == "OK"]
    skipped = [k for k, m in meta["cases"].items()
               if isinstance(m, dict) and m.get("status") == "SKIP"]
    print(f"\n실행 {len(ok)} / 실패 {len(failed)} / 건너뜀 {len(skipped)}")
    for key in skipped:
        print(f"  SKIP {key}  {meta['cases'][key].get('reason', '')}")


# ──────────────────────────────────────────────────────────────────────────────

def default_golden_dir() -> Path:
    env = os.environ.get("PARSE_CHUNK_GOLDEN_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "doc_parser" / "parse_chunk_golden"


def default_python() -> str:
    candidate = PREPROCESSOR_DIR / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", action="store_true", help="골든 기록")
    mode.add_argument("--check", action="store_true", help="골든과 대조")
    mode.add_argument("--noise", action="store_true", help="2회 실행해 서로 대조")
    ap.add_argument("--cases", default=None, help="케이스 목록 파일(yaml/json). 미지정 시 내장 목록")
    ap.add_argument("--only", nargs="*", default=None, help="doc_type 으로 케이스를 거른다")
    ap.add_argument("--golden", default=None, help=f"골든 디렉터리(기본 {default_golden_dir()})")
    ap.add_argument("--out", default=None, help="실행 산출 디렉터리(기본 골든 옆 _runs)")
    ap.add_argument("--formats", nargs="*", default=list(DEFAULT_FORMATS),
                    help="파서 출력 포맷 축(기본 docling json)")
    ap.add_argument("--python", default=default_python(), help="parse_chunk_test.py 실행 인터프리터")
    ap.add_argument("--diff-lines", type=int, default=40, help="차이 출력 줄수 상한")
    ap.add_argument("--include-unstable", action="store_true",
                    help="기본 제외된 흔들리는 케이스(hwp·pptx)도 포함한다")
    ap.add_argument("--allow-offline", action="store_true",
                    help="--record: 엔드포인트가 닿지 않아도 진행")
    ap.add_argument("--allow-llm-nulls", action="store_true",
                    help="--record: llm 필드가 전부 비어도 진행")
    args = ap.parse_args()

    cases = load_cases(Path(args.cases).expanduser() if args.cases else None,
                       args.include_unstable)
    if not args.cases and not args.include_unstable:
        for _dt, src, note in UNSTABLE_CASES:
            print(f"제외: {src.name} — {note}")
    if args.only:
        cases = [c for c in cases if c["doc_type"] in args.only]
        if not cases:
            print(f"--only {args.only} 에 해당하는 케이스가 없습니다.")
            return 2
    formats = tuple(args.formats)

    golden_dir = Path(args.golden).expanduser() if args.golden else default_golden_dir()
    out_root = Path(args.out).expanduser() if args.out else golden_dir.parent / "_runs"
    cache_root = golden_dir.parent / "_llm_cache"
    for path in (out_root, cache_root):
        path.mkdir(parents=True, exist_ok=True)

    if args.record:
        return mode_record(args, cases, formats, out_root / "record", cache_root, golden_dir)
    if args.check:
        return mode_check(args, cases, formats, out_root / "check", cache_root, golden_dir)
    return mode_noise(args, cases, formats, out_root / "noise", cache_root, golden_dir)


if __name__ == "__main__":
    raise SystemExit(main())
