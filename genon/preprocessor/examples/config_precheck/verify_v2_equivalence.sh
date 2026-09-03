#!/usr/bin/env bash
# v1 ↔ v2 병행 검증. 전환 전에 v2 로 옮겨도 결과가 같은지 확인한다.
#
#   ./verify_v2_equivalence.sh                         # 저장소 resource/
#   ./verify_v2_equivalence.sh /path/to/site/resource  # 현장 설정 디렉터리
#
# 하나라도 어긋나면 비-0 으로 끝난다. 전환은 이게 전부 통과한 뒤에 한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREPROCESSOR_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

# yaml 만 있으면 되므로 어떤 python 이든 상관없다(무거운 의존성 없음).
if [ -z "${PYTHON:-}" ]; then
  if [ -x "${PREPROCESSOR_DIR}/.venv/bin/python" ]; then
    PYTHON="${PREPROCESSOR_DIR}/.venv/bin/python"
  elif [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

exec "${PYTHON}" "${SCRIPT_DIR}/verify_v2_equivalence.py" "$@"
