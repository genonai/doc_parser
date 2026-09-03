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

# 기록 전 안전 장치가 매퍼를 실제로 만들어 대조하므로 전처리기 의존성이 필요하다.
# 매퍼를 못 불러오면 그 설정은 FAIL 로 잡혀 기록되지 않는다(조용히 통과하지 않는다).
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
