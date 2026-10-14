#!/usr/bin/env python3
"""`GENOS_PROFILE=1` 로 돌린 워커 로그의 `[prof]` 줄을 표로 묶는다(#514, 표준 라이브러리만).

    kubectl -n <NS> logs <POD> --since=2h > worker.log
    python3 prof_report.py worker.log --from 21:44:20 --to 21:49:27      # 순차 구간
    python3 prof_report.py worker.log --from 21:49:27 --to 21:52:10      # 동시 구간

표 1 단계별(문서 × 단계): wall(벽시계) · proc_cpu(프로세스 CPU 증분) · wait(= wall - proc_cpu, 최소 0).
     proc_cpu 는 한 번에 한 문서만 돌 때(순차 구간)만 그 단계의 몫이다. 동시 구간에서는 wall 만 본다.
표 2 단계 합계: 단계마다 건수·wall 합·proc_cpu 합·wait 합. 순차 구간에서 "CPU 일" 대 "기다림" 의 비율.
표 3 외부 호출: 엔드포인트마다 건수·실패·지연 p50/p95/최대, 그리고 같은 엔드포인트 동시 호출 수 구간별 p50.
     동시 호출이 늘 때 p50 이 따라 늘면 그 서빙의 용량이 병목이다.
"""
from __future__ import annotations

import argparse
import collections
import re
import statistics
import sys

LINE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d)[,.]\d+ .*?\[prof\] (stage|call) (.*)$")
KV = re.compile(r"(\w+)=(\S+)")
BUCKETS = [(1, 1), (2, 3), (4, 7), (8, 15), (16, 10**9)]


def parse(paths, start, end):
    stages, calls = [], []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = LINE.match(line)
                if not m or (start and m.group(1) < start) or (end and m.group(1) > end):
                    continue
                kv = dict(KV.findall(m.group(3)))
                (stages if m.group(2) == "stage" else calls).append(kv)
    return stages, calls


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else 0.0


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def stage_tables(stages):
    per_doc = collections.OrderedDict()
    totals = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for s in stages:
        wall, cpu = float(s["wall"]), float(s["proc_cpu"])
        per_doc.setdefault(s["doc"], []).append((s["name"], wall, cpu))
        t = totals[s["name"]]
        t[0] += 1
        t[1] += wall
        t[2] += cpu
    rows = []
    for doc, items in per_doc.items():
        cells = " · ".join(f"{n} {w:.1f}/{c:.1f}" for n, w, c in items)
        rows.append((doc, cells))
    t2 = [(n, c, f"{w:.1f}", f"{cpu:.1f}", f"{max(w - cpu, 0):.1f}")
          for n, (c, w, cpu) in sorted(totals.items(), key=lambda kv: -kv[1][1])]
    return (table(["문서", "단계 wall/proc_cpu(초)"], rows),
            table(["단계", "건수", "wall 합", "proc_cpu 합", "wait 합"], t2))


def call_table(calls):
    by_ep = collections.defaultdict(list)
    for c in calls:
        by_ep[c["ep"]].append(c)
    rows = []
    for ep, items in sorted(by_ep.items(), key=lambda kv: -len(kv[1])):
        walls = [float(c["wall"]) for c in items]
        bad = sum(1 for c in items if not str(c["status"]).startswith("2"))
        by_bucket = []
        for lo, hi in BUCKETS:
            sel = [float(c["wall"]) for c in items if lo <= int(c["inflight"]) <= hi]
            by_bucket.append(f"{statistics.median(sel):.2f} ({len(sel)})" if sel else "—")
        rows.append((ep, len(items), bad, f"{pct(walls, .5):.2f}", f"{pct(walls, .95):.2f}", f"{max(walls):.2f}",
                     *by_bucket))
    head = ["엔드포인트", "건수", "실패", "p50", "p95", "최대"] + [
        f"동시 {lo}" if lo == hi else (f"동시 {lo}~{hi}" if hi < 10**9 else f"동시 {lo}+") for lo, hi in BUCKETS]
    return table(head, rows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("logs", nargs="+")
    p.add_argument("--from", dest="start", default=None, help="HH:MM:SS (로그 시각, 포함)")
    p.add_argument("--to", dest="end", default=None, help="HH:MM:SS (로그 시각, 포함)")
    args = p.parse_args(argv)
    stages, calls = parse(args.logs, args.start, args.end)
    t1, t2 = stage_tables(stages)
    print(f"## 단계 ({len(stages)}줄)\n\n{t1}\n\n## 단계 합계\n\n{t2}\n\n## 외부 호출 ({len(calls)}건)\n\n{call_table(calls)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
