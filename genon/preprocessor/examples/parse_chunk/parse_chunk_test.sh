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
#   llm (문서 단위)        : product_slf product_ssf product_hpp cs_hpp card
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
  "product_hpp:${MONIMO}/monimo_product_hpp_sample.json"
  "stock_insight:${MONIMO}/monimo_stock_insight_sample.xlsx"
  "link:${MONIMO}/monimo_link_sample.json"
)

# 한 건이 실패해도 나머지는 계속 돌린다(set -e 아래에서 전체 중단 방지).
for case in "${MONIMO_CASES[@]}"; do
  dt="${case%%:*}"; src="${case#*:}"
  echo "=== doc_type=${dt}  ${src}"
  "${PYTHON}" parse_chunk_test.py --doc_type "${dt}" "${src}" "${OUT}/" \
    || echo "[FAIL] doc_type=${dt} ${src}"
done

# 기존 카드 데모(회귀 확인용)
"${PYTHON}" parse_chunk_test.py --doc_type card "/Users/shkim/_shkim/01.source/doc_parser/shkim_labs/20260803_monimo/01_card/card01.flat.html" result_parse_chunk/


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
# ⚠️ 지금은 custom_field_faq.yaml 에 llm_fields(QUESTION_VARIANTS)가 있어 행마다 LLM 을 1회 호출한다.
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
