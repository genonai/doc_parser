#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

command -v git >/dev/null 2>&1 || {
  echo "Required command not found: git" >&2
  exit 1
}

command -v rsync >/dev/null 2>&1 || {
  echo "Required command not found: rsync" >&2
  exit 1
}

# 이 스크립트는 build-script/ 안에 있으므로 자기 위치가 저장소 루트가 아니다.
# 루트는 git 에게 묻는다. 스크립트를 또 옮기거나 어느 디렉터리에서 호출하든 결과가 같다.
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel)" || {
  echo "Not inside a git repository: ${SCRIPT_DIR}" >&2
  exit 1
}

SOURCE_DIR="${REPO_ROOT}/genon/preprocessor"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <destination-folder-name>" >&2
  echo "Example: $0 patch_20260826" >&2
  exit 1
fi

DEST_NAME="$1"

if [[ -z "${DEST_NAME}" || "${DEST_NAME}" == "." || "${DEST_NAME}" == ".." || "${DEST_NAME}" == */* ]]; then
  echo "Enter a folder name only, without a path: ${DEST_NAME}" >&2
  exit 1
fi

DEST_DIR="${REPO_ROOT}/dist/${DEST_NAME}"

if [[ ! -d "${SOURCE_DIR}" ]]; then
  echo "Source directory not found: ${SOURCE_DIR}" >&2
  exit 1
fi

mkdir -p "${DEST_DIR}"

(
  cd "${SOURCE_DIR}"
  git ls-files -z -- '*.py' '*.md' '*.yaml' '*.sh' \
    | rsync -a --from0 --files-from=- ./ "${DEST_DIR}/"
)

FILE_COUNT="$(
  cd "${SOURCE_DIR}"
  git ls-files -- '*.py' '*.md' '*.yaml' '*.sh' | wc -l | tr -d ' '
)"

echo "Patch created: ${DEST_DIR}"
echo "Copied files: ${FILE_COUNT}"
