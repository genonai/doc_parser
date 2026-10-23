"""동시 처리 측정 공용 함수 — 잡 목록·지표·간섭 판정·표(#577).

#577 전에는 코드서빙 HTTP 측정 도구(concurrency_test.py)에 있었다. HTTP 업무 경로를 없애면서 워커 측정
도구(worker_concurrency_test.py)가 쓰는 부분만 여기로 옮겼다.

잡: 문서 7종 x (enrichment on / off) = 14건, 매니페스트(--manifest)로 doc_type 잡을 더한다.
간섭 지표:
  R1  off 잡에 이미지 설명(pictures[].annotations)·문서 요약(doc_summary)이 섞여 들어오면 토글이 덮인 것.
      on 잡이 순차 실행과 달리 설명·요약을 잃어도 같은 결함.
  R2  청크의 n_chunk_of_page 가 그 페이지의 실제 청크 수와 다르면 쪽별 청크 수가 다른 잡 값으로 덮인 것.
  필드  매니페스트 잡은 청크 필드 집합이 순차 실행과 달라지면 doc_type 설정 간섭을 의심한다.
"""
from __future__ import annotations

import collections
import json

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
