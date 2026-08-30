#!/usr/bin/env bash
# custom_fields doc_type 별 파싱→청킹 자동 검증.
#
# parse_chunk_test.sh 는 손으로 이것저것 돌려보는 놀이터고, 이 스크립트는 "항상 돌리는"
# 검증이다. doc_type 마다 저장소 안의 샘플을 파싱·청킹한 뒤 그 doc_type 의
# custom_field yaml 이 약속한 required/constants 가 실제 청크에 실렸는지 단정한다.
# 실패가 하나라도 있으면 비-0 으로 끝난다.
#
# 사용:
#   ./parse_chunk_verify.sh                  # 전체
#   ./parse_chunk_verify.sh --only faq menu  # 일부 doc_type 만
#   ./parse_chunk_verify.sh --keep --out ./result_verify
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREPROCESSOR_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

# venv 탐색은 parse_chunk_test.sh 와 같은 순서를 쓴다.
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
exec "${PYTHON}" parse_chunk_verify.py --python "${PYTHON}" "$@"
