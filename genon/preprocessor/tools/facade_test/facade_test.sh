#!/usr/bin/env bash
# facade_test.py 실행 시나리오 모음. 필요한 줄의 주석을 풀어 실행한다.
# 입력 경로는 이 디렉터리 기준이며, 실데이터는 저장소 밖 경로를 직접 지정한다.
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

cd "${SCRIPT_DIR}"
SAMPLES="../../sample_files"
OUT="result_facade_test"

# ── 단일 파일 (기본 facade: intelligent) ─────────────────────────────────────
"${PYTHON}" facade_test.py "${SAMPLES}/pdf_sample.pdf" "${OUT}/pdf/"
# "${PYTHON}" facade_test.py "${SAMPLES}/hwp_sample.hwp" "${OUT}/hwp/"
# "${PYTHON}" facade_test.py "${SAMPLES}/docx_sample.docx" "${OUT}/docx/"

# ── facade kwargs: 목차 분할, 상세 로그 ──────────────────────────────────────
# "${PYTHON}" facade_test.py "${SAMPLES}/pdf_sample.pdf" "${OUT}/toc/" --kw toc=1 --kw log_level=5

# ── 디렉터리 일괄 (실패 파일은 failed_files.json 에 기록하고 계속) ─────────────
# "${PYTHON}" facade_test.py "${SAMPLES}" "${OUT}/all/"
# "${PYTHON}" facade_test.py "${SAMPLES}" "${OUT}/all/" --fail-fast

# ── facade 전환 ──────────────────────────────────────────────────────────────
# "${PYTHON}" facade_test.py --facade convert "${SAMPLES}/pdf_sample.pdf" "${OUT}/convert/"
# "${PYTHON}" facade_test.py --facade attachment "${SAMPLES}/pdf_sample.pdf" "${OUT}/attachment/"
# "${PYTHON}" facade_test.py --facade parser "${SAMPLES}/pdf_sample.pdf" "${OUT}/parser/"

# ── DocumentProcessor 를 가진 임의의 .py 를 facade 로 로드 ───────────────────
# "${PYTHON}" facade_test.py --facade /path/to/my_processor.py "${SAMPLES}/pdf_sample.pdf" "${OUT}/custom/"

# facade 옵션은 모두 --kw 로 넘긴다. parse_chunk_test.py 의 개별 플래그와는 다음처럼 대응한다.
#   --llm_cache → --kw llm_cache=1      --interim_root X → --kw interim_root=X
#   --workflow_id X → --kw workflow_id=X  --run_id X → --kw run_id=X
#   --error_policy strict → --kw error_policy=strict

# ── LLM 파일 캐시: 같은 workflow_id/run_id/interim_root 로 재실행하면 2회차부터 캐시 재사용 ──
#    요청 종료 시 "[llm_cache] hit=.. miss=.." 로그로 확인한다.
# INTERIM="${OUT}/interim"
# "${PYTHON}" facade_test.py "${SAMPLES}/pdf_sample.pdf" "${OUT}/cache/" \
#   --kw llm_cache=1 --kw interim_root="${INTERIM}" --kw workflow_id=wf-test-001 --kw run_id=run-1

# ── enrichment 실패를 예외로 전파 ─────────────────────────────────────────────
# "${PYTHON}" facade_test.py "${SAMPLES}/pdf_sample.pdf" "${OUT}/strict/" --kw error_policy=strict

# ── docling 파이프라인 구간별 소요 시간 출력 ─────────────────────────────────
# DOCLING_DEBUG_PROFILE_PIPELINE_TIMINGS=true "${PYTHON}" facade_test.py "${SAMPLES}/pdf_sample.pdf" "${OUT}/timing/"
