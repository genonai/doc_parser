#!/usr/bin/env bash
set -euo pipefail

# sync-serving-repo.sh — 코드서빙용 git repo(복사본)를 생성한다.
#
# 동작: 현재 repo(doc_parser)의 추적 파일을 복사하되 **docling/ 소스(+부속 파일·build-script)를 제외**하고,
#       그 자리에 docling wheel 을 packages/ 로 동봉 + requirements.txt 에 그 wheel 경로를 기입한 뒤
#       별도 serving repo 로 push 한다.
#
# 왜: 코드서빙은 GenOS 가 런타임에 serving repo 를 /app/src/service 로 clone 해 main.py 를 띄우고,
#     그 전에 requirements.txt 를 pip install 한다. docling(fork) 소스를 복사본에서 빼고 wheel 로만
#     동봉하면(소스 폴더 비노출), pip 이 requirements.txt 의 wheel 경로를 그대로 설치한다. index 불필요.
#     (wheel 빌드는 build-docling-wheel.sh)
#
# 사용:
#   # 복사본만 로컬 생성(검증용, push 안 함)
#   bash build-script/sync-serving-repo.sh
#   # serving repo 로 push
#   PUSH=true SERVING_REPO_URL=git@github.com:genonai/doc-parser-serving.git \
#     GENON_BUILD=0 bash build-script/sync-serving-repo.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 설정 (env 로 오버라이드) ────────────────────────────────────────────────
SOURCE_REF="${SOURCE_REF:-HEAD}"                 # 복사 원본 커밋
SERVING_REPO_URL="${SERVING_REPO_URL:-}"         # push 대상 (PUSH=true 시 필수)
SERVING_BRANCH="${SERVING_BRANCH:-main}"
GENON_BUILD="${GENON_BUILD:-0}"                  # docling wheel local segment N (build-docling-wheel.sh 로 전달)
OUT_DIR="${OUT_DIR:-${ROOT_DIR}/dist-serving}"   # 복사본이 만들어지는 로컬 경로
PUSH="${PUSH:-false}"

# 복사본에서 제외할 경로 (repo 루트 기준).
#   - docling/ : fork 소스 (런타임에 private index 에서 genon-docling 으로 설치)
#   - docling 프로젝트 부속 파일/디렉토리 : 이 repo 가 docling fork 임을 드러내고 docling 의
#     메타데이터/의존성 목록/문서/테스트를 노출하므로 함께 제외. genon 자산은 main.py + genon/ 뿐.
# ※ 여기에 없는 최상위 항목(main.py, genon/, build-script/, requirements.txt 등)은 복사본에 유지된다.
EXCLUDE_PATHS=(
  # fork 소스 (대신 packages/ 에 wheel 로 동봉)
  "docling"
  # genon 빌드 도구 (런타임 불필요 — 하니스는 베이스 이미지에 이미 포함).
  #   build-docling-wheel.sh / sync-serving-repo.sh / code-serving Dockerfile·build.config 등
  #   genon 빌드 방식이 노출되므로 복사본에서 제외.
  "build-script"
  # docling 패키징/락 (name=docling, IBM 저자, 전체 의존성 트리 노출)
  "pyproject.toml"
  "uv.lock"
  # docling 프로젝트 문서/설정 (fork 정체 노출)
  "README.md"
  "Dockerfile"
  "CHANGELOG.md"
  "CITATION.cff"
  "CODE_OF_CONDUCT.md"
  "CONTRIBUTING.md"
  "MAINTAINERS.md"
  "LICENSE"
  "mkdocs.yml"
  "pytest.ini"
  ".env.example"
  ".gitattributes"
  ".pre-commit-config.yaml"
  # docling 문서/테스트/CI/부가 디렉토리
  "docs"
  "tests"
  ".actor"
  ".github"
)

echo "[INFO] ROOT_DIR         = ${ROOT_DIR}"
echo "[INFO] SOURCE_REF       = ${SOURCE_REF}"
echo "[INFO] OUT_DIR          = ${OUT_DIR}"
echo "[INFO] EXCLUDE          = ${EXCLUDE_PATHS[*]}"
echo "[INFO] GENON_BUILD      = ${GENON_BUILD}"
echo "[INFO] PUSH             = ${PUSH}"

SOURCE_COMMIT="$(git -C "${ROOT_DIR}" rev-parse "${SOURCE_REF}")"

# ── 1) 추적 파일 export → docling/ 제외 ─────────────────────────────────────
rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}"
git -C "${ROOT_DIR}" archive "${SOURCE_REF}" | tar -x -C "${OUT_DIR}"

for p in "${EXCLUDE_PATHS[@]}"; do
  rm -rf "${OUT_DIR:?}/${p}"
done

# ── 2) docling wheel 빌드 → packages/ 동봉 + requirements.txt 에 경로 기입 ───
# 런타임에 플랫폼이 requirements.txt 를 pip install 하면, pip 이 아래 경로의 wheel 을 그대로 설치한다.
# (index·find-links 불필요 — requirements.txt 의 wheel 경로 한 줄로 성립)
echo "[INFO] docling wheel 빌드 (build-docling-wheel.sh, GENON_BUILD=${GENON_BUILD}) ..."
WHEEL_OUT="$(mktemp -d)"
WHEEL_LINE="$(GENON_BUILD="${GENON_BUILD}" SOURCE_REF="${SOURCE_REF}" OUT_DIR="${WHEEL_OUT}" \
  bash "${SCRIPT_DIR}/build-docling-wheel.sh" | tee /dev/stderr | sed -n 's/^WHEEL=//p' | tail -1)"
if [[ -z "${WHEEL_LINE}" || ! -f "${WHEEL_LINE}" ]]; then
  echo "[ERROR] docling wheel 빌드 실패 (WHEEL 경로를 얻지 못함)." >&2
  exit 1
fi
WHEEL_NAME="$(basename "${WHEEL_LINE}")"

mkdir -p "${OUT_DIR}/packages"
cp "${WHEEL_LINE}" "${OUT_DIR}/packages/${WHEEL_NAME}"
rm -rf "${WHEEL_OUT}"

# requirements.txt: 복사본에 포함된 원본(doc_parser 루트 requirements.txt, 현재 빈 파일) 내용을 보존하고,
# 그 뒤에 docling wheel 경로 줄을 append 한다. (루트에 서빙용 deps 가 생겨도 유실 안 되게)
# OUT_DIR 은 매 실행 git archive 로 새로 추출되므로 누적/중복 걱정 없음.
REQ_FILE="${OUT_DIR}/requirements.txt"
{
  echo ""
  echo "# ---- sync-serving-repo.sh 가 추가: docling wheel (직접 편집 금지) ----"
  echo "# docling(fork) 은 소스 대신 packages/ 의 wheel 로 동봉된다. pip 이 이 경로의 wheel 을 직접 설치한다."
  echo "./packages/${WHEEL_NAME}"
} >> "${REQ_FILE}"

# ── 3) 검증: 복사본에 docling 소스가 없고 wheel 이 동봉돼야 한다 ─────────────
if [[ -e "${OUT_DIR}/docling" ]]; then
  echo "[ERROR] 복사본에 docling/ 이 남아 있습니다." >&2
  exit 1
fi
# genon 자체 파일(genon/tools/.../docling_viewer.py 등)은 제외하고, docling 패키지 소스만 검사
if find "${OUT_DIR}" -type f -path '*/docling/__init__.py' | grep -q .; then
  echo "[ERROR] 복사본에 docling 패키지 소스가 남아 있습니다:" >&2
  find "${OUT_DIR}" -type f -path '*/docling/__init__.py' >&2
  exit 1
fi
if [[ ! -f "${OUT_DIR}/packages/${WHEEL_NAME}" ]]; then
  echo "[ERROR] packages/ 에 wheel 이 없습니다: ${WHEEL_NAME}" >&2
  exit 1
fi
echo "[SMOKE] 복사본 청결성 OK — docling 소스 없음, packages/${WHEEL_NAME} 동봉됨"
echo "[INFO] 복사본 생성 위치: ${OUT_DIR}  (원본 커밋 ${SOURCE_COMMIT})"

# ── 4) serving repo 로 push ─────────────────────────────────────────────────
if [[ "${PUSH}" == "true" ]]; then
  if [[ -z "${SERVING_REPO_URL}" ]]; then
    echo "[ERROR] PUSH=true 인데 SERVING_REPO_URL 이 비어 있습니다." >&2
    exit 1
  fi
  CLONE_DIR="$(mktemp -d)"
  trap 'rm -rf "${CLONE_DIR}"' EXIT
  echo "[INFO] serving repo clone: ${SERVING_REPO_URL} (branch ${SERVING_BRANCH})"
  if git clone --branch "${SERVING_BRANCH}" "${SERVING_REPO_URL}" "${CLONE_DIR}" 2>/dev/null; then
    :
  else
    echo "[INFO] 브랜치가 없어 새로 초기화합니다."
    git clone "${SERVING_REPO_URL}" "${CLONE_DIR}" || { mkdir -p "${CLONE_DIR}" && git -C "${CLONE_DIR}" init -q; }
    git -C "${CLONE_DIR}" checkout -q -B "${SERVING_BRANCH}"
  fi
  # .git 은 보존, 나머지는 복사본으로 미러(삭제 포함)
  rsync -a --delete --exclude='.git' "${OUT_DIR}/" "${CLONE_DIR}/"
  git -C "${CLONE_DIR}" add -A
  if git -C "${CLONE_DIR}" diff --cached --quiet; then
    echo "[INFO] 변경 없음 — push 생략."
  else
    git -C "${CLONE_DIR}" commit -q -m "sync from doc_parser ${SOURCE_COMMIT} (docling excluded)"
    git -C "${CLONE_DIR}" push origin "${SERVING_BRANCH}"
    echo "[INFO] push 완료 → ${SERVING_REPO_URL} (${SERVING_BRANCH})"
  fi
else
  echo "[INFO] PUSH=false — 로컬 복사본만 생성. push 하려면 PUSH=true + SERVING_REPO_URL 설정."
fi
