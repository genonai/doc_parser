#!/usr/bin/env bash
set -euo pipefail

# build-docling-wheel.sh — vendored docling(fork) 를 wheel 로 빌드+검증한다.
#
# 목적: 코드서빙 복사본(sync-serving-repo.sh)에 docling 소스 대신 이 wheel 을 packages/ 에 동봉한다.
#       런타임에 복사본의 requirements.txt 가 이 wheel 을 경로로 참조해 설치한다. (index 불필요)
#
# 핵심:
#   - 배포명(dist) = genon-docling, import 네임스페이스 = docling (306곳 코드 수정 불필요).
#     → 공개 PyPI 의 `docling`(IBM) 과 이름이 달라 혼동 없음.
#   - 루트 pyproject.toml 은 건드리지 않는다(전처리기 빌드/semantic-release 보호).
#     임시 빌드 디렉토리에서 name/version 만 오버라이드해 빌드한다.
#   - 버전 = <base>+genon.<N> (PEP440 local segment).
#
# 사용:
#   bash build-script/build-docling-wheel.sh                 # 로컬 작업 트리의 docling/ 을 dist-docling/ 에 wheel 로 생성
#   OUT_DIR=/tmp/w GENON_BUILD=1 bash build-script/build-docling-wheel.sh
#
# 항상 로컬 작업 트리(현재 docling/ 폴더)를 빌드한다 — 커밋 여부 무관.
# 출력: 생성된 wheel 경로를 마지막 줄에 "WHEEL=<path>" 로 출력.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 설정 (env 로 오버라이드) ────────────────────────────────────────────────
DIST_NAME="${DIST_NAME:-genon-docling}"     # 배포명 (import 은 docling 유지)
GENON_BUILD="${GENON_BUILD:-0}"             # local segment N: <base>+genon.<N>
OUT_DIR="${OUT_DIR:-${ROOT_DIR}/dist-docling}"

# base version 은 로컬 루트 pyproject 에서 읽는다 (예: 2.41.0)
BASE_VERSION="$(sed -n 's/^version = "\([0-9][^"]*\)".*/\1/p' "${ROOT_DIR}/pyproject.toml" | head -1)"
if [[ -z "${BASE_VERSION}" ]]; then
  echo "[ERROR] 루트 pyproject.toml 에서 base version 을 읽지 못했습니다." >&2
  exit 1
fi
FULL_VERSION="${BASE_VERSION}+genon.${GENON_BUILD}"

echo "[INFO] ROOT_DIR   = ${ROOT_DIR}  (local working tree)"
echo "[INFO] DIST_NAME  = ${DIST_NAME}"
echo "[INFO] VERSION    = ${FULL_VERSION}  (import namespace: docling)"
echo "[INFO] OUT_DIR    = ${OUT_DIR}"

# ── 임시 빌드 트리 구성 (루트 소스는 불변) ──────────────────────────────────
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "${BUILD_DIR}"' EXIT

# 로컬 작업 트리의 docling/ + README.md 를 임시 트리로 복사한다(커밋 여부 무관).
#   git ls-files (cached+others, exclude-standard) 로 추적/미추적 파일을 모으되
#   .gitignore 대상(__pycache__/, .DS_Store 등)은 자동 제외해 wheel 오염을 막는다.
( cd "${ROOT_DIR}" \
    && git ls-files --cached --others --exclude-standard docling README.md \
    | tar -cf - -T - ) | tar -xf - -C "${BUILD_DIR}"

# 오버라이드된 pyproject 생성: 로컬 루트 pyproject 에서 name/version 만 바꾸고 build-system 명시
sed -e "s/^name = \"docling\"/name = \"${DIST_NAME}\"/" \
    -e "s/^version = .*/version = \"${FULL_VERSION}\"/" \
    "${ROOT_DIR}/pyproject.toml" \
  > "${BUILD_DIR}/pyproject.toml"

# build-system 이 없으면 (루트엔 없음) setuptools 백엔드 명시적으로 추가
if ! grep -q '^\[build-system\]' "${BUILD_DIR}/pyproject.toml"; then
  {
    echo ""
    echo "[build-system]"
    echo 'requires = ["setuptools>=68", "wheel"]'
    echo 'build-backend = "setuptools.build_meta"'
  } >> "${BUILD_DIR}/pyproject.toml"
fi

# ── 빌드 ────────────────────────────────────────────────────────────────────
rm -rf "${OUT_DIR}"
mkdir -p "${OUT_DIR}"
echo "[INFO] uv build --wheel ..."
( cd "${BUILD_DIR}" && uv build --wheel --out-dir "${OUT_DIR}" )

WHEEL="$(ls -1 "${OUT_DIR}"/*.whl | head -1)"
if [[ -z "${WHEEL}" ]]; then
  echo "[ERROR] wheel 이 생성되지 않았습니다." >&2
  exit 1
fi
echo "[INFO] built: ${WHEEL}"

# ── wheel 내용 검증 (fork 전용 파일이 fork 의 존재 이유) ─────────────────────
python3 - "${WHEEL}" <<'PY'
import sys, zipfile
whl = sys.argv[1]
names = zipfile.ZipFile(whl).namelist()
required = [
    "docling/prompts/prompts.json",
    "docling/prompts/prompt_manager.py",
    "docling/utils/document_enrichment.py",
    "docling/utils/llm_cache.py",
    "docling/models/ocr_pb2.py",
    "docling/models/ocr_pb2_grpc.py",
]
missing = [r for r in required if not any(n == r or n.endswith("/" + r) for n in names)]
if missing:
    print("[SMOKE] wheel 에 fork 전용 파일 누락:", missing)
    sys.exit(1)
print("[SMOKE] wheel 내용 검증 OK (genon 패치 파일 포함)")
PY

# sync-serving-repo.sh 가 파싱하기 위한 최종 출력
echo "WHEEL=${WHEEL}"
