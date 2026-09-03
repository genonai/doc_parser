#!/usr/bin/env bash
# v1 custom_field yaml → v2 변환. 기본은 미리보기이고 --write 로만 기록한다.
#
#   ./migrate_to_v2.sh                         # 미리보기
#   ./migrate_to_v2.sh --write                 # 실제 기록
#
# 변환할 수 없는 설정이 있으면 비-0 으로 끝난다.
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

exec "${PYTHON}" "${SCRIPT_DIR}/migrate_to_v2.py" "$@"
