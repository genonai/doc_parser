#!/usr/bin/env bash
set -euo pipefail

# sync-serving-repo.sh — 코드서빙 배포본을 별도 공개 repo로 생성/갱신한다.
#
# 동작: 배포에 필요한 것만(whitelist: genon/ + main.py + requirements.txt) 서브모듈 폴더 `code-serving/`에
#       재생성하고, docling wheel 을 packages/ 로 동봉 + requirements.txt 에 wheel 경로 append +
#       배포본 root README.md(코드서빙 사용 가이드, build-script/code-serving-README.md) 복사 +
#       requirements-dev.txt(로컬 facade/test.py 실행 전용 deps)와 .gitignore 생성 후,
#       배포본 repo(genonai/doc_parser_code_serving) 안에서 commit/push 한다.
#
# 왜: 코드서빙은 GenOS 가 런타임에 배포본 repo 를 /app/src/service 로 clone 해 main.py 를 띄우고,
#     그 전에 requirements.txt 를 pip install 한다. docling(fork) 소스는 배포본에 넣지 않고 wheel 로만
#     동봉하면(소스 폴더 비노출), pip 이 requirements.txt 의 wheel 경로를 그대로 설치한다. index 불필요.
#     (wheel 빌드는 build-docling-wheel.sh)
#
# 사전 준비(1회): 공개 배포본 repo 를 로컬 클론 (doc_parser 는 이 폴더를 추적하지 않음 — .gitignore)
#   git clone git@github.com:genonai/doc_parser_code_serving.git code-serving
#   → push 는 SERVING_DIR 의 origin 이 배포본 repo 와 일치하는 클론일 때만 허용된다(무관 repo 보호).
#     서브모듈로 등록할 필요 없음.
#
# 사용:
#   # 클론 없이 로컬 조립만(검증용) — 임시폴더에 생성, push 안 함
#   bash build-script/sync-serving-repo.sh
#   # 배포본 클론에 재생성 + 배포본 repo 로 push
#   PUSH=true GENON_BUILD=0 bash build-script/sync-serving-repo.sh
#
# ── 버전 정합 ────────────────────────────────────────────────────────────────
#   원본(doc_parser)의 git 릴리스 태그가 버전의 단일 진실 소스다. 이 스크립트는
#   배포본에 (1) 같은 이름의 미러 태그를 push 하고 (2) VERSION 스탬프 파일을 동봉해
#   배포본 커밋 ↔ 원본 릴리스를 1급으로 연결한다. VERSION 은 SOURCE_REF 에서 자동
#   파생(git describe)되며, 릴리스 태그 커밋이 아니면 dev 빌드로 태깅된다.
#
# ── 릴리스 순서 / 운영 캐비앳 ────────────────────────────────────────────────
#   1) docling/ 또는 genon/ 변경을 doc_parser 에 커밋하고 릴리스 태그(예: 2.2.5) 부여.
#   2) 배포본 재생성 + push + 미러 태그 (docling 패치가 바뀌었으면 GENON_BUILD=N 을 올림):
#        PUSH=true VERSION=2.2.5 GENON_BUILD=N bash build-script/sync-serving-repo.sh
#        → 배포본 push + 미러 태그 2.2.5 push + VERSION 스탬프 동봉
#        (VERSION 을 생략하면 git describe 로 자동 파생. 태그 커밋에서 실행하면 정확히 그 태그.)
#        원본↔배포본 연결은 배포본 커밋 메시지의 SOURCE_COMMIT + 미러 태그 + VERSION 스탬프로 기록된다
#        (doc_parser 는 code-serving 을 추적하지 않으므로 gitlink 고정 단계는 없다).
#   3) 배포처(gitea) 를 새 배포본으로 갱신 후 리비전 재배포 (배포 절차는 code-serving-README.md 참고).
#
#   - wheel 히스토리 누적: 매 릴리스 wheel(~5MB)이 배포본 repo 에 커밋되어 쌓인다. 비대해지면 LFS/릴리스 자산 전환 고려.
#   - genon/ 디스크 중복: doc_parser/genon 과 code-serving/genon(복사 산출물)이 공존한다("복사" 특성상 불가피).
#   - 재태깅: 이미 존재하는 태그를 다른 커밋으로 옮기려 하면 에러로 중단된다. 의도적일 때만 FORCE_TAG=true.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 설정 (env 로 오버라이드) ────────────────────────────────────────────────
SOURCE_REF="${SOURCE_REF:-HEAD}"                     # 복사 원본 커밋
SERVING_DIR="${SERVING_DIR:-${ROOT_DIR}/code-serving}"  # 배포본 repo 의 로컬 클론(= 빌드 출력). doc_parser 는 추적 안 함(.gitignore).
SERVING_BRANCH="${SERVING_BRANCH:-main}"
# push 허용 판정용: SERVING_DIR 의 origin 이 이 repo 와 일치할 때만 commit/push(무관 repo 보호).
SERVING_REMOTE="${SERVING_REMOTE:-git@github.com:genonai/doc_parser_code_serving.git}"
GENON_BUILD="${GENON_BUILD:-0}"                      # docling wheel local segment N (build-docling-wheel.sh 로 전달)
OUT_DIR="${OUT_DIR:-}"                               # dry-run 조립 위치 지정용(비우면 mktemp 임시폴더). gitignored 경로 권장.
PUSH="${PUSH:-false}"
SERVING_README="${SERVING_README:-${ROOT_DIR}/build-script/code-serving-README.md}"  # 배포본 root 로 복사할 README 소스

# 미러 태그로 쓸 버전. 비우면 SOURCE_REF 에서 자동 파생(태그 커밋이면 정확히 그 태그, 아니면 2.2.4-14-gSHA).
VERSION="${VERSION:-$(git -C "${ROOT_DIR}" describe --tags "${SOURCE_REF}" 2>/dev/null || true)}"
FORCE_TAG="${FORCE_TAG:-false}"                      # true 일 때만 기존 태그를 다른 커밋으로 강제 이동(-f)

# 릴리스 위생: SOURCE_REF 가 정확히 릴리스 태그 커밋이 아니면 경고(dev 빌드로 태깅됨).
if ! git -C "${ROOT_DIR}" describe --exact-match --tags "${SOURCE_REF}" >/dev/null 2>&1; then
  echo "[WARN] SOURCE_REF(${SOURCE_REF}) 가 릴리스 태그 커밋이 아닙니다 — dev 빌드로 태깅됩니다(VERSION=${VERSION:-<없음>})."
fi

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
  # 사내 전용 문서 — 공개 배포본에 나갈 필요가 없고, 배포본에 없는 폴더(build-script/·docling/·docs/)를
  # 안내해 오히려 혼란을 준다. 코드서빙 사용/설치 안내는 배포본 root README.md 가 담당한다.
  "genon/README.md"                          # 사내 개발 문서(이미지 빌드·paddle·vllm)
  "genon/MAINTAINERS.md"                     # docling 원본 메인테이너 잔존물
  "genon/dotsocr_vllm_max_num_seqs.md"       # 사내 모델 서빙 튜닝 메모
)

SOURCE_COMMIT="$(git -C "${ROOT_DIR}" rev-parse "${SOURCE_REF}")"

# ── 대상 결정: "배포본 repo 의 로컬 클론" 일 때만 거기, 아니면 dry-run(임시폴더) ──
# ※ git dir 존재만으로 판단하면 SERVING_DIR 오버라이드로 무관한 repo 를 가리킬 때
#   그 repo 의 파일이 삭제·commit·push 될 수 있다. 그래서 SERVING_DIR 의 origin 원격이
#   배포본 repo(SERVING_REMOTE)와 같은 repo 를 가리킬 때만 대상으로 삼는다.
#   (doc_parser 는 code-serving 을 서브모듈로 추적하지 않음 — 로컬 클론일 뿐, .gitignore 처리.)

# github 원격 URL 을 "owner/repo" 로 환원(ssh/https·.git 유무·트레일링슬래시 무시).
normalize_remote() {
  local url="$1"
  url="${url%.git}"; url="${url%/}"
  url="${url#git@github.com:}"
  url="${url#https://github.com/}"
  url="${url#ssh://git@github.com/}"
  printf '%s' "${url}"
}

# SERVING_DIR 이 배포본 repo 의 클론인가(origin 이 SERVING_REMOTE 와 동일 repo).
is_serving_clone() {
  local dir="$1"
  git -C "${dir}" rev-parse --git-dir >/dev/null 2>&1 || return 1
  local origin; origin="$(git -C "${dir}" remote get-url origin 2>/dev/null || true)"
  [[ -n "${origin}" ]] || return 1
  [[ "$(normalize_remote "${origin}")" == "$(normalize_remote "${SERVING_REMOTE}")" ]]
}

PUSH_TARGET_OK=false
DRYRUN_TMP=""
if is_serving_clone "${SERVING_DIR}"; then
  PUSH_TARGET_OK=true
  DEST="${SERVING_DIR}"
else
  # 배포본 클론 아님: OUT_DIR 지정 시 그 경로, 아니면 mktemp 임시폴더(검증 후 정리).
  if [[ -n "${OUT_DIR}" ]]; then
    DEST="${OUT_DIR}"; rm -rf "${DEST}"; mkdir -p "${DEST}"
  else
    DEST="$(mktemp -d)"; DRYRUN_TMP="${DEST}"
  fi
  echo "[WARN] SERVING_DIR(${SERVING_DIR}) 이 배포본 repo 의 클론이 아닙니다. dry-run 으로 ${DEST} 에 조립만 합니다(push 불가)."
  echo "       배포하려면:  git clone ${SERVING_REMOTE} code-serving"
  echo "       (code-serving/ 은 .gitignore 로 doc_parser 가 추적하지 않습니다. 서브모듈 등록 불필요.)"
fi

echo "[INFO] ROOT_DIR      = ${ROOT_DIR}"
echo "[INFO] SOURCE_REF    = ${SOURCE_REF} (${SOURCE_COMMIT})"
echo "[INFO] DEST          = ${DEST}  (push_target_ok=${PUSH_TARGET_OK})"
echo "[INFO] VERSION       = ${VERSION:-<없음>}  (force_tag=${FORCE_TAG})"
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
# docling wheel 은 항상 로컬 작업 트리(현재 docling/ 폴더) 기준으로 빌드된다(SOURCE_REF 무관).
# 릴리스 위생: 로컬 docling 이 SOURCE_REF(genon/미러 태그 기준 커밋)와 다르면, 동봉될 wheel 이
# 미러 태그가 가리키는 커밋과 불일치할 수 있어 경고한다(기존 [WARN] 톤과 동일).
if ! git -C "${ROOT_DIR}" diff --quiet "${SOURCE_REF}" -- docling \
   || [[ -n "$(git -C "${ROOT_DIR}" ls-files --others --exclude-standard docling)" ]]; then
  echo "[WARN] 로컬 docling/ 이 ${SOURCE_REF} 와 다릅니다 — wheel 은 로컬 작업 트리 기준으로 빌드됩니다(미러 태그 커밋과 불일치 가능)."
fi
echo "[INFO] docling wheel 빌드 (build-docling-wheel.sh, GENON_BUILD=${GENON_BUILD}, 로컬 작업 트리) ..."
WHEEL_OUT="$(mktemp -d)"
WHEEL_LINE="$(GENON_BUILD="${GENON_BUILD}" OUT_DIR="${WHEEL_OUT}" \
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

# ── 2c) requirements-dev.txt 생성 (로컬 bare-metal 에서 facade 직접 실행용) ──
# 운영(코드서빙)은 base 이미지에 이 deps 가 이미 포함되어 런타임은 requirements.txt(docling wheel)만
# 설치한다. 로컬에서 서빙 없이 전처리기를 직접 돌릴 때만 이 파일이 추가로 필요하다(README 부록 참고).
# ⚠️ intelligent facade 는 fastapi/httpx 만으로 import 되지만, parser·chunking·attachment·convert 는
#    모듈 최상위에서 fitz(pymupdf)/langchain*/markdown2/pydub 를 import 한다. 이들이 없으면 로컬에서
#    facade import 자체가 실패하므로 함께 담는다 (외부 개발자의 주 작업 대상이 parser/chunker).
# DEST 는 매 실행 새로 재생성(1) 단계)되므로 누적/중복 없음.
{
  echo "# ---- sync-serving-repo.sh 가 생성: 로컬 실행 전용 (직접 편집 금지) ----"
  echo "# 로컬 bare-metal 에서 genon/preprocessor/facade/*_processor.py 를 직접 실행할 때만 필요."
  echo "# 운영 코드서빙 base 이미지엔 이미 포함 → 런타임은 requirements.txt(docling wheel)만 설치한다."
  echo "# 사용:  uv pip install -r requirements.txt      # docling wheel"
  echo "#       uv pip install -r requirements-dev.txt   # 아래 목록"
  echo ""
  echo "# 공통(모든 facade)"
  echo "fastapi"
  echo "httpx"
  echo "grpcio"
  echo "protobuf"
  echo ""
  echo "# parser / chunking / attachment / convert facade 의 모듈 최상위 import"
  echo "pymupdf"                  # import fitz
  echo "langchain-community"
  echo "langchain-core"
  echo "langchain-text-splitters"
  echo "markdown2"
  echo "pydub"
  echo "chardet"                  # parser/attachment 의 인코딩 감지 (없으면 import 시 RuntimeError)
} > "${DEST}/requirements-dev.txt"
echo "[INFO] requirements-dev.txt 생성: ${DEST}/requirements-dev.txt"

# ── 2d) 배포본 root .gitignore 생성 ─────────────────────────────────────────
# 로컬 개발 산출물과 오프라인 설치 키트가 실수로 gitea 배포 저장소에 커밋되지 않도록 한다.
# DEST 는 매 실행 새로 재생성되므로 이 파일도 배포 스크립트가 단일 정본으로 관리한다.
cat > "${DEST}/.gitignore" <<'EOF'
# Python virtual environments and caches
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# IDE / OS files
.DS_Store
.idea/

# Local test outputs
genon/preprocessor/facade/result.json
genon/preprocessor/examples/parse_chunk/result_parse_chunk/

# Platform-specific offline development kits (distribute separately; do not commit)
offline-dev-kit/
wheelhouse/
EOF
echo "[INFO] .gitignore 생성: ${DEST}/.gitignore"

# ── 2e) VERSION 스탬프 생성 (배포본 ↔ 원본 릴리스 연결 + 산출물 자가 식별) ──────
# 원본 릴리스 태그·SHA·wheel 을 배포본에 새긴다. 배포된 산출물이 자기 버전을 식별 가능.
# ⚠️ 결정적 값(원본에서 파생)만 사용 — now() 타임스탬프 금지. 넣으면 매 실행 diff 가 생겨
#    아래 4) 의 "변경 없음 스킵"이 깨진다. 날짜는 원본 커밋 날짜(고정값)만 쓴다.
SOURCE_COMMIT_DATE="$(git -C "${ROOT_DIR}" show -s --format=%cI "${SOURCE_COMMIT}")"
# 값은 셸 보간이 아니라 env 로 넘겨 python 이 인코딩한다(따옴표 escaping + 셸 주입 여지 제거).
VERSION="${VERSION}" SOURCE_COMMIT="${SOURCE_COMMIT}" \
SOURCE_COMMIT_DATE="${SOURCE_COMMIT_DATE}" WHEEL_NAME="${WHEEL_NAME}" \
DEST_VERSION="${DEST}/VERSION" \
python3 -c '
import json, os
with open(os.environ["DEST_VERSION"], "w") as f:
    json.dump({
        "source_version": os.environ["VERSION"],
        "source_commit": os.environ["SOURCE_COMMIT"],
        "source_commit_date": os.environ["SOURCE_COMMIT_DATE"],
        "docling_wheel": os.environ["WHEEL_NAME"],
    }, f, indent=2, ensure_ascii=False)
    f.write("\n")
'
echo "[INFO] VERSION 스탬프 생성: ${DEST}/VERSION (source_version=${VERSION:-<없음>})"

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
if [[ ! -f "${DEST}/.gitignore" ]]; then
  echo "[ERROR] .gitignore 가 생성되지 않았습니다." >&2
  exit 1
fi
if [[ ! -f "${DEST}/VERSION" ]]; then
  echo "[ERROR] VERSION 스탬프가 생성되지 않았습니다." >&2
  exit 1
fi
echo "[SMOKE] 배포본 청결성 OK — docling 소스 없음, genon/+main.py 존재, packages/${WHEEL_NAME} 동봉됨, requirements-dev.txt·.gitignore·VERSION 생성됨"
echo "[INFO] 배포본 조립 위치: ${DEST}  (원본 커밋 ${SOURCE_COMMIT})"

# ── 4) 배포본 클론이면 commit/push ──────────────────────────────────────────
if [[ "${PUSH_TARGET_OK}" != "true" ]]; then
  echo "[INFO] 배포본 클론 아님 — 조립만 완료(dry-run). 배포본 repo 를 code-serving/ 로 clone 후 다시 실행하면 commit/push 가능."
  # mktemp 로 만든 임시 조립본은 검증 끝났으니 정리 (OUT_DIR 지정 시엔 남겨둠)
  [[ -n "${DRYRUN_TMP}" ]] && rm -rf "${DRYRUN_TMP}"
  exit 0
fi

git -C "${DEST}" add -A
if git -C "${DEST}" diff --cached --quiet; then
  # 내용 동일 — 새 커밋은 없지만, 기존 HEAD 가 이 VERSION 에 해당하므로 태깅은 계속 진행한다.
  echo "[INFO] 변경 없음 — commit 생략(기존 HEAD 에 태그만 부여)."
else
  git -C "${DEST}" commit -q -m "sync from doc_parser ${SOURCE_COMMIT} (docling→wheel)"
  echo "[INFO] 서브모듈 commit 완료."
  if [[ "${PUSH}" == "true" ]]; then
    git -C "${DEST}" push origin "HEAD:${SERVING_BRANCH}"
    echo "[INFO] push 완료 → 배포본 repo (${SERVING_BRANCH})"
  else
    echo "[INFO] PUSH=false — 서브모듈에 commit 만 함. push 하려면 PUSH=true 로 재실행."
  fi
fi

# ── 5) 미러 태그: 원본 릴리스 태그를 배포본 HEAD 에 부여(+PUSH 시 push) ────────
if [[ -z "${VERSION}" ]]; then
  echo "[WARN] VERSION 이 비어 미러 태그를 생략합니다(원본이 태그 커밋이 아니거나 태그가 없음)."
else
  HEAD_SHA="$(git -C "${DEST}" rev-parse HEAD)"
  EXIST_SHA="$(git -C "${DEST}" rev-list -n1 "${VERSION}" 2>/dev/null || true)"
  if [[ -n "${EXIST_SHA}" && "${EXIST_SHA}" != "${HEAD_SHA}" && "${FORCE_TAG}" != "true" ]]; then
    echo "[ERROR] 태그 ${VERSION} 가 이미 다른 커밋(${EXIST_SHA:0:9})을 가리킵니다. 배포본 HEAD 는 ${HEAD_SHA:0:9}." >&2
    echo "        릴리스된 태그를 옮기는 것은 사고 위험이 큽니다. 의도적이면 FORCE_TAG=true 로 재실행하세요." >&2
    exit 1
  fi
  if [[ "${EXIST_SHA}" == "${HEAD_SHA}" ]]; then
    echo "[INFO] 태그 ${VERSION} 가 이미 HEAD 를 가리킴 — 태깅 생략(idempotent)."
  else
    TAG_FORCE=(); [[ "${FORCE_TAG}" == "true" ]] && TAG_FORCE=(-f)
    git -C "${DEST}" tag ${TAG_FORCE[@]+"${TAG_FORCE[@]}"} -a "${VERSION}" -m "code-serving for doc_parser ${VERSION} (${SOURCE_COMMIT})"
    echo "[INFO] 미러 태그 부여: ${VERSION} → ${HEAD_SHA:0:9}"
  fi
  if [[ "${PUSH}" == "true" ]]; then
    PUSH_FORCE=(); [[ "${FORCE_TAG}" == "true" ]] && PUSH_FORCE=(-f)
    git -C "${DEST}" push ${PUSH_FORCE[@]+"${PUSH_FORCE[@]}"} origin "refs/tags/${VERSION}"
    echo "[INFO] 미러 태그 push 완료 → 배포본 repo (${VERSION})"
  else
    echo "[INFO] PUSH=false — 미러 태그는 로컬에만 부여. push 하려면 PUSH=true 로 재실행."
  fi
fi

if [[ "${PUSH}" == "true" ]]; then
  echo "[INFO] 배포본 repo 에 커밋+태그(${VERSION:-<없음>}) push 완료. doc_parser 는 code-serving 을 추적하지 않으므로 별도 gitlink 고정 불필요."
fi
