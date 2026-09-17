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

# 번들에서 빼는 것. git pathspec 이라 하위 전체가 걸린다.
#   resource_dev/  로컬 개발용 설정이고 **실 API 키가 커밋되어 있다**. 운영에 얹을 값이
#                  아니므로, 현장에 전달하는 산출물에 키가 섞여 나가지 않게 제외한다.
#                  로컬 검증용으로 필요하면 저장소에서 직접 쓴다(번들 대상이 아니다).
PATCH_EXCLUDES=(':(exclude)resource_dev/')

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

# 이미 내용이 있는 곳에는 덮어쓰지 않는다. rsync 는 지우지 않으므로, 번들 대상에서 빠진
# 파일(제외 목록에 추가한 것, 저장소에서 삭제한 것)이 옛 사본으로 남아 **저장소와 어긋난
# 번들**이 된다. 실제로 resource_dev 를 제외 목록에 넣은 뒤 같은 이름으로 다시 만들었을 때
# 키가 든 옛 파일이 그대로 남았다. 지우는 것은 호출자가 명시적으로 하게 한다.
if [[ -d "${DEST_DIR}" ]] && [[ -n "$(ls -A "${DEST_DIR}" 2>/dev/null)" ]]; then
  echo "Destination already has files: ${DEST_DIR}" >&2
  echo "Remove it first so the bundle matches the repository:" >&2
  echo "  rm -rf ${DEST_DIR}" >&2
  exit 1
fi

mkdir -p "${DEST_DIR}"

# 목록은 **한 번만** 만든다. 예전에는 복사와 개수 세기가 각자 git 을 불러, 대상 조건이
# 바뀌면 한쪽만 고쳐져 "복사한 것과 보고한 개수"가 갈릴 수 있었다.
FILE_LIST="$(mktemp)"
trap 'rm -f "${FILE_LIST}"' EXIT

(
  cd "${SOURCE_DIR}"
  git ls-files -z -- '*.py' '*.md' '*.yaml' '*.sh' "${PATCH_EXCLUDES[@]}"
) > "${FILE_LIST}"

(
  cd "${SOURCE_DIR}"
  rsync -a --from0 --files-from="${FILE_LIST}" ./ "${DEST_DIR}/"
)

# NUL 구분이라 줄 수가 아니라 구분자 개수를 센다.
FILE_COUNT="$(tr -cd '\0' < "${FILE_LIST}" | wc -c | tr -d ' ')"

echo "Patch created: ${DEST_DIR}"
echo "Copied files: ${FILE_COUNT}"
