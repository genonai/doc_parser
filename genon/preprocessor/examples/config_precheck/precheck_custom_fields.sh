#!/usr/bin/env bash
# 배포 전 custom_fields 설정 점검. 파싱도 LLM 호출도 하지 않고 yaml 만 읽는다.
#
#   ./precheck_custom_fields.sh                         # 저장소 resource/
#   ./precheck_custom_fields.sh /path/to/site/resource  # 현장 설정 디렉터리
#
# 기동을 막는 문제가 하나라도 있으면 비-0 으로 끝난다.
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

exec "${PYTHON}" "${SCRIPT_DIR}/precheck_custom_fields.py" "$@"
