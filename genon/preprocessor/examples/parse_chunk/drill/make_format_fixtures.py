#!/usr/bin/env python
"""xlsx / md / html 신규 문서 드릴 픽스처 생성 (#363 08-4).

06 은 JSON 만 다뤘다. 나머지 세 포맷에서 "설정으로 안 되는 원천" 을 고객이 훅으로
처리할 수 있는지 재려면 그런 원천이 있어야 한다. 실무에서 실제로 오는 형태만 만든다.

    ./make_format_fixtures.py        # sample_files/drill/ 에 생성
"""

from __future__ import annotations

import sys
from pathlib import Path

PREPROCESSOR_DIR = Path(__file__).resolve().parents[3]
OUT = PREPROCESSOR_DIR / "sample_files" / "drill"
sys.path.insert(0, str(PREPROCESSOR_DIR.parent.parent))
sys.path.insert(0, str(PREPROCESSOR_DIR))

from genon.preprocessor.converters import xlsx_processor as xp  # noqa: E402


def xlsx_fixtures() -> list[tuple[str, str]]:
    made = []

    # x1 — 위 두 줄이 로고·안내문이고 3행이 진짜 헤더. 관공서·사내 배포본에 흔하다.
    xp.sheets_to_xlsx({"지점현황": [
        ["(주)샘플 고객센터", "", ""],
        ["2026년 1월 기준", "", ""],
        ["지점명", "주소", "전화"],
        ["강남", "서울 강남구", "02-111-2222"],
        ["부산", "부산 해운대구", "051-333-4444"],
    ]}, str(OUT), "x1_junk_header.xlsx")
    made.append(("x1", "상단 2행이 로고·안내문, 3행이 진짜 헤더"))

    # x2 — 값만 정규화하면 되는 원천(전화번호 하이픈). **진짜 병합셀**이 있어야 한다.
    # sheets_to_xlsx 는 격자만 쓰므로 병합을 못 만든다 — 그래서 openpyxl 로 직접 만든다.
    # 훅이 격자를 값만 바꿔 돌려줬을 때 병합 기반 멀티헤더 판정이 살아남는지가 관건이다.
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "연락처"
    for row in (["연락처", None, "주소"], ["전화", "팩스", "시도"],
                ["02-111-2222", "02-111-3333", "서울"],
                ["051-333-4444", "051-333-5555", "부산"]):
        ws.append(row)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2)
    wb.save(str(OUT / "x2_value_only.xlsx"))
    made.append(("x2", "값만 정규화(하이픈 제거). 진짜 병합 헤더 유지 여부가 관건"))

    # x3 — 시트가 여럿이고 하나만 필요하다.
    xp.sheets_to_xlsx({
        "안내": [["이 파일은 내부용입니다", ""]],
        "본문": [["코드", "설명"], ["A01", "가입 안내"], ["A02", "해지 안내"]],
    }, str(OUT), "x3_multi_sheet.xlsx")
    made.append(("x3", "시트 2개 중 '본문' 만 필요"))
    return made


def md_fixtures() -> list[tuple[str, str]]:
    (OUT / "m1_marker_headings.md").write_text(
        "# 상품 안내\n\n"
        "■ 가입 자격\n만 19세 이상 개인\n\n"
        "■ 우대 금리\n급여 이체 시 연 0.3%p\n\n"
        "▶ 유의 사항\n중도 해지 시 약정 이율이 적용되지 않습니다.\n",
        encoding="utf-8")
    (OUT / "m2_text_fence.md").write_text(
        "# 약관 발췌\n\n"
        "```text\n"
        "제1조(목적) 이 약관은 회사와 고객 사이의 권리와 의무를 정함을\n"
        "목적으로 한다. 회사는 관계 법령을 준수하며 고객의 권익을\n"
        "보호하기 위하여 최선을 다한다.\n"
        "```\n",
        encoding="utf-8")
    return [("m1", "h태그 없이 ■ ▶ 마커로만 계층"),
            ("m2", "레이아웃 보존용 ```text 펜스(줄바꿈이 강제됨)")]


def html_fixtures() -> list[tuple[str, str]]:
    (OUT / "h1_srcdoc.html").write_text(
        "<html><body><h1>카드 안내</h1>"
        "<iframe srcdoc=\"&lt;p&gt;연회비는 국내전용 1만원입니다.&lt;/p&gt;"
        "&lt;p&gt;해외겸용은 1만 2천원입니다.&lt;/p&gt;\"></iframe>"
        "</body></html>", encoding="utf-8")
    (OUT / "h2_hidden_accordion.html").write_text(
        "<html><body><h1>자주 묻는 질문</h1>"
        "<div class='acc'><button>배송은 얼마나 걸리나요?</button>"
        "<div class='panel' style='display:none'>영업일 기준 2~3일 걸립니다.</div></div>"
        "</body></html>", encoding="utf-8")
    return [("h1", "iframe srcdoc 속성 안에 본문"),
            ("h2", "display:none 아코디언 안에 답변")]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for label, made in (("xlsx", xlsx_fixtures()), ("md", md_fixtures()), ("html", html_fixtures())):
        print(f"[{label}]")
        for key, note in made:
            print(f"  {key:<4} {note}")
    print(f"\n생성 위치: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
