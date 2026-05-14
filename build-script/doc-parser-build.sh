#!/usr/bin/env bash
set -euo pipefail

# 이 스크립트는 반드시 repo 루트(/workspace/doc_parser)에서 실행된다고 가정
# 다른 데서 실행해도 되게 하려면 아래에서 루트 계산
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 기본값
CONFIG_FILE="${ROOT_DIR}/build-script/doc-parser-build.config"

if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck source=/dev/null
  source "${CONFIG_FILE}"
else
  echo "[WARN] ${CONFIG_FILE} 이(가) 없어서 기본값으로 빌드합니다."
fi

# 로컬 전용 토큰 파일이 있으면 추가로 읽음 (HF_TOKEN 등 민감 정보용, Git 미추적)
LOCAL_CONFIG_FILE="${ROOT_DIR}/build-script/hf_private_token.env"
if [[ -f "${LOCAL_CONFIG_FILE}" ]]; then
  # shellcheck source=/dev/null
  source "${LOCAL_CONFIG_FILE}"
fi

# build.config 에서 못 가져오면 기본값 세팅
DOCKER_REGISTRY="${DOCKER_REGISTRY:-localhost:5000}"
IMAGE_NAME="${IMAGE_NAME:-doc-parser-preprocessor}"
IMAGE_VERSION="${IMAGE_VERSION:-latest}"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-genon/preprocessor/docker/Dockerfile}"
APP_UID="${APP_UID:-1000}"
APP_GID="${APP_GID:-1000}"
APP_UNAME="${APP_UNAME:-genos}"
APP_GNAME="${APP_GNAME:-genos}"
APP_NLTK_PACKAGES="${APP_NLTK_PACKAGES:-all}"
export HF_TOKEN="${HF_TOKEN:-}"

# 최종 이미지 태그
IMAGE_TAG="${DOCKER_REGISTRY}/mnc/${IMAGE_NAME}:${IMAGE_VERSION}"

echo "[INFO] ROOT_DIR        = ${ROOT_DIR}"
echo "[INFO] DOCKERFILE_PATH = ${DOCKERFILE_PATH}"
echo "[INFO] IMAGE_TAG       = ${IMAGE_TAG}"
echo "[INFO] UID:GID         = ${APP_UID}:${APP_GID} (${APP_UNAME}:${APP_GNAME})"
echo "[INFO] NLTK_PACKAGES  = ${APP_NLTK_PACKAGES}"

# HuggingFace 토큰 존재 여부 확인 (Private SDK 다운로드 시 필수)
if [[ -z "${HF_TOKEN}" ]]; then
  echo "[ERROR] HF_TOKEN이 설정되지 않았습니다. Private SDK 다운로드가 필요한 빌드는 진행할 수 없습니다."
  echo "[ERROR] build-script/hf_private_token.env 또는 환경변수에 HF_TOKEN을 설정하세요."
  exit 1
else
  echo "[INFO] HF_TOKEN이 감지되었습니다. Secret 마운트를 사용하여 빌드합니다."
fi

# SDK 아티팩트 캐시 무력화 옵션 처리
# SDK_NO_CACHE=true 면 sdk_artifacts / pdf_sdk_artifacts 스테이지만 캐시 무시 → SDK 항상 최신.
# (전역 캐시는 건드리지 않으므로 다른 스테이지/다른 빌드 캐시에는 영향 없음)
NO_CACHE_ARGS=()
if [[ "${SDK_NO_CACHE:-true}" == "true" ]]; then
  echo "[INFO] SDK_NO_CACHE=true → sdk_artifacts, pdf_sdk_artifacts 스테이지 캐시 무시하고 재빌드"
  NO_CACHE_ARGS=(--no-cache-filter sdk_artifacts,pdf_sdk_artifacts)
else
  echo "[INFO] SDK_NO_CACHE=false → SDK 스테이지도 Docker 레이어 캐시 사용"
fi

# BuildKit plain 로그로 보기 + 루트(.)를 컨텍스트로 빌드
# NO_CACHE_ARGS 가 비어있을 때도 set -u 에서 안전하도록 ${arr[@]+...} 패턴 사용
DOCKER_BUILDKIT=1 docker build \
  --platform linux/amd64 \
  ${NO_CACHE_ARGS[@]+"${NO_CACHE_ARGS[@]}"} \
  -f "${ROOT_DIR}/${DOCKERFILE_PATH}" \
  -t "${IMAGE_TAG}" \
  --secret id=HF_TOKEN,env=HF_TOKEN \
  --build-arg UID="${APP_UID}" \
  --build-arg GID="${APP_GID}" \
  --build-arg UNAME="${APP_UNAME}" \
  --build-arg GNAME="${APP_GNAME}" \
  --build-arg NLTK_PACKAGES="${APP_NLTK_PACKAGES}" \
  "${ROOT_DIR}"

echo "[INFO] Build done: ${IMAGE_TAG}"

# 푸시 여부 플래그
if [[ "${PUSH_IMAGE:-false}" == "true" ]]; then
  echo "[INFO] Pushing ${IMAGE_TAG} ..."
  docker push "${IMAGE_TAG}"
  echo "[INFO] Push done"
fi
