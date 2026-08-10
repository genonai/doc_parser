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
"${PYTHON}" parse_chunk_test.py "../../sample_files/pdf_sample.pdf" result_parse_chunk/


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
# custom_fields(카드 12필드)는 resource_dev/custom_field_card.yaml 로 설정되며 결과는
# <name>.metadata.json 으로 저장된다.
# MONIMO_SRC="../../../../shkim_labs/20260803_monimo/01_card/card02.docling.html"
# "${PYTHON}" parse_chunk_test.py --doc_type card "${MONIMO_SRC}" "${OUT}/"
# 결과 확인: cat "${OUT}/card02.docling.metadata.json"
# --doc_type card: 모든 청크에 doc_type="card" 스탬프(기존 12필드 유지).

# ── monimo FAQ 엑셀 → parser(tabular 행별) → chunker (행마다 1청크, 컬럼→목표필드 매핑) ─────────
# doc_type=faq 이면 xlsx 를 시트 HTML 통짜가 아니라 "행=청크" 로 처리하고, 각 행 컬럼을
# custom_field_faq.yaml 매핑으로 목표 필드(question/answer_text/category_code/... + doc_type)로 부착한다.
# LLM 미호출 → 모델서버 불필요. (증권/카드 FAQ 도 동일 스키마라 같은 매핑으로 처리)
# FAQ_SRC="../../../../shkim_labs/20260803_monimo/02_faq/증권FAQ_260712.xlsx"
# "${PYTHON}" parse_chunk_test.py --doc_type faq "${FAQ_SRC}" "${OUT}/"
# 결과 확인: 행마다 1청크 + 목표 메타 부착
#   ls "${OUT}"/생명FAQ_260712.chunks.json
#   "${PYTHON}" -c "import json;d=json.load(open('${OUT}/생명FAQ_260712.chunks.json'));print(len(d));print(d[0])"

# 캐시 파일 확인: ls -R "${INTERIM}/${WF}/${RUN}/llm_cache/"

# error_policy=strict (enrichment 실패 시 예외 전파):
# "${PYTHON}" parse_chunk_test.py --llm_cache --interim_root "${INTERIM}" --workflow_id "${WF}" --run_id "${RUN}" --error_policy strict "${FILE}" "${OUT}/"

# 요청 deadline(초) — 초과 시 timeout(행잉 방지):
# "${PYTHON}" parse_chunk_test.py --request_deadline 60 "${FILE}" "${OUT}/"

# 캐시 미지정(기본): 기존과 완전히 동일(캐시 코드 미진입):
# "${PYTHON}" parse_chunk_test.py "${FILE}" "${OUT}/"
