#!/usr/bin/env python
"""신규 문서 실전 드릴(#363 06) 픽스처 생성기.

보유 샘플과 **일부러 다른 구조**를 만든다. 실제 신규 원천에서 흔히 나오는 변형을
재현해 "설정으로 되는가 / 코드가 필요한가" 를 가른다.

손으로 만든 파일이 아니라 스크립트로 재생성 가능해야 축을 나중에 바꿀 수 있다
(examples/parse_chunk/make_*_sample.py 의 기존 관례).

사용:
    python make_drill_fixtures.py            # sample_files/drill/ 에 생성
    python make_drill_fixtures.py --out DIR
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR.parents[2] / "sample_files" / "drill"


def _write_json(path: Path, payload, *, encoding: str = "utf-8", bom: bool = False) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    data = text.encode(encoding)
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


# ──────────────────────────────────────────────────────────────────────────────
# B1 동명 키 충돌 — 필요한 것은 **깊은 쪽** title
# ──────────────────────────────────────────────────────────────────────────────
def b1_name_collision():
    return {
        "title": "2026년 상품 안내 모음",          # ← 파일 제목(얕음). 이걸 잡으면 안 된다
        "publishedAt": "2026-07-01",
        "items": [
            {
                # 레코드 안에도 title 이 있다. 얕은 쪽이 이기므로 이것이 잡힌다 —
                # 그런데 필요한 것은 meta.title 이다. 이것이 진짜 동명 키 충돌이다.
                "title": "목록 항목 1",
                "meta": {"title": "청년 우대 적금", "code": "P001"},
                "summary": "만 19~34세 대상 우대금리 적금",
            },
            {
                "title": "목록 항목 2",
                "meta": {"title": "직장인 신용대출", "code": "P002"},
                "summary": "재직 6개월 이상 대상 신용대출",
            },
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# B2 동적 키 — 상품코드가 key 다
# ──────────────────────────────────────────────────────────────────────────────
def b2_dynamic_keys():
    return {
        "P001": {"name": "청년 우대 적금", "rate": "연 3.5%", "target": "만 19~34세"},
        "P002": {"name": "직장인 신용대출", "rate": "연 5.1%", "target": "재직 6개월 이상"},
        "P003": {"name": "주택청약 종합저축", "rate": "연 2.8%", "target": "무주택 세대주"},
    }


# ──────────────────────────────────────────────────────────────────────────────
# B3 2단 중첩 레코드 — groups[].items[]
# ──────────────────────────────────────────────────────────────────────────────
def b3_nested_records():
    return {
        "groups": [
            {
                "groupName": "예금",
                "items": [
                    {"itemTitle": "정기예금 12개월", "itemBody": "만기 일시지급"},
                    {"itemTitle": "정기예금 24개월", "itemBody": "만기 일시지급"},
                ],
            },
            {
                "groupName": "대출",
                "items": [
                    {"itemTitle": "마이너스통장", "itemBody": "한도 내 자유입출금"},
                ],
            },
        ]
    }


# ──────────────────────────────────────────────────────────────────────────────
# B4 조건부 선택 — 형제 노드 type 값에 따라 본문 필드가 갈린다
# ──────────────────────────────────────────────────────────────────────────────
def b4_conditional():
    return {
        "noticeList": [
            {"type": "TEXT", "subject": "시스템 점검 안내",
             "plainBody": "7월 5일 02:00~04:00 점검", "htmlBody": None},
            {"type": "HTML", "subject": "여름 이벤트",
             "plainBody": None, "htmlBody": "<p>선착순 <b>1,000명</b></p>"},
            {"type": "TEXT", "subject": "약관 개정 안내",
             "plainBody": "8월 1일부터 적용됩니다", "htmlBody": None},
        ]
    }


# ──────────────────────────────────────────────────────────────────────────────
# B5 타입 흔들림 — 같은 필드가 문자열/숫자/배열/null
# ──────────────────────────────────────────────────────────────────────────────
def b5_type_drift():
    return {
        "rows": [
            {"name": "A상품", "fee": "18,000원", "tags": "적금"},
            {"name": "B상품", "fee": 25000, "tags": ["대출", "신용"]},
            {"name": "C상품", "fee": None, "tags": []},
            {"name": "D상품", "fee": "무료", "tags": "예금"},
        ]
    }


# ──────────────────────────────────────────────────────────────────────────────
# B6 표가 HTML 문자열로 박혀 있다
# ──────────────────────────────────────────────────────────────────────────────
def b6_html_table_in_string():
    table = (
        "<table><thead><tr><th>구분</th><th>연회비</th><th>적립</th></tr></thead>"
        "<tbody>"
        "<tr><td>국내전용</td><td>15,000원</td><td>0.5%</td></tr>"
        "<tr><td>해외겸용</td><td>18,000원</td><td>0.8%</td></tr>"
        "</tbody></table>"
    )
    return {
        "cards": [
            {"cardName": "生활 체크카드", "detail": table},
            {"cardName": "여행 신용카드",
             "detail": "<ul><li>공항라운지 연 2회</li><li>해외 이용 수수료 면제</li></ul>"},
        ]
    }


# ──────────────────────────────────────────────────────────────────────────────
# B7 문서형 + 레코드형 혼합 — 헤더 메타와 레코드 배열이 한 파일에
# ──────────────────────────────────────────────────────────────────────────────
def b7_mixed_doc_and_records():
    return {
        "documentTitle": "고객센터 FAQ 2026",
        "owner": "고객지원팀",
        "revision": "v3",
        "faqList": [
            {"q": "비밀번호를 잊었어요", "a": "앱 로그인 화면의 '비밀번호 찾기' 를 누르세요."},
            {"q": "카드를 분실했어요", "a": "고객센터로 즉시 신고하시면 사용이 정지됩니다."},
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# B8 깊은 중첩 — depth 8 (보유 최대 5)
# ──────────────────────────────────────────────────────────────────────────────
def b8_deep_nesting():
    leaf = {"leafTitle": "심층 항목", "leafBody": "여덟 겹 안쪽에 있는 본문"}
    node = leaf
    for name in ["lv7", "lv6", "lv5", "lv4", "lv3", "lv2", "lv1"]:
        node = {name: node}
    return node


# ──────────────────────────────────────────────────────────────────────────────
# B12 JSONL / NDJSON — 줄 단위 레코드 피드
# ──────────────────────────────────────────────────────────────────────────────
def b12_jsonl_lines():
    return [
        {"subject": "1월 소식", "body": "새해 인사드립니다"},
        {"subject": "2월 소식", "body": "설 연휴 운영 안내"},
        {"subject": "3월 소식", "body": "봄맞이 이벤트"},
    ]


# ──────────────────────────────────────────────────────────────────────────────
# B13 null·결측 키
# ──────────────────────────────────────────────────────────────────────────────
def b13_null_and_missing():
    return {
        "rows": [
            {"name": "완전한 건", "memo": "메모 있음", "state": "사용"},
            {"name": "메모 null", "memo": None, "state": "사용"},
            {"name": "메모 키 없음", "state": "사용"},
            {"memo": "이름이 없다", "state": "사용"},
        ]
    }


# ──────────────────────────────────────────────────────────────────────────────
# B14 이스케이프된 중첩 JSON — 값이 JSON 문자열이다
# ──────────────────────────────────────────────────────────────────────────────
def b14_escaped_json():
    inner = json.dumps({"limit": "월 30만원", "rate": "0.7%"}, ensure_ascii=False)
    return {
        "items": [
            {"itemName": "캐시백 카드", "detail": inner},
            {"itemName": "포인트 카드",
             "detail": json.dumps({"limit": "월 10만원", "rate": "1.2%"}, ensure_ascii=False)},
        ]
    }


# ──────────────────────────────────────────────────────────────────────────────
# B15 빈 배열 / 0건
# ──────────────────────────────────────────────────────────────────────────────
def b15_empty_records():
    return {"noticeList": []}


# ──────────────────────────────────────────────────────────────────────────────
# B17 대량 레코드 — 비용·지연 축
# ──────────────────────────────────────────────────────────────────────────────
def b17_many_records(n: int = 5000):
    return {
        "rows": [
            {"name": f"항목 {i:05d}", "body": f"{i} 번째 레코드의 본문입니다."}
            for i in range(1, n + 1)
        ]
    }


FIXTURES = [
    ("b1_name_collision.json", b1_name_collision, "동명 키 충돌 — 깊은 쪽 title 이 필요"),
    ("b2_dynamic_keys.json", b2_dynamic_keys, "동적 키 — 상품코드가 key"),
    ("b3_nested_records.json", b3_nested_records, "2단 중첩 레코드 groups[].items[]"),
    ("b4_conditional.json", b4_conditional, "형제 type 값에 따라 본문 필드가 갈림"),
    ("b5_type_drift.json", b5_type_drift, "같은 필드가 문자열/숫자/배열/null"),
    ("b6_html_table.json", b6_html_table_in_string, "표가 HTML 문자열로 박힘"),
    ("b7_mixed.json", b7_mixed_doc_and_records, "문서형 메타 + 레코드 배열 혼합"),
    ("b8_deep_nesting.json", b8_deep_nesting, "depth 8"),
    ("b13_null_missing.json", b13_null_and_missing, "null 값과 결측 키"),
    ("b14_escaped_json.json", b14_escaped_json, "값이 이스케이프된 JSON 문자열"),
    ("b15_empty_records.json", b15_empty_records, "records 는 있는데 요소 0"),
    # 약 500K 라 저장소에는 커밋하지 않는다(genon/.gitignore). 이 스크립트가 다시 만든다.
    ("b17_many_records.json", b17_many_records, "레코드 5,000건 (gitignore)"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"출력 디렉터리(기본 {DEFAULT_OUT})")
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    for name, builder, note in FIXTURES:
        _write_json(out / name, builder())
        print(f"  {name:28s} {note}")

    # B11 인코딩 — 같은 내용을 BOM / CP949 로 낸다
    payload = {"rows": [{"name": "인코딩 시험", "body": "한글이 깨지지 않아야 한다"}]}
    _write_json(out / "b11_bom.json", payload, bom=True)
    print(f"  {'b11_bom.json':28s} UTF-8 BOM")
    _write_json(out / "b11_cp949.json", payload, encoding="cp949")
    print(f"  {'b11_cp949.json':28s} CP949")

    # B12 JSONL — 줄 단위. 확장자는 .jsonl 이라 확장자 별칭도 함께 시험된다
    lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in b12_jsonl_lines())
    (out / "b12_feed.jsonl").write_text(lines + "\n", encoding="utf-8")
    print(f"  {'b12_feed.jsonl':28s} JSONL/NDJSON 줄 단위 레코드")

    print(f"\n{len(FIXTURES) + 3}개 생성: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
