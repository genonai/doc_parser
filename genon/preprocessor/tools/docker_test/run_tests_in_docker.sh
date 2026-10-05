#!/usr/bin/env bash
# 전처리기 테스트를 리눅스 컨테이너(docker/Dockerfile.test)에서 실행한다.
#
#   tools/docker_test/run_tests_in_docker.sh                    # 이미지 빌드 후 unit+smoke+regression 전체
#   tools/docker_test/run_tests_in_docker.sh --no-build -- tests/unit -q --color=no
#   PLATFORM=linux/amd64 tools/docker_test/run_tests_in_docker.sh   # 플랫폼 지정
#
# 플랫폼 기본값은 Docker 호스트의 네이티브 아키텍처다(에뮬레이션을 피한다).
#   - linux/amd64 : CI·운영과 같다. HWP SDK 를 포함한 전체 경로를 검증한다.
#   - linux/arm64 : Apple Silicon 에서 네이티브로 실행된다. HWP SDK(x86-64 전용)는 쓰지 않는다.
#   Apple Silicon 에서 linux/amd64 를 지정하면 Rosetta 에뮬레이션에서 LibreOffice 의 JVM(H2Orestart)이
#   정상 동작하지 않아 오피스 문서 → PDF 변환 테스트가 실패한다.
#
# 저장소는 /repo 로 마운트되고, 모델·NLTK·HWP SDK 는 named volume(doc-parser-test-cache)에 한 번만 받는다.
# HWP SDK 토큰은 build-script/hf_private_token.env 의 HWP_SDK_TOKEN 을 읽어 환경변수로만 넘긴다.
# 캐시를 비우려면: docker volume rm doc-parser-test-cache
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
PLATFORM="${PLATFORM:-linux/$(docker version --format '{{.Server.Arch}}')}"
# 아키텍처별로 태그를 나눠 서로 덮어쓰지 않게 한다.
IMAGE="${IMAGE:-doc-parser-test:${PLATFORM#linux/}}"
CACHE_VOLUME="${CACHE_VOLUME:-doc-parser-test-cache}"

BUILD=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0; shift ;;
    --) shift; break ;;
    *) echo "알 수 없는 옵션: $1 (pytest 인자는 -- 뒤에 둔다)" >&2; exit 2 ;;
  esac
done

if [ "$BUILD" -eq 1 ]; then
  docker build --platform "$PLATFORM" -f "$REPO/genon/preprocessor/docker/Dockerfile.test" -t "$IMAGE" "$REPO"
  docker image ls "$IMAGE" --format '[INFO] 이미지 크기: {{.Size}}'
fi

TOKEN_FILE="$REPO/build-script/hf_private_token.env"
if [ -z "${HWP_SDK_TOKEN:-}" ] && [ -f "$TOKEN_FILE" ]; then
  HWP_SDK_TOKEN="$(sed -n 's/^HWP_SDK_TOKEN=//p' "$TOKEN_FILE")"
fi
export HWP_SDK_TOKEN="${HWP_SDK_TOKEN:-}"

# macOS 기본 bash 3.2 는 set -u 에서 빈 배열 전개를 오류로 처리하므로 문자열 플래그를 쓴다.
TTY_FLAG=""
[ -t 1 ] && TTY_FLAG="-t"
echo "[INFO] 볼륨: $REPO -> /repo, $CACHE_VOLUME -> /cache" >&2
exec docker run --rm $TTY_FLAG --platform "$PLATFORM" \
  -v "$REPO:/repo" \
  -v "$CACHE_VOLUME:/cache" \
  -e HWP_SDK_TOKEN \
  "$IMAGE" "$@"
