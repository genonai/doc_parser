#!/usr/bin/env python3
"""monimo_term_duplicate_rows_sample.xlsx 재생성 — rows 원천의 완전 중복 행 제거 검증용(#412).

용어사전 샘플(monimo_term_sample.xlsx)을 그대로 두고 데이터 행 2개를 덧붙인다.

1. SLF_0001 행을 모든 컬럼까지 그대로 한 번 더 넣는다. 원천이 같은 행을 다시 보낸 경우이며,
   병합 전에 빠져 청크·BIZ_ID 가 한 번만 생겨야 한다.
2. 대조군으로 HPP_0001 행을 최종수정일 하나만 바꿔 한 번 더 넣는다. 일부 컬럼만 같은 행은
   제거 대상이 아니므로 유지되어야 한다.

샘플이 다시 바뀌면 손으로 xlsx 를 만지지 말고 이 스크립트를 고쳐 다시 돌린다.

실행:  genon/preprocessor/.venv/bin/python tools/parse_chunk/make_term_duplicate_rows_sample.py
"""
from __future__ import annotations

from pathlib import Path

import openpyxl

MONIMO = Path(__file__).resolve().parents[2] / "sample_files" / "monimo"
SRC = MONIMO / "monimo_term_sample.xlsx"
OUT = MONIMO / "monimo_term_duplicate_rows_sample.xlsx"

CONTROL_MOD_DT = "2026-06-15"


def main() -> None:
    wb = openpyxl.load_workbook(SRC)
    ws = wb.active
    rows = {row[0]: list(row) for row in ws.iter_rows(values_only=True) if row and row[0]}
    header = rows["ID"]
    control = list(rows["HPP_0001"])
    control[header.index("최종수정일")] = CONTROL_MOD_DT
    ws.append(rows["SLF_0001"])
    ws.append(control)
    wb.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
