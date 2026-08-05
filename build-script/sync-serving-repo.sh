#!/usr/bin/env bash
set -euo pipefail

# sync-serving-repo.sh — 코드서빙 배포본을 별도 Private repo(서브모듈)로 생성/갱신한다.
#
# 동작: 배포에 필요한 것만(whitelist: genon/ + main.py + requirements.txt) 서브모듈 폴더 `code-serving/`에
#       재생성하고, docling wheel 을 packages/ 로 동봉 + requirements.txt 에 wheel 경로 append +
#       배포본 root README.md(코드서빙 사용 가이드, build-script/code-serving-README.md) 복사 +
#       requirements-dev.txt(로컬 facade/test.py 실행 전용 deps) 생성 후,
#       서브모듈(=배포본 repo genonai/doc_parser_code_serving) 안에서 commit/push 한다.
#
# 왜: 코드서빙은 GenOS 가 런타임에 배포본 repo 를 /app/src/service 로 clone 해 main.py 를 띄우고,
#     그 전에 requirements.txt 를 pip install 한다. docling(fork) 소스는 배포본에 넣지 않고 wheel 로만
#     동봉하면(소스 폴더 비노출), pip 이 requirements.txt 의 wheel 경로를 그대로 설치한다. index 불필요.
#     (wheel 빌드는 build-docling-wheel.sh)
#
# 사전 준비(1회): 배포본 repo(Private) 생성 후 서브모듈 등록
#   git submodule add git@github.com:genonai/doc_parser_code_serving.git code-serving
#
# 사용:
#   # 서브모듈 없이 로컬 조립만(검증용) — 임시폴더에 생성, push 안 함
#   bash build-script/sync-serving-repo.sh
#   # 서브모듈에 재생성 + 배포본 repo 로 push
#   PUSH=true GENON_BUILD=0 bash build-script/sync-serving-repo.sh
#
# ── 릴리스 순서 / 운영 캐비앳 ────────────────────────────────────────────────
#   1) docling/ 또는 genon/ 변경을 doc_parser 에 커밋.
#   2) 배포본 재생성 + push (docling 패치가 바뀌었으면 GENON_BUILD=N 을 올림):
#        PUSH=true GENON_BUILD=N bash build-script/sync-serving-repo.sh
#   3) (원하면) doc_parser 에서 gitlink 고정: git add code-serving && git commit -m "chore: bump code-serving submodule"
#   4) 배포처(gitea) 를 새 배포본으로 갱신 후 리비전 재배포 (배포 절차는 code-serving-README.md 참고).
#
#   - wheel 히스토리 누적: 매 릴리스 wheel(~5MB)이 배포본 repo 에 커밋되어 쌓인다. 비대해지면 LFS/릴리스 자산 전환 고려.
#   - genon/ 디스크 중복: doc_parser/genon 과 code-serving/genon(복사 산출물)이 공존한다("복사" 특성상 불가피).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 설정 (env 로 오버라이드) ────────────────────────────────────────────────
SOURCE_REF="${SOURCE_REF:-HEAD}"                     # 복사 원본 커밋
SERVING_DIR="${SERVING_DIR:-${ROOT_DIR}/code-serving}"  # 배포본 서브모듈 경로(= 빌드 출력)
SERVING_BRANCH="${SERVING_BRANCH:-main}"
GENON_BUILD="${GENON_BUILD:-0}"                      # docling wheel local segment N (build-docling-wheel.sh 로 전달)
OUT_DIR="${OUT_DIR:-}"                               # dry-run 조립 위치 지정용(비우면 mktemp 임시폴더). gitignored 경로 권장.
PUSH="${PUSH:-false}"
SERVING_README="${SERVING_README:-${ROOT_DIR}/build-script/code-serving-README.md}"  # 배포본 root 로 복사할 README 소스

# 배포본에 담을 것 (whitelist, repo 루트 기준 추적 경로). 나머지는 애초에 복사 안 함.
WHITELIST=("genon" "main.py" "requirements.txt")

# whitelist 로 가져온 뒤 배포본에서 제거할 하위 폴더 (dev/legacy/build — 서빙 런타임 무의존).
#   main.py 는 production resource/ 를 config_path 로 고정하므로 resource_dev 제외해도 무영향.
EXCLUDE_PATHS=(
  "genon/preprocessor/resource_dev"
  "genon/preprocessor/docker"
  "genon/preprocessor/facade/legacy"
  "genon/serving"
  "genon/train"
  # 2차 검토 추가 (활성 facade/main.py 무의존 검증됨)
  "genon/preprocessor/facade/legal_parser"   # 미사용 독립 파서
  "genon/tools"                              # CLI 도구(런타임 무관)
  "genon/preprocessor/resources"             # 폰트·tessdata tar (베이스 이미지에 이미 포함)
  "genon/preprocessor/scripts"               # 이미지 등록 스크립트
)

SOURCE_COMMIT="$(git -C "${ROOT_DIR}" rev-parse "${SOURCE_REF}")"

# ── 대상 결정: "루트 repo 에 등록된 서브모듈" 일 때만 거기, 아니면 dry-run(임시폴더) ──
# ※ git dir 존재만으로 판단하면 SERVING_DIR 오버라이드로 무관한 repo 를 가리킬 때
#   그 repo 의 파일이 삭제(96행)·commit·push(184~193행) 될 수 있다. 루트 인덱스에서
#   gitlink(mode 160000)로 추적되는 경로 = 등록된 서브모듈일 때만 대상으로 삼는다.
is_registered_submodule() {
  local dir="$1"
  local mode="$(git -C "${ROOT_DIR}" ls-files --stage -- "${dir}" 2>/dev/null | awk '{print $1; exit}')"
  [[ "${mode}" == "160000" ]]
}

IS_SUBMODULE=false
DRYRUN_TMP=""
if git -C "${SERVING_DIR}" rev-parse --git-dir >/dev/null 2>&1 && is_registered_submodule "${SERVING_DIR}"; then
  IS_SUBMODULE=true
  DEST="${SERVING_DIR}"
else
  # 서브모듈 없음: OUT_DIR 지정 시 그 경로, 아니면 mktemp 임시폴더(검증 후 정리).
  # ※ SERVING_DIR(code-serving/)에 직접 조립하면 doc_parser 오염 + 이후 git submodule add 경로 충돌 → 회피.
  if [[ -n "${OUT_DIR}" ]]; then
    DEST="${OUT_DIR}"; rm -rf "${DEST}"; mkdir -p "${DEST}"
  else
    DEST="$(mktemp -d)"; DRYRUN_TMP="${DEST}"
  fi
  echo "[WARN] 등록된 서브모듈이 아닙니다(${SERVING_DIR}). dry-run 으로 ${DEST} 에 조립만 합니다(push 불가)."
  echo "       배포하려면 먼저: git submodule add git@github.com:genonai/doc_parser_code_serving.git code-serving"
fi

echo "[INFO] ROOT_DIR      = ${ROOT_DIR}"
echo "[INFO] SOURCE_REF    = ${SOURCE_REF} (${SOURCE_COMMIT})"
echo "[INFO] DEST          = ${DEST}  (submodule=${IS_SUBMODULE})"
echo "[INFO] WHITELIST     = ${WHITELIST[*]}"
echo "[INFO] EXCLUDE       = ${EXCLUDE_PATHS[*]}"
echo "[INFO] GENON_BUILD   = ${GENON_BUILD}"
echo "[INFO] PUSH          = ${PUSH}"

# ── 1) DEST 재생성 (.git 보존) — whitelist 만 clean 추적본으로 export ─────────
mkdir -p "${DEST}"
find "${DEST}" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
git -C "${ROOT_DIR}" archive "${SOURCE_REF}" "${WHITELIST[@]}" | tar -x -C "${DEST}"

# 제외 하위 폴더 제거 (dev/legacy/build)
for p in "${EXCLUDE_PATHS[@]}"; do
  rm -rf "${DEST:?}/${p}"
done

# ── 2) docling wheel 빌드 → packages/ 동봉 + requirements.txt 에 경로 append ─
echo "[INFO] docling wheel 빌드 (build-docling-wheel.sh, GENON_BUILD=${GENON_BUILD}) ..."
WHEEL_OUT="$(mktemp -d)"
WHEEL_LINE="$(GENON_BUILD="${GENON_BUILD}" SOURCE_REF="${SOURCE_REF}" OUT_DIR="${WHEEL_OUT}" \
  bash "${SCRIPT_DIR}/build-docling-wheel.sh" | tee /dev/stderr | sed -n 's/^WHEEL=//p' | tail -1)"
if [[ -z "${WHEEL_LINE}" || ! -f "${WHEEL_LINE}" ]]; then
  echo "[ERROR] docling wheel 빌드 실패 (WHEEL 경로를 얻지 못함)." >&2
  exit 1
fi
WHEEL_NAME="$(basename "${WHEEL_LINE}")"

mkdir -p "${DEST}/packages"
cp "${WHEEL_LINE}" "${DEST}/packages/${WHEEL_NAME}"
rm -rf "${WHEEL_OUT}"

# requirements.txt: whitelist 로 가져온 원본(현재 빈 파일) 내용 보존 + wheel 경로 줄 append.
# DEST 는 매 실행 새로 재생성되므로 누적/중복 없음.
{
  echo ""
  echo "# ---- sync-serving-repo.sh 가 추가: docling wheel (직접 편집 금지) ----"
  echo "# docling(fork) 은 소스 대신 packages/ 의 wheel 로 동봉된다. pip 이 이 경로의 wheel 을 직접 설치한다."
  echo "./packages/${WHEEL_NAME}"
} >> "${DEST}/requirements.txt"

# ── 2b) 배포본 root README.md 복사 (코드서빙 사용 가이드) ─────────────────────
if [[ -f "${SERVING_README}" ]]; then
  cp "${SERVING_README}" "${DEST}/README.md"
  echo "[INFO] README.md 복사: ${SERVING_README} → ${DEST}/README.md"
else
  echo "[WARN] README 소스가 없어 건너뜀: ${SERVING_README}"
fi

# ── 2c) requirements-dev.txt 생성 (로컬 bare-metal 에서 facade/test.py 실행용) ──
# 운영(코드서빙)은 base 이미지에 이 deps 가 이미 포함되어 런타임은 requirements.txt(docling wheel)만
# 설치한다. 로컬에서 서빙 없이 전처리기를 직접 돌릴 때만 이 파일이 추가로 필요하다(README 부록 참고).
# DEST 는 매 실행 새로 재생성(1) 단계)되므로 누적/중복 없음.
{
  echo "# ---- sync-serving-repo.sh 가 생성: 로컬 실행 전용 (직접 편집 금지) ----"
  echo "# 로컬 bare-metal 에서 genon/preprocessor/facade/test.py(지능형/PDF) 실행 시에만 필요."
  echo "# 운영 코드서빙 base 이미지엔 이미 포함 → 런타임은 requirements.txt(docling wheel)만 설치한다."
  echo "# 사용:  uv pip install -r requirements.txt      # docling wheel"
  echo "#       uv pip install -r requirements-dev.txt   # 아래 4개"
  echo "fastapi"
  echo "httpx"
  echo "grpcio"
  echo "protobuf"
} > "${DEST}/requirements-dev.txt"
echo "[INFO] requirements-dev.txt 생성: ${DEST}/requirements-dev.txt"

# ── 3) 검증: docling 패키지 소스 부재 + wheel 동봉 + 핵심 파일 존재 ───────────
if find "${DEST}" -type f -path '*/docling/__init__.py' | grep -q .; then
  echo "[ERROR] 배포본에 docling 패키지 소스가 남아 있습니다:" >&2
  find "${DEST}" -type f -path '*/docling/__init__.py' >&2
  exit 1
fi
for f in "main.py" "genon" "packages/${WHEEL_NAME}"; do
  if [[ ! -e "${DEST}/${f}" ]]; then
    echo "[ERROR] 배포본에 ${f} 가 없습니다." >&2
    exit 1
  fi
done
if [[ -f "${SERVING_README}" && ! -f "${DEST}/README.md" ]]; then
  echo "[ERROR] README 소스는 있으나 배포본에 복사되지 않았습니다." >&2
  exit 1
fi
if [[ ! -f "${DEST}/requirements-dev.txt" ]]; then
  echo "[ERROR] requirements-dev.txt 가 생성되지 않았습니다." >&2
  exit 1
fi
echo "[SMOKE] 배포본 청결성 OK — docling 소스 없음, genon/+main.py 존재, packages/${WHEEL_NAME} 동봉됨, requirements-dev.txt 생성됨"
echo "[INFO] 배포본 조립 위치: ${DEST}  (원본 커밋 ${SOURCE_COMMIT})"

# ── 4) 서브모듈이면 commit/push ─────────────────────────────────────────────
if [[ "${IS_SUBMODULE}" != "true" ]]; then
  echo "[INFO] 서브모듈 아님 — 조립만 완료(dry-run). 서브모듈 등록 후 다시 실행하면 commit/push 가능."
  # mktemp 로 만든 임시 조립본은 검증 끝났으니 정리 (OUT_DIR 지정 시엔 남겨둠)
  [[ -n "${DRYRUN_TMP}" ]] && rm -rf "${DRYRUN_TMP}"
  exit 0
fi

git -C "${DEST}" add -A
if git -C "${DEST}" diff --cached --quiet; then
  echo "[INFO] 변경 없음 — commit/push 생략."
  exit 0
fi
git -C "${DEST}" commit -q -m "sync from doc_parser ${SOURCE_COMMIT} (docling→wheel)"
echo "[INFO] 서브모듈 commit 완료."

if [[ "${PUSH}" == "true" ]]; then
  git -C "${DEST}" push origin "HEAD:${SERVING_BRANCH}"
  echo "[INFO] push 완료 → 배포본 repo (${SERVING_BRANCH})"
  echo "[INFO] doc_parser 에서 gitlink 를 고정하려면: git add code-serving && git commit"
else
  echo "[INFO] PUSH=false — 서브모듈에 commit 만 함. push 하려면 PUSH=true 로 재실행."
fi
