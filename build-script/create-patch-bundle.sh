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

# 번들에 싣는 폴더. 코드서빙 서버(루트 main.py)가 실제로 쓰는 것과 현장 검증 스크립트만 둔다.
#   activities/  파싱·청킹 파사드(parse.py·chunk.py) — 루트 main.py 와 Temporal 워커가 올린다
#   facade/      루트 main.py 가 올리는 나머지 processor 3종 + 옛 이름 별칭 2개
#   processing/  facade 가 호출하는 처리 라이브러리
#   resource/    표준 설정과 LLM 프롬프트(.md 도 런타임 입력이다)
#   src/         루트 main.py 가 sys.path 에 넣고 logger·settings·minio 유틸을 불러온다
#   examples/    고객이 붙여 쓰는 훅·설정 예제
#   tools/       현장에서 실행하는 검증 스크립트(설정 점검, 골든 기준선 등)
# 그 밖(tests, manual, sample_files, docker, scripts, configs 등)은 서버가 쓰지 않으므로 싣지 않는다.
# 특히 sample_files/monimo 는 고객사 실 문서라 번들로 다른 현장에 나가면 안 된다.
# resource/ 는 따로 복사한다. --site 를 주면 그 원천이 사이트 완성본(genon/sites/<site>/resource/)으로 바뀐다.
# 완성본은 표준 사본에 사이트 소유 파일을 더한 전체 설정 폴더다(genon/sites/README.md).
PATCH_DIRS=(activities facade processing src examples tools)
PATCH_EXTS=(py md yaml sh)

# 폴더 x 확장자 조합의 git pathspec. `**/` 는 0개 이상의 하위 폴더에 대응한다.
PATCH_PATHSPECS=()
for dir in "${PATCH_DIRS[@]}"; do
  for ext in "${PATCH_EXTS[@]}"; do
    PATCH_PATHSPECS+=(":(glob)${dir}/**/*.${ext}")
  done
done
RESOURCE_PATHSPECS=()
for ext in "${PATCH_EXTS[@]}"; do
  RESOURCE_PATHSPECS+=(":(glob)**/*.${ext}")
done

usage() {
  echo "Usage: $0 <destination-folder-name> [--site <site>]" >&2
  echo "Example: $0 patch_20260826" >&2
  echo "Example: $0 patch_20260826 --site monimo" >&2
  exit 1
}

DEST_NAME=""
SITE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site)
      [[ $# -ge 2 && -n "$2" ]] || usage
      SITE="$2"
      shift 2
      ;;
    -*)
      usage
      ;;
    *)
      [[ -z "${DEST_NAME}" ]] || usage
      DEST_NAME="$1"
      shift
      ;;
  esac
done
[[ -n "${DEST_NAME}" ]] || usage

if [[ -z "${DEST_NAME}" || "${DEST_NAME}" == "." || "${DEST_NAME}" == ".." || "${DEST_NAME}" == */* ]]; then
  echo "Enter a folder name only, without a path: ${DEST_NAME}" >&2
  exit 1
fi

DEST_DIR="${REPO_ROOT}/dist/${DEST_NAME}"

RESOURCE_SRC="${SOURCE_DIR}/resource"
if [[ -n "${SITE}" ]]; then
  if [[ "${SITE}" == */* || "${SITE}" == "." || "${SITE}" == ".." ]]; then
    echo "Enter a site name only, without a path: ${SITE}" >&2
    exit 1
  fi
  RESOURCE_SRC="${REPO_ROOT}/genon/sites/${SITE}/resource"
  if [[ ! -d "${RESOURCE_SRC}" ]]; then
    echo "Site resource directory not found: ${RESOURCE_SRC}" >&2
    exit 1
  fi
fi

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
RESOURCE_LIST="$(mktemp)"
trap 'rm -f "${FILE_LIST}" "${RESOURCE_LIST}"' EXIT

(
  cd "${SOURCE_DIR}"
  git ls-files -z -- "${PATCH_PATHSPECS[@]}"
) > "${FILE_LIST}"

(
  cd "${RESOURCE_SRC}"
  git ls-files -z -- "${RESOURCE_PATHSPECS[@]}"
) > "${RESOURCE_LIST}"

(
  cd "${SOURCE_DIR}"
  rsync -a --from0 --files-from="${FILE_LIST}" ./ "${DEST_DIR}/"
)

mkdir -p "${DEST_DIR}/resource"
(
  cd "${RESOURCE_SRC}"
  rsync -a --from0 --files-from="${RESOURCE_LIST}" ./ "${DEST_DIR}/resource/"
)

# NUL 구분이라 줄 수가 아니라 구분자 개수를 센다.
FILE_COUNT="$(cat "${FILE_LIST}" "${RESOURCE_LIST}" | tr -cd '\0' | wc -c | tr -d ' ')"

echo "Patch created: ${DEST_DIR}"
echo "Resource source: ${RESOURCE_SRC#"${REPO_ROOT}/"}"
echo "Copied files: ${FILE_COUNT}"

# 폴더별 개수도 복사에 쓴 같은 목록에서 센다.
for dir in "${PATCH_DIRS[@]}"; do
  printf '  %-12s %s\n' "${dir}/" "$(tr '\0' '\n' < "${FILE_LIST}" | grep -c "^${dir}/" || true)"
done
printf '  %-12s %s\n' "resource/" "$(tr -cd '\0' < "${RESOURCE_LIST}" | wc -c | tr -d ' ')"
