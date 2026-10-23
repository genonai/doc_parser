#!/usr/bin/env python3
"""코드서빙 /parser + /chunker 동시 요청 테스트 (표준 라이브러리만 사용).

문서 7종 x (enrichment on / off) = 14 요청을 (1) 순차, (2) 동시(N 워커)로 보내고 요청 간 간섭을 본다.
간섭 지표:
  R1  off 요청에 이미지 설명(pictures[].annotations)·문서 요약(doc_summary)이 섞여 들어오면 토글이 덮인 것.
      on 요청이 순차 실행과 달리 설명·요약을 잃어도 같은 결함.
  R2  청크의 n_chunk_of_page 가 그 페이지의 실제 청크 수와 다르면 쪽별 청크 수가 다른 요청 값으로 덮인 것.
  시간  순차 합계 vs 동시 벽시계, 요청별 소요 시간.
  필드  매니페스트 잡(doc_type 별 custom_fields)은 청크 필드 집합이 순차 실행과 달라지면 doc_type 설정 간섭을 의심한다.
결과: --out 아래 results.json(요약), raw/<phase>/<job>.parse.json·.chunks.json(원 응답).
매니페스트: --manifest 로 doc_type 이 붙는 잡을 더한다(같은 폴더의 monimo_manifest.json 은 parse_chunk_verify 의 케이스 표).
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.request

DOCS = ["pdf_sample.pdf", "table.pdf", "pptx_sample.pptx", "hwp_sample.hwp",
        "hwpx_sample.hwpx", "docx_sample.docx", "png_sample.png"]


def build_jobs(docdir: str, extra: dict) -> list[dict]:
    jobs = []
    for f in DOCS:
        base, ext = f.rsplit(".", 1)
        for variant, flag in (("on", 1), ("off", 0)):
            jobs.append({
                "name": f"{base}__{variant}",
                "variant": variant,
                "file_path": f"{docdir}/{base}__{variant}.{ext}",
                "params": {"img_desc": flag, "table_desc": flag, "doc_summary": flag, **extra},
            })
    return jobs


def load_manifest(path: str, docdir: str) -> list[dict]:
    """매니페스트(JSON 배열: name, file, params)를 잡 목록으로 읽는다. file 이 상대 경로면 docdir 기준이다.
    on/off 변형이 없으므로 variant 는 "n/a" 이고 R1 토글 검사는 건너뛴다(doc_type 별 custom_fields 경로)."""
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    jobs = []
    for e in entries:
        fp = e["file"] if e["file"].startswith("/") else f"{docdir}/{e['file']}"
        jobs.append({"name": e["name"], "variant": "n/a", "file_path": fp, "params": dict(e.get("params") or {})})
    return jobs


def post_json(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"code": -1, "errMsg": f"HTTP {e.code}: {e.read()[:300]!r}"}
    except Exception as e:  # 타임아웃·연결 오류
        return {"code": -2, "errMsg": f"{type(e).__name__}: {e}"}


def find_key(obj, key: str):
    """중첩 dict/list 에서 key 의 첫 값을 찾는다(없으면 None)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def parse_metrics(data: dict) -> dict:
    doc = data.get("document") if isinstance(data, dict) else None
    pictures = (doc or {}).get("pictures", []) if isinstance(doc, dict) else []
    annotated = sum(1 for p in pictures if p.get("annotations"))
    tables = (doc or {}).get("tables", []) if isinstance(doc, dict) else []
    return {
        "pages": (data.get("usage") or {}).get("pages") if isinstance(data, dict) else None,
        "n_pictures": len(pictures),
        "n_pictures_annotated": annotated,
        "n_tables": len(tables),
        "has_doc_summary": bool(find_key(data, "doc_summary")),
        "kind": "docling" if isinstance(doc, dict) else "elements",
    }


def chunk_metrics(chunks) -> dict:
    if not isinstance(chunks, list):
        return {"n_chunks": None, "page_count_consistent": None}
    by_page = collections.defaultdict(list)
    for c in chunks:
        # parse-format(elements) 문서는 쪽 개념이 없어 n_chunk_of_page 가 0 으로 온다. 그 청크는 검사 대상이 아니다.
        if isinstance(c, dict) and "i_page" in c and c.get("n_chunk_of_page"):
            by_page[c["i_page"]].append(c.get("n_chunk_of_page"))
    bad = {p: (sorted(set(v)), len(v)) for p, v in by_page.items() if set(v) != {len(v)}}
    keys = sorted({k for c in chunks if isinstance(c, dict) for k in c.keys()})
    return {"n_chunks": len(chunks), "page_count_consistent": not bad, "page_count_mismatch": bad or None,
            "chunk_keys": keys}


def run_job(base: str, job: dict, out_dir: str, chunk_size, timeout: float) -> dict:
    rec = {"name": job["name"], "variant": job["variant"], "file_path": job["file_path"]}
    t0 = time.time()
    rec["t_start"] = t0
    resp = post_json(f"{base}/parser", {"file_path": job["file_path"], "params": job["params"]}, timeout)
    rec["parse_elapsed"] = round(time.time() - t0, 2)
    rec["parse_code"] = resp.get("code")
    rec["parse_err"] = None if resp.get("code") == 0 else resp.get("errMsg")
    with open(os.path.join(out_dir, f"{job['name']}.parse.json"), "w", encoding="utf-8") as f:
        json.dump(resp, f, ensure_ascii=False)
    if resp.get("code") != 0:
        rec["t_end"] = time.time()
        return rec
    data = resp.get("data") or {}
    rec.update(parse_metrics(data))
    document = data.get("document") if isinstance(data.get("document"), dict) else data
    params = {"document": document}
    if chunk_size is not None:
        params["chunk_size"] = chunk_size
    t1 = time.time()
    cresp = post_json(f"{base}/chunker", {"file_path": job["file_path"], "params": params}, timeout)
    rec["chunk_elapsed"] = round(time.time() - t1, 2)
    rec["chunk_code"] = cresp.get("code")
    rec["chunk_err"] = None if cresp.get("code") == 0 else cresp.get("errMsg")
    with open(os.path.join(out_dir, f"{job['name']}.chunks.json"), "w", encoding="utf-8") as f:
        json.dump(cresp, f, ensure_ascii=False)
    if cresp.get("code") == 0:
        rec.update(chunk_metrics(cresp.get("data")))
    rec["t_end"] = time.time()
    return rec


def run_phase(base, jobs, out_root, phase, workers, chunk_size, timeout) -> dict:
    out_dir = os.path.join(out_root, "raw", phase)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    if workers <= 1:
        recs = [run_job(base, j, out_dir, chunk_size, timeout) for j in jobs]
    else:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            recs = list(ex.map(lambda j: run_job(base, j, out_dir, chunk_size, timeout), jobs))
    wall = round(time.time() - t0, 2)
    for r in recs:
        r["t_start"] = round(r["t_start"] - t0, 2)
        r["t_end"] = round(r["t_end"] - t0, 2)
    return {"phase": phase, "workers": workers, "wall": wall, "jobs": recs}


def interference(phase: dict, solo: dict | None) -> list[str]:
    """간섭 징후를 문장으로 모은다. solo 가 있으면 on 요청의 설명·요약 유무를 순차 결과와 비교한다."""
    issues = []
    ref = {r["name"]: r for r in (solo or {}).get("jobs", [])}
    for r in phase["jobs"]:
        if r.get("parse_code") != 0:
            issues.append(f"{r['name']}: parse 실패 {r.get('parse_err')}")
            continue
        if r["variant"] == "off" and (r.get("n_pictures_annotated") or r.get("has_doc_summary")):
            issues.append(f"{r['name']}: off 요청에 설명 {r.get('n_pictures_annotated')}건/요약 {r.get('has_doc_summary')} (R1 토글 누수)")
        s = ref.get(r["name"])
        # on 요청은 찾은 그림마다 설명이 붙고 요약이 있어야 한다. 그림 수 자체는 VLM 레이아웃 응답에 따라
        # 실행마다 달라질 수 있으므로 순차와 비교하지 않는다(설명 수가 그림 수보다 적을 때만 토글이 꺼진 것).
        if r["variant"] == "on" and ((r.get("n_pictures_annotated") or 0) < (r.get("n_pictures") or 0)
                                     or (s and s.get("has_doc_summary") and not r.get("has_doc_summary"))):
            issues.append(f"{r['name']}: on 요청인데 설명/요약이 빠짐 "
                          f"(그림 {r.get('n_pictures')} 중 설명 {r.get('n_pictures_annotated')}, 요약 {r.get('has_doc_summary')}) (R1)")
        if r.get("chunk_code") not in (None, 0):
            issues.append(f"{r['name']}: chunk 실패 {r.get('chunk_err')}")
        if r.get("page_count_consistent") is False:
            issues.append(f"{r['name']}: n_chunk_of_page 불일치 {r.get('page_count_mismatch')} (R2)")
        if s and s.get("n_chunks") is not None and r.get("n_chunks") != s.get("n_chunks"):
            issues.append(f"{r['name']}: 청크 수 순차 {s.get('n_chunks')} → 동시 {r.get('n_chunks')}")
        if s and s.get("chunk_keys") and r.get("chunk_keys") and s["chunk_keys"] != r["chunk_keys"]:
            issues.append(f"{r['name']}: 청크 필드 집합이 순차와 다름 (doc_type 설정 간섭 의심) "
                          f"{sorted(set(s['chunk_keys']) ^ set(r['chunk_keys']))}")
    return issues


def table(phase: dict) -> str:
    cols = ["name", "parse_code", "parse_elapsed", "pages", "n_pictures", "n_pictures_annotated", "has_doc_summary",
            "chunk_code", "chunk_elapsed", "n_chunks", "page_count_consistent", "t_start", "t_end"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in phase["jobs"]:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", required=True, help="예: http://code-serving-38-21:8080")
    p.add_argument("--docdir", required=True, help="서빙 컨테이너에서 보이는 문서 폴더(NFS)")
    p.add_argument("--out", required=True)
    p.add_argument("--phases", default="solo,concurrent")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--chunk-size", type=int, default=None, help="생략하면 서빙 config 값")
    p.add_argument("--timeout", type=float, default=1800)
    p.add_argument("--param", action="append", default=[], help="모든 요청에 더할 params KEY=VALUE (예: log_level=4)")
    p.add_argument("--manifest", default=None, help="추가 잡 매니페스트 JSON(name, file, params). 예: monimo_manifest.json")
    p.add_argument("--only-manifest", action="store_true", help="기본 문서 7종 x on/off 를 빼고 매니페스트만 보낸다")
    args = p.parse_args(argv)

    extra = {}
    for kv in args.param:
        k, _, v = kv.partition("=")
        try:
            extra[k] = int(v)
        except ValueError:
            extra[k] = v
    jobs = [] if args.only_manifest else build_jobs(args.docdir, extra)
    if args.manifest:
        for j in load_manifest(args.manifest, args.docdir):
            j["params"].update(extra)
            jobs.append(j)
    if not jobs:
        raise SystemExit("보낼 잡이 없습니다 (--only-manifest 에는 --manifest 가 필요합니다).")
    os.makedirs(args.out, exist_ok=True)

    with urllib.request.urlopen(f"{args.base}/version", timeout=30) as resp:
        version = resp.read().decode("utf-8")
    results = {"base": args.base, "version": json.loads(version), "extra_params": extra, "phases": {}}
    print(f"[version] {version.strip()}", flush=True)

    solo = None
    for phase in [s.strip() for s in args.phases.split(",") if s.strip()]:
        workers = 1 if phase == "solo" else args.workers
        print(f"[{phase}] start — {len(jobs)} jobs, workers={workers}", flush=True)
        res = run_phase(args.base, jobs, args.out, phase, workers, args.chunk_size, args.timeout)
        res["issues"] = interference(res, solo)
        res["sum_parse_elapsed"] = round(sum(r.get("parse_elapsed") or 0 for r in res["jobs"]), 2)
        results["phases"][phase] = res
        if phase == "solo":
            solo = res
        print(f"[{phase}] wall={res['wall']}s  parse 합계={res['sum_parse_elapsed']}s", flush=True)
        print(table(res), flush=True)
        print(f"[{phase}] 간섭 징후 {len(res['issues'])}건", flush=True)
        for i in res["issues"]:
            print("  - " + i, flush=True)
        with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
