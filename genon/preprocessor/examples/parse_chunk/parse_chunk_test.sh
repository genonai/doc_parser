#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREPROCESSOR_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

# venv 탐색 순서: genon/preprocessor/.venv (원본 repo 의 uv sync 위치)
#              → 저장소 루트 .venv (코드서빙 배포본의 로컬 개발환경 위치)
#              → 시스템 python
if [ -z "${PYTHON:-}" ]; then
  if [ -x "${PREPROCESSOR_DIR}/.venv/bin/python" ]; then
    PYTHON="${PREPROCESSOR_DIR}/.venv/bin/python"
  elif [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
  else
    PYTHON="python"
  fi
fi

export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:/usr/local/lib:/usr/lib"

# ── 풀 E2E: PDF/문서 → 파싱(docling) → 청킹  (파싱에 layout/OCR 모델서빙 필요) ──
# python parse_chunk_test.py "../genon/preprocessor/sample_files/docx_sample.pdf" result_parse_chunk/
# python parse_chunk_test.py "./20260617_table_doc/10.여비규정_20240129_인사경영국_20240129.pdf" result_parse_chunk/

# ── docling JSON 입력 → 청킹만  (모델서버 불필요) ──
# python parse_chunk_test.py "result_parse_chunk/docx_sample.docling.json" result_parse_chunk/

# ── 디렉터리 일괄 ──
# python parse_chunk_test.py "../genon/preprocessor/sample_files" result_parse_chunk/

cd "${SCRIPT_DIR}"
# "${PYTHON}" parse_chunk_test.py "../../sample_files/pdf_sample.pdf" result_parse_chunk/

# ── 모니모 doc_type 15종 일괄 E2E (parse → chunk, 케이스 16건) ───────────────────
# 샘플은 sample_files/monimo/ 의 가상 데이터. 행/레코드마다 LLM 이 1회 호출되므로
# 각 샘플은 2~3건으로 제한되어 있다(전체 ~37 호출).
# monimo_event 는 케이스가 2개다 — 협의용 한글 키 표기(sample_files/json/…)와
# 원천 화면에서 확인한 실 payload 스키마(monimo_event_real_sample.json). 같은 config 가
# 두 표기를 모두 받는지(key_map 별칭 fallback) 확인하는 용도다.
#
# 필요 조건: resource_dev/custom_field_*.yaml 의 llm_fields.url / api_key 가 살아있는 모델서빙을
#            가리켜야 SUMMARY_TEXT·KEYWORDS·SYNONYMS·QUESTION_VARIANTS 가 채워진다.
#            서빙이 없으면 on_error:null 정책에 따라 그 필드만 null 이 되고 나머지는 정상 산출된다.
# 산출물: result_parse_chunk/<stem>.chunks.json  (+ docling 경로는 <stem>.docling.json)
#
# doc_type 별 경로:
#   tabular_mapping (xlsx) : menu term faq cs_slf cs_ssf stock_insight   → 행 1개 = 청크 1개
#   json_mapping    (json) : faq monimo_event monimo_news cs_sss link    → 레코드 1개 = 청크 1개
#   json_semantic   (json) : product_hpp                                 → 섹션 1개 = 청크 1개
#                            (성격별로 나뉜 섹션마다 SECTION_NM/SOURCE_JSON_PATH + 공통 정보)
#   llm (문서 단위)        : product_slf product_ssf cs_hpp card
#                            → metadata 가 모든 청크에 동일하게 붙는다
MONIMO="../../sample_files/monimo"
OUT="result_parse_chunk"

MONIMO_CASES=(
  "menu:${MONIMO}/monimo_menu_sample.xlsx"
  "term:${MONIMO}/monimo_term_sample.xlsx"
  "faq:${MONIMO}/monimo_faq_sample.xlsx"
  "faq:${MONIMO}/monimo_faq_json_sample.json"
  "monimo_event:../../sample_files/json/monimo_event_sample.json"
  "monimo_event:${MONIMO}/monimo_event_real_sample.json"
  # 실 WCMS 마크업(evant.html) 재현 — 5열 표의 빈 셀까지 살아나오는지 본다.
  # DETAIL_TEXT 의 표는 output.table_format 을 따른다(html=<table>, markdown=파이프 표).
  # 구 코드는 셀 하나당 한 줄로 뭉개 빈 셀이 사라졌고 열 대응을 복원할 수 없었다.
  "monimo_event:${MONIMO}/monimo_event_table_sample.json"
  "monimo_news:${MONIMO}/monimo_news_sample.json"
  "cs_slf:${MONIMO}/monimo_cs_slf_sample.xlsx"
  "cs_ssf:${MONIMO}/monimo_cs_ssf_sample.xlsx"
  "cs_sss:${MONIMO}/monimo_cs_sss_sample.json"
  "cs_hpp:${MONIMO}/monimo_cs_hpp_sample.html"
  # 실 원천 형태(ADCC 카드 고객센터). 파일명이 점으로 시작하고 본문이 <!DOCTYPE>/<html> 없는
  # fragment 다 — 이 조합이 겹치면 docling 이 포맷 판정에 실패해 "File format not allowed" 로
  # 죽었다(#349). 파일명의 선행 점을 지우면 재현이 사라지므로 rename 하지 말 것.
  #   _02_ : section 1개(항목 1건), _01_ : section 3개(항목 3건 → metadata 는 1세트만 나온다)
  "cs_hpp:${MONIMO}/.INC_235488_02_20260626103138.html"
  "cs_hpp:${MONIMO}/.INC_235489_01_20260626103139.html"
  "product_slf:${MONIMO}/monimo_product_slf_sample.md"
  "product_ssf:${MONIMO}/monimo_product_ssf_sample.md"
  # json_semantic — 상품 1건(JSON 파일 1개)이 성격별 섹션(혜택 상세/상품 문서 …)으로 나뉘어
  # 섹션마다 청크 1건이 된다. wcms 샘플은 풀 캡처 재현본, product_hpp_sample 은 최소(루트 평평) 케이스.
  "product_hpp:${MONIMO}/monimo_product_hpp_wcms_sample.json"
  "product_hpp:${MONIMO}/monimo_product_hpp_sample.json"
  # 원천이 한 종목의 세부내용 JSON 하나를 ntc_objline 1..N 으로 문자 단위로 잘라 여러 행에
  # 뿌린다. row_merge 가 그 행들을 도로 이어붙인다 — 아래 전용 블록 참고.
  "stock_insight:${MONIMO}/monimo_stock_insight_sample.xlsx"
  "link:${MONIMO}/monimo_link_sample.json"
)

# 한 건이 실패해도 나머지는 계속 돌린다(set -e 아래에서 전체 중단 방지).
# for case in "${MONIMO_CASES[@]}"; do
#   dt="${case%%:*}"; src="${case#*:}"
#   echo "=== doc_type=${dt}  ${src}"
#   "${PYTHON}" parse_chunk_test.py --doc_type "${dt}" "${src}" "${OUT}/" \
#     || echo "[FAIL] doc_type=${dt} ${src}"
# done

# ── Markdown front matter → 청크 metadata 승격 / 청크 텍스트 제외 확인 (#360) ──
# custom_field_product_{slf,ssf}.yaml 의 markdown.front_matter 블록이
#   document_type/source_file/source_pages/author/created_at(→created_date) 만 metadata 로 올리고
#   front matter 전체(exclude_text_fields: ["*"])는 청크 텍스트에서 뺀다.
# front matter 만으로 이루어진 청크가 사라져 8청크 → 7청크가 된다.
# 본문에서 뺀 front matter 도 LLM 프롬프트에는 계속 실린다(PRODUCT_C 근거 보존).
# LLM 연결이 죽어도 front matter 승격 필드와 constants(GROUP_C 등)는 그대로 남는다.
# "${PYTHON}" parse_chunk_test.py --doc_type product_slf \
#   "${MONIMO}/monimo_product_slf_sample.md" "${OUT}/front_matter/"
# "${PYTHON}" -c "
# import json
# d = json.load(open('${OUT}/front_matter/monimo_product_slf_sample.chunks.json'))
# c = d[0]
# print(len(d), c['source_file'], c['source_pages'], c['created_date'])
# assert c['source_pages'] == 9          # front matter 유래 값은 타입(int) 그대로
# assert c['created_date'] == 20260112   # created_at → 청커 date_int transform
# assert all('conversion_note' not in x['text'] and 'source_file:' not in x['text'] for x in d)
# "

# ── AI차트뷰 xlsx: 행 병합 + 자동판별 렌더링 + 섹션 단위 분할 (#360) ─────────
# 2026-08-28 원천 개편으로 세 가지가 바뀌었다.
#   1) 헤더가 영문 소문자 (jong_name·jong_code·regt_no·ntc_objline·detail_desc·news_date …)
#   2) 한 종목의 세부내용이 ntc_objline 1..N 으로 **문자 단위 절단**되어 행마다 흩어짐
#      (실 원천에 `"price_pat` / `tern_desc":` 처럼 키 이름 중간에서 끊긴 사례가 있다)
#      → row_merge(group_by=REGT_NO+JONG_CODE, order_by=NTC_OBJLINE_NO)가 구분자 없이 이어붙임
#   3) detail_desc 에 **JSON·HTML·평문이 섞여** 오고 정해진 스키마가 없음
#      → text_from 이 종류를 자동 판별해 하나의 마크다운으로 수렴시킴
# 샘플 4종목이 세 종류를 모두 덮는다:
#   테슬라·엔비디아 = JSON, 팔란티어 = 구조 HTML(표 포함), 리게티 = 평문
# 청커는 `## ` 를 우선 분리자로 써서 섹션 경계에서 자르고, 섹션 하나가 chunk_size 를 넘어
# 중간에서 잘리면 직전 제목을 `(이어서)` 로 다시 붙인다 — 뒷 조각만 검색돼도 문맥이 남는다.
# 샘플을 다시 만들려면: "${PYTHON}" make_stock_insight_sample.py
# LLM 필드가 비활성이라 모델서빙 없이 돈다.
"${PYTHON}" parse_chunk_test.py --doc_type stock_insight --chunk-size 1500 \
  "${MONIMO}/monimo_stock_insight_sample.xlsx" "${OUT}/"
# "${PYTHON}" -c "
# import json
# d = json.load(open('${OUT}/monimo_stock_insight_sample.chunks.json'))
# stocks = {c['JONG_NM'] for c in d}
# assert stocks == {'테슬라', '엔비디아', '팔란티어 테크놀로지스', '리게티 컴퓨팅'}, sorted(stocks)
# assert len(d) > len(stocks), f'분할이 일어나지 않았습니다: {len(d)}청크'
# for i, c in enumerate(d):
#     # 조각마다 '어느 종목의 언제 기준 분석인지' 3줄이 반복된다.
#     assert c['text'].startswith(f\"종목명: {c['JONG_NM']}\\n종목코드: {c['JONG_CODE']}\\n분석기준일: {c['ANALYSIS_DATE']}\"), i
#     assert len(c['text']) <= 1500, (i, len(c['text']))
#     assert '<BR>' not in c['text'] and '<strong>' not in c['text'], i
#     assert 'DETAIL_TEXT' not in c['text'] and 'detail_desc:' not in c['text'], i

# def body(c): return c['text'].split(chr(10), 3)[3]   # 접두 3줄을 걷어낸 본문
# # JSON 종목: 원본은 metadata 에 JSON 그대로, 본문은 마크다운 헤딩으로 펴진다.
# tesla = [c for c in d if c['JONG_NM'] == '테슬라']
# assert all(json.loads(c['DETAIL_DESC']) for c in tesla)   # 조각 순서/구분자가 틀리면 터진다
# assert '## trading strategy' in tesla[0]['text']
# # 섹션이 있는 원천이면 모든 조각이 섹션 문맥을 갖는다(헤딩으로 시작하거나 (이어서)).
# for name in ('테슬라', '엔비디아', '팔란티어 테크놀로지스'):
#     for i, c in enumerate(x for x in d if x['JONG_NM'] == name):
#         assert body(c).lstrip().startswith('#') or '(이어서)' in body(c), (name, i, body(c)[:40])
# # 구조 HTML 종목: 표 값이 살아 있어야 한다. 평문 종목: 그대로 실려야 한다.
# assert '36,178,448' in ' '.join(c['text'] for c in d if c['JONG_NM'] == '팔란티어 테크놀로지스')
# assert '20일선(17.15)' in ' '.join(c['text'] for c in d if c['JONG_NM'] == '리게티 컴퓨팅')
# assert d[0]['ANALYSIS_DATE'] == 20260827
# assert 'md_stck_itm_c' not in d[0]        # TB 에 없는 원천 컬럼은 metadata 로 새지 않는다
# print(f'stock_insight OK: 종목 {len(stocks)}건 → {len(d)}청크, 길이 {[len(c[\"text\"]) for c in d]}')
# "

# 기존 카드 데모(회귀 확인용)
# front matter 타입 보존은 front matter 키에만 적용된다 — card 의 annual_fee_amount 는
# 예전대로 문자열 '18000' 이어야 한다(이미 적재된 컬렉션의 property 타입 호환).
# "${PYTHON}" parse_chunk_test.py --doc_type card "/Users/shkim/_shkim/01.source/doc_parser/shkim_labs/20260803_monimo/01_card/card01.flat.html" "${OUT}/"
# "${PYTHON}" -c "
# import json
# d = json.load(open('${OUT}/card01.flat.chunks.json'))
# assert d[0]['annual_fee_amount'] == '18000', d[0]['annual_fee_amount']
# "

# "${PYTHON}" parse_chunk_test.py --doc_type faq "/Users/shkim/_shkim/01.source/doc_parser/shkim_labs/20260803_monimo/02_faq/증권FAQ_260712.csv" "${OUT}/"
# "${PYTHON}" parse_chunk_test.py --doc_type card "../../../../shkim_labs/20260806_hwp/hwp_sample_table.hwp" "${OUT}/"
# "${PYTHON}" parse_chunk_test.py --doc_type monimo_event "${MONIMO}/monimo_event_table_sample.json" "${OUT}/"
# "${PYTHON}" parse_chunk_test.py --doc_type product_hpp "${MONIMO}/monimo_product_hpp_wcms_sample.json" "${OUT}/"

# ── 표 표기형태별 추가 텍스트(text_table_html / text_table_md) ─────────────────
# output.table_text_formats 를 켜면 청크에 text 외에 표기형태별 텍스트가 함께 실린다.
# text 는 그대로 두고 표 부분만 바꿔 렌더한 전문이며, 표가 없는 청크는 text 와 같은 값이 된다.
# resource_dev 는 ["html", "markdown"] 로 켜져 있다. 껐다 켠 대조는 아래처럼 확인한다
# (설정 편집 없이 청킹만 다시 돌리려면 이미 만들어둔 .docling.json 을 입력으로 준다).
# "${PYTHON}" parse_chunk_test.py --doc_type cs_hpp "${MONIMO}/monimo_cs_hpp_rich_table_sample.html" "${OUT}/"
# "${PYTHON}" -c "
# import json
# d = json.load(open('${OUT}/monimo_cs_hpp_rich_table_sample.chunks.json'))
# t = next(c for c in d if c['has_table'])
# assert '<table' in t['text_table_html'] and '<table' not in t['text_table_md']
# print('표기형태 분리 확인:', len(t['text']), len(t['text_table_html']), len(t['text_table_md']))
# "

# ── 청크 선두 헤더(HEADER: <섹션 경로>) on/off ─────────────────────────────────
# 섹션 경로는 청크 선두 한 줄에서만 붙는다(compose_vectors). 예전에는 본문 안에도 같은 제목이
# 두 번 더 들어가 청크 텍스트의 30~56% 가 제목 반복이었고, 제목만 있고 본문이 없는 청크도
# 생겼다(여비세칙 76개 중 20개) — 둘 다 제거되었다.
# --chunk-header 미지정 시 설정값(chunking.include_chunk_header, 기본 on)을 따른다.
# off 는 순수 본문만 뽑을 때 쓴다. 검색 시 섹션 문맥이 사라지므로 RAG 적재용으로는 on 을 권장.
# 이미 만들어둔 .docling.json 을 입력으로 주면 모델서버 없이 청킹만 다시 돌려 비교할 수 있다.
# "${PYTHON}" parse_chunk_test.py --chunk-header on  "${OUT}/hwp_sample_table.docling.json" "${OUT}/hdr_on/"
# "${PYTHON}" parse_chunk_test.py --chunk-header off "${OUT}/hwp_sample_table.docling.json" "${OUT}/hdr_off/"


# ── LLM 캐시 / error_policy / deadline 테스트 (parse→chunk 분리 경로) ──────
# 캐시는 parse 단계 LLM 호출(OCR VLM/TOC/이미지·표 desc/메타데이터)에서 동작한다.
# 로컬은 NFS 가 없으므로 interim_root 를 로컬 쓰기가능 경로로 준다.
# 각 LLM 호출마다 로그에 HIT(캐시 재사용) / MISS(실제 호출) / STORE(저장)가 찍힌다.

FILE="../../sample_files/pdf_sample.pdf"
OUT="result_parse_chunk"
INTERIM="${OUT}/interim"          # <INTERIM>/<workflow_id>/<run_id>/llm_cache/
WF="wf-parse-001"                 # 재실행 간 동일해야 캐시 재사용
RUN="run-1"

# 1) 1회차(MISS=LLM 실제 호출 후 저장):
# "${PYTHON}" parse_chunk_test.py --llm_cache --interim_root "${INTERIM}" --workflow_id "${WF}" --run_id "${RUN}" "${FILE}" "${OUT}/"
# 2) 2회차(HIT=캐시 재사용): 로그에서 페이지별 "HIT" 및 요약 "[llm_cache] hit=.. miss=.." 확인
# "${PYTHON}" parse_chunk_test.py --llm_cache --interim_root "${INTERIM}" --workflow_id "${WF}" --run_id "${RUN}" "${FILE}" "${OUT}/"

# ── monimo 카드 HTML → (flatten) → 파싱(custom_fields enrichment) → 청킹 ──────────
# custom_fields(카드 12필드)는 resource_dev/custom_field_card.yaml 로 설정된다.
# 결과는 파일로 저장되지 않고 파서 응답의 metadata 로 콘솔에 출력되며, 청킹 후에는
# <name>.chunks.json 의 각 청크 top-level 키로 실린다(GenOSVectorMeta extra=allow).
# MONIMO_SRC="../../../../shkim_labs/20260803_monimo/01_card/card02.docling.html"
# "${PYTHON}" parse_chunk_test.py --doc_type card "${MONIMO_SRC}" "${OUT}/"
# 결과 확인: "${PYTHON}" -c "import json;d=json.load(open('${OUT}/card02.docling.chunks.json'));print(d[0])"
# --doc_type card: 모든 청크에 doc_type="card" 스탬프(기존 12필드 유지).

# ── monimo FAQ 엑셀 → parser(tabular 행별) → chunker (행마다 1청크) ───────────────────────────
# processing_mode=tabular 이면 doc_type 없이도 "행=청크" 로 처리한다.
# doc_type=faq 는 각 행 컬럼을 custom_field_faq.yaml의 목표 필드
# (question/answer_text/category_code/... + doc_type)로 매핑할 때만 사용한다.
# 지금은 custom_field_faq.yaml 에 llm_fields(QUESTION_VARIANTS)가 있어 행마다 LLM 을 1회 호출한다.
#    모델서버 없이 매핑만 보려면 --doc_type 없이 돌리거나 그 yaml 의 llm_fields 를 주석 처리한다.
#    (증권/카드 FAQ 도 동일 스키마라 같은 매핑으로 처리)
# FAQ_SRC="../../../../shkim_labs/20260803_monimo/02_faq/증권FAQ_260712.xlsx"
# "${PYTHON}" parse_chunk_test.py --doc_type faq "${FAQ_SRC}" "${OUT}/"
# 결과 확인: 행마다 1청크. --doc_type faq 사용 시 목표 custom field(DB 컬럼명)도 부착.
#   ls "${OUT}"/생명FAQ_260712.chunks.json
#   "${PYTHON}" -c "import json;d=json.load(open('${OUT}/생명FAQ_260712.chunks.json'));print(len(d));print(d[0])"

# ── monimo 이벤트 JSON → parser(json_mapping 레코드별) → chunker (레코드마다 1청크) ──────────
# resource_dev/parser_processor_config.yaml 의 doc_type=monimo_event 블록을 enable: true 로 바꾸면
# eventList[*] 가 레코드마다 청크 1개가 되고 TITLE/EVENT_FROM/EVENT_TO/DETAIL_HTML 이 청크 metadata 로 실린다.
# 요약본문(SUMMARY_TEXT)은 LLM 생성이라 custom_field_monimo_event.yaml 의 llm_fields.url/model
# 설정이 필요하다(LLM 연결·프롬프트까지 그 파일 하나에 인라인되어 있다).
# LLM 없이 매핑만 확인하려면 같은 파일에서 llm_fields 를 주석 처리하고
# text_fields 를 [TITLE, DETAIL_TEXT] 로 바꾼다.
# (doc_type 은 이제 위 MONIMO_CASES 배열에서 기본으로 함께 실행된다)
# "${PYTHON}" parse_chunk_test.py --doc_type monimo_event "../../sample_files/json/monimo_event_sample.json" "${OUT}/"
# 결과 확인: 제목이 빈 3번째 레코드는 skip 되어 2청크.
#   "${PYTHON}" -c "import json;d=json.load(open('${OUT}/monimo_event_sample.chunks.json'));print(len(d));print(d[0])"

# 캐시 파일 확인: ls -R "${INTERIM}/${WF}/${RUN}/llm_cache/"

# error_policy=strict (enrichment 실패 시 예외 전파):
# "${PYTHON}" parse_chunk_test.py --llm_cache --interim_root "${INTERIM}" --workflow_id "${WF}" --run_id "${RUN}" --error_policy strict "${FILE}" "${OUT}/"

# 요청 deadline(초) — 초과 시 timeout(행잉 방지):
# "${PYTHON}" parse_chunk_test.py --request_deadline 60 "${FILE}" "${OUT}/"

# 캐시 미지정(기본): 기존과 완전히 동일(캐시 코드 미진입):
# "${PYTHON}" parse_chunk_test.py "${FILE}" "${OUT}/"
