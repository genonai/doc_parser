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
import re
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
    ("cs_hpp",        MONIMO / "monimo_cs_hpp_rich_table_sample.html", "rich cell 표 + colspan 안내 셀"),
    ("product_slf",   MONIMO / "monimo_product_slf_sample.md",         "llm + markdown front matter"),
    ("product_ssf",   MONIMO / "monimo_product_ssf_sample.md",         "llm + markdown front matter"),
    ("product_hpp",   MONIMO / "monimo_product_hpp_wcms_sample.json",  "json_semantic(풀 캡처)"),
    ("product_hpp",   MONIMO / "monimo_product_hpp_sample.json",       "json_semantic(최소)"),
    ("product_hpp",   MONIMO / "monimo_product_hpp_rich_table_sample.json",
     "rich cell 표(연회비·적립·제휴링크)"),
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


def check_product_hpp_table_format(chunks: list) -> list[str]:
    """table_format: auto 는 정형 표를 markdown 으로 낸다.

    연회비 표는 행 라벨이 `<th scope="row">` 인 실제 WCMS 표다. 병합 셀도 계층 헤더도
    없으므로 markdown 이어야 하는데, 예전에는 행 라벨 `<th>` 때문에 모든 행이 헤더 행으로
    집계돼 계층 헤더 표로 오인되고 html 로 나갔다(table_shape.leading_header_row_count).
    """
    problems = []
    # 표 청크만 본다 — table_as_chunk 로 표와 표 설명이 다른 청크로 갈리므로, 설명 청크가
    # "총 연회비" 를 언급하는 것만으로 골라지면 표기형태를 엉뚱한 청크에서 검사한다.
    texts = [c.get("text") or "" for c in chunks if c.get("has_table")]
    fee = next((t for t in texts if "총 연회비" in t), None)
    if fee is None:
        return ["연회비 표가 어느 청크에도 없습니다"]

    if "<table" in fee:
        problems.append("연회비 표가 html 로 나왔습니다(auto 가 markdown 을 골라야 함)")
    # compact_tables 가 켜져 있으면 구분선이 `| - | - |` 로 줄어든다(`| --- |` 아님).
    if not any(re.fullmatch(r"\|(\s*-+\s*\|)+", line.strip()) for line in fee.splitlines()):
        problems.append("연회비 표에 markdown 구분선이 없습니다")
    # 행 라벨은 첫 컬럼의 데이터 행으로 남아야 한다(헤더로 반복되면 안 된다).
    if not any(line.lstrip().startswith("| 총 연회비 |") for line in fee.splitlines()):
        problems.append("행 라벨 '총 연회비' 가 첫 컬럼 데이터 행으로 남지 않았습니다")
    for value in ("20,000", "18,000", "15,000", "13,000", "국내전용"):
        if value not in fee:
            problems.append(f"연회비 표에서 '{value}' 가 사라졌습니다")

    # 대조군: 원래도 정형이던 적립률 표도 markdown 이어야 한다.
    rate = next((t for t in texts if "적립률" in t and "일반 가맹점" in t), None)
    if rate is not None and "<table" in rate:
        problems.append("적립률 표가 html 로 나왔습니다(정형 표 회귀)")
    return problems


def check_stock_insight_row_merge(chunks: list) -> list[str]:
    """AI차트뷰 원천은 세부내용을 여러 행에 문자 단위로 잘라 보낸다.

    row_merge 가 등록번호+종목코드 연속 런을 게시물 라인번호 순으로 이어붙여
    20행 → 4레코드(종목 4건)가 되어야 한다. 구분자가 끼거나 순서가 틀리면 복원되지 않는다.

    detail_desc 는 **JSON·HTML·평문 중 무엇이든** 올 수 있어 스키마를 못 박을 수 없다.
    샘플에 세 종류를 모두 넣어 text_from 의 자동 판별을 검증한다.
      테슬라·엔비디아 = JSON, 팔란티어 = 구조 HTML(표 포함), 리게티 = 평문
    레코드가 chunk_size 를 넘으므로 종목당 여러 청크로 갈라지고, 조각마다 종목명 접두가
    반복되며 분할 지점에는 직전 섹션 제목이 `(이어서)` 로 다시 붙는다.
    """
    problems = []
    expected = {"테슬라", "엔비디아", "팔란티어 테크놀로지스", "리게티 컴퓨팅"}
    stocks = {c.get("JONG_NM") for c in chunks}
    if stocks != expected:
        problems.append(f"종목 {len(expected)}건 기대, 실제 {sorted(stocks)}(row_merge 병합 실패)")
    if len(chunks) <= len(stocks):
        problems.append(f"분할이 일어나지 않았습니다: 종목 {len(stocks)}건에 청크 {len(chunks)}건")

    for idx, chunk in enumerate(chunks):
        name = chunk.get("JONG_NM")
        text = chunk.get("text") or ""
        # 분할 조각마다 "어느 종목의 언제 기준 분석인지" 가 다시 붙어야 그 조각만 검색돼도
        # LLM 이 근거로 쓸 수 있다. 종목코드·기준일은 어휘 매칭에도 기여한다.
        expected_prefix = (
            f"종목명: {name}\n종목코드: {chunk.get('JONG_CODE')}\n"
            f"분석기준일: {chunk.get('ANALYSIS_DATE')}"
        )
        if not text.startswith(expected_prefix):
            problems.append(f"[{idx}] 접두 3줄이 어긋납니다: {text[:60]!r}")
        if "<BR>" in text or "<strong>" in text:
            problems.append(f"[{idx}] 본문에 인라인 HTML 태그가 남았습니다")
        if '{"trading_strategy"' in text:
            problems.append(f"[{idx}] 본문에 JSON 원문이 그대로 들어갔습니다(text_from 미적용)")

    # JSON 종목: 원본이 metadata 에 JSON 그대로 남고, 본문은 마크다운 헤딩으로 펴진다.
    tesla = [c for c in chunks if c.get("JONG_NM") == "테슬라"]
    for idx, chunk in enumerate(tesla):
        try:
            json.loads(chunk.get("DETAIL_DESC") or "")
        except (ValueError, TypeError):
            problems.append(f"테슬라[{idx}] DETAIL_DESC 가 JSON 으로 복원되지 않았습니다")
    if tesla and "## trading strategy" not in tesla[0]["text"]:
        problems.append("JSON 이 마크다운 헤딩으로 펴지지 않았습니다(## trading strategy 없음)")

    # 핵심 계약: 섹션이 있는 원천이면 **모든** 청크가 섹션 문맥을 갖는다. 헤딩 경계에서
    # 잘렸으면 조각이 헤딩으로 시작하고, 섹션 하나가 chunk_size 를 넘어 중간에서 잘렸으면
    # `(이어서)` 로 직전 제목을 물려받는다. 둘 다 아니면 "8월 17일 2544만" 같은 조각이
    # 무엇에 대한 값인지 모르는 채로 검색에 노출된다.
    by_stock = {}
    for chunk in chunks:
        by_stock.setdefault(chunk.get("JONG_NM"), []).append(chunk)
    for name, group in by_stock.items():
        bodies = [c["text"].split("\n", 3)[3] if c["text"].count("\n") >= 3 else ""
                  for c in group]
        if not any(body.lstrip().startswith("#") for body in bodies):
            continue                      # 헤딩이 없는 원천(평문)은 대상이 아니다
        for idx, body in enumerate(bodies):
            if not body.lstrip().startswith("#") and "(이어서)" not in body:
                problems.append(
                    f"{name}[{idx}] 조각이 섹션 문맥 없이 시작합니다: {body[:40]!r}"
                )

    # 구조 HTML 종목: 표가 셀 한 줄씩으로 뭉개지지 않고 살아 있어야 한다.
    pltr = " ".join(c["text"] for c in chunks if c.get("JONG_NM") == "팔란티어 테크놀로지스")
    if pltr and "36,178,448" not in pltr:
        problems.append("HTML 표의 값이 사라졌습니다(구조 HTML 렌더링 실패)")

    # 평문 종목: 그대로 실려야 한다(판별이 HTML/JSON 으로 새면 안 된다).
    rgti = " ".join(c["text"] for c in chunks if c.get("JONG_NM") == "리게티 컴퓨팅")
    if rgti and "20일선(17.15)" not in rgti:
        problems.append("평문 detail_desc 가 본문에 실리지 않았습니다")

    first = tesla[0] if tesla else {}
    if first.get("ANALYSIS_DATE") != 20260827:
        problems.append(f"ANALYSIS_DATE 20260827 기대, 실제 {first.get('ANALYSIS_DATE')!r}")
    # TB 에 대응 컬럼이 없어 매핑하지 않는 원천 컬럼이 metadata 로 새면 안 된다.
    leaked = sorted({"md_stck_itm_c", "kosc_stck_itm_c", "nat_c"} & set(first))
    if leaked:
        problems.append(f"매핑하지 않은 원천 컬럼이 metadata 에 실렸습니다: {leaked}")
    return problems



def check_cs_hpp_degenerate_table(chunks: list) -> list[str]:
    """레이아웃용 표(colspan 안내 배너)는 표 표기 없이 평문으로 나간다(#360).

    이 샘플의 첫 표는 `<tr><td colspan="4">해당 목차는 [AI 에이전트용] …</td></tr>` 하나뿐인
    안내 배너다. 표로 내면 markdown 에서는 산문이 열 수만큼 복제되고 데이터 행이 0인 무효
    표가 되며, html 에서는 태그만 얹힌다. 어느 쪽도 검색에 기여하지 않는다.

    표기형태 필드에서도 같아야 한다 — 강제 markdown/html 보다 이 판정이 앞선다는 계약이다.
    같은 문서의 정형 표 2개는 표로 남아야 한다(오탐 대조군).
    """
    # 배너 원문 그대로 찾는다. `[표 검색 설명]` 의 LLM 요약문도 같은 내용을 다른 문장으로
    # 다시 말하므로, 짧은 조각으로 세면 그 요약까지 중복으로 집계된다.
    banner = ("해당 목차는 [AI 에이전트용] 이므로, 실제 상담 시에는 세부 지침이 포함된 "
              "[상담직원용] 컨텐츠를 활용하시기 바랍니다.")
    problems = []
    fields = ("text", "text_table_html", "text_table_md")
    hit = next((c for c in chunks if banner in (c.get("text") or "")), None)
    if hit is None:
        return ["안내 배너 원문이 어느 청크에도 없습니다(내용 손실)"]

    for field in fields:
        value = hit.get(field) or ""
        if banner not in value:
            problems.append(f"{field} 에서 안내 배너가 사라졌습니다")
            continue
        if value.count(banner) != 1:
            problems.append(
                f"{field} 에 안내 배너가 {value.count(banner)}번 실렸습니다(colspan 복제)")
        # 배너가 든 줄에 표 표기가 남아 있으면 안 된다.
        line = next(ln for ln in value.splitlines() if banner in ln)
        if "|" in line or "<table" in line or "<td" in line:
            problems.append(f"{field} 의 안내 배너가 표 표기를 달고 있습니다: {line[:60]!r}")

    # 대조군: 정형 표 2개는 표로 남는다(표기형태는 auto 가 정하므로 둘 다 허용).
    tables = [c.get("text") or "" for c in chunks if c.get("has_table")]
    kept = sum(
        1 for t in tables
        if "<table" in t or any(
            re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", ln.strip()) for ln in t.splitlines()))
    if kept < 2:
        problems.append(f"정형 표 2개가 표로 남아야 하는데 {kept}개만 남았습니다(오탐)")
    return problems


def check_table_text_formats(chunks: list) -> list[str]:
    """표 표기형태별 추가 필드(#360 text_table_html / text_table_md) 공통 단정.

    resource_dev 가 이 기능을 켜 두므로 모든 케이스가 두 필드를 갖는다. 예전에는 이 함수가
    "필드가 없으면 기능 범위 밖" 으로 통과시켰는데, 그 폴백이 곧 이 기능이 행 기반 경로
    (product_hpp JSON 등)에서 조용히 빠져 있던 것을 감추고 있었다. 이제 필드 부재는 실패다.

    확인하는 것은 하나다: 표기형태만 다르고 담은 내용은 text 와 같아야 한다.
    표 밖 본문이 잘려 나가거나 셀 값이 중복되면 여기서 걸린다.
    """
    problems = []
    fields = ("text_table_html", "text_table_md")
    present = [c for c in chunks if any(f in c for f in fields)]
    if not present:
        return ["표기형태 필드가 한 청크에도 없습니다 "
                "(resource_dev 는 table_text_formats on — 이 경로가 기능을 건너뜁니다)"]
    if len(present) != len(chunks):
        problems.append(
            f"표기형태 필드가 일부 청크에만 있습니다 {len(present)}/{len(chunks)}건")

    def tokens(text: str) -> list:
        """표기형태를 지운 내용 토큰. 태그·파이프·markdown 구분선을 걷어낸다."""
        text = re.sub(r"<[^>]+>", " ", text or "")
        text = re.sub(r"^\s*\|[\s\-:|]+\|\s*$", " ", text, flags=re.M)
        return [t for t in re.split(r"\s+", text.replace("|", " ")) if t]

    for idx, chunk in enumerate(present):
        base = tokens(chunk.get("text"))
        for field in fields:
            value = chunk.get(field)
            if not value:
                problems.append(f"[{idx}] {field} 가 비어 있습니다")
                continue
            got = tokens(value)
            if got == base:
                continue
            # 병합 셀(colspan)을 markdown 으로 펴면 값이 컬럼마다 복제된다 — 손실만 문제다.
            lost = [t for t in base if t not in got]
            if lost:
                problems.append(f"[{idx}] {field} 에서 내용이 사라졌습니다: {lost[:3]}")
    return problems


def check_table_as_chunk(chunks: list) -> list[str]:
    """표 독립 청크(#360 table_as_chunk) 공통 단정.

    표는 자기 청크를 갖는다 — 한 청크에 표가 둘 이상 실리면 그 규칙이 깨진 것이다.
    "표 밖 문장이 없다" 로는 단정할 수 없다: 청크 접두(카드명·섹션 제목)와 `[표 검색 설명]`
    블록은 표 청크에 함께 실려야 하는 값이다(docling 경로도 그렇게 싣는다).
    """
    problems = []
    for idx, chunk in enumerate(chunks):
        if not chunk.get("has_table"):
            continue
        text = chunk.get("text") or ""
        # 검출은 여기서 단순하게 센다(이 스크립트는 facade 를 import 하지 않는다).
        # html 표는 여는 태그 수, markdown 표는 구분선 줄 수가 곧 표 개수다.
        count = text.lower().count("<table") + sum(
            1 for line in text.splitlines()
            if re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", line.strip()))
        if count > 1:
            problems.append(f"[{idx}] 한 청크에 표가 {count}개 실렸습니다(표 독립 청크 위반)")
    return problems


# (doc_type, 샘플 파일명) → 추가 단정 함수
# 마크다운 링크. 라벨 안 대괄호는 다루지 않는다 - 스킴이 있는 것만 링크로 본다.
_MD_LINK_RE = re.compile(r"\]\(\s*(?:https?|mailto|tel):")


def check_no_markdown_links(chunks: list) -> list[str]:
    """청크 텍스트에 마크다운 링크 URL 이 남지 않는다(공통).

    docling HTML 백엔드는 `<a href>` 를 hyperlink 로 보존하고 markdown serializer 가
    `[라벨](URL)` 로 찍는다. URL 은 검색에 기여하지 않으면서 청크 예산만 먹으므로
    `facade/common/markdown_export` 가 라벨만 남기고 버린다. 라벨 보존은
    케이스별 단정(check_product_hpp_link_labels)에서 따로 본다.
    """
    problems = []
    for idx, chunk in enumerate(chunks):
        for field in ("text", "text_table_html", "text_table_md"):
            value = chunk.get(field)
            if not isinstance(value, str):
                continue
            hit = _MD_LINK_RE.search(value)
            if hit:
                start = max(hit.start() - 40, 0)
                problems.append(
                    f"청크[{idx}].{field} 에 링크 URL 이 남았습니다: "
                    f"...{value[start:hit.end() + 10]}...")
                break
    return problems


def check_product_hpp_link_labels(chunks: list) -> list[str]:
    """URL 만 버리고 라벨은 남는다 - 링크 텍스트를 통째로 지우는 회귀를 잡는다."""
    joined = "\n".join(c.get("text") or "" for c in chunks)
    missing = [label for label in ("www.samsungfire.com", "www.speedmate.com", "예약하기")
               if label not in joined]
    if missing:
        return [f"링크 라벨이 사라졌습니다: {missing} (URL 만 버려야 함)"]
    return []


EXTRA_CHECKS = {
    ("product_slf", "monimo_product_slf_sample.md"): check_front_matter,
    ("card", "card01.flat.html"): check_card_annual_fee,
    ("product_hpp", "monimo_product_hpp_wcms_sample.json"): check_product_hpp_table_format,
    ("cs_hpp", "monimo_cs_hpp_rich_table_sample.html"): check_cs_hpp_degenerate_table,
    ("product_hpp", "monimo_product_hpp_rich_table_sample.json"): check_product_hpp_link_labels,
    ("stock_insight", "monimo_stock_insight_sample.xlsx"): check_stock_insight_row_merge,
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


def load_config_spec(block: dict) -> dict:
    """블록이 가리키는 custom_field yaml 원본(없으면 빈 dict)."""
    path = RESOURCE_DIR / str(block.get("config_file") or "")
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


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


def check_field_labels(chunks: list, spec: dict) -> list[str]:
    """`field_labels` 를 선언한 필드는 청크 본문에 `항목명: 값` 으로 실린다.

    라벨은 값이 한 줄일 때만 붙는다(여러 줄 블록은 자기 제목을 이미 갖는다). 그래서 검사도
    한 줄 값에만 적용한다. metadata 에는 값이 있는데 본문에 `항목명: 값` 이 없으면 설정과
    산출이 어긋난 것이다 — extractor 마다 라벨 규칙이 갈리던 회귀(#360)를 여기서 잡는다.
    """
    labels = spec.get("field_labels") or {}
    body_fields = [f for f in (spec.get("text_fields") or []) if f in labels]
    if not body_fields:
        return []
    problems = []
    for idx, chunk in enumerate(chunks):
        text = chunk.get("text") or ""
        for field in body_fields:
            value = chunk.get(field)
            if not isinstance(value, str) or not value.strip() or "\n" in value:
                continue
            expected = f"{labels[field]}: {value}"
            if expected not in text:
                problems.append(
                    f"[{idx}] '{field}' 가 항목명 없이 실렸습니다(기대 {expected[:40]!r})")
                break
    return problems


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

    spec = load_config_spec(block)
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

    problems += check_field_labels(chunks, spec)
    problems += check_table_text_formats(chunks)
    problems += check_no_markdown_links(chunks)
    problems += check_table_as_chunk(chunks)

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
