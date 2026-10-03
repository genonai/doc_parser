#!/bin/sh
# Python code-serving 초기화 스크립트.
# 컨테이너 최초 부팅 시 한 번만 실행 (entrypoint.sh의 marker로 가드).
#
# 동작:
#   1. Gitea repo clone (REPOSITORY_URL + COMMIT_HASH).
#   2. BUILD_COMMAND 가 주어졌으면 그것을 실행.
#      비어있으면 packages/*.whl 을 --no-deps --no-index 로 설치하고 requirements.txt 의 나머지 줄을 설치한다.
set -eu

DESTINATION="/app/src/service"

# Expect: REPOSITORY_URL like
#   http://llmops:<TOKEN>@llmops-gitea-service:3000/llmops/22
# And COMMIT_HASH is set.

if [ "${GIT_HOST_TYPE:-INTERNAL}" = "EXTERNAL" ]; then
  # EXTERNAL — REPOSITORY_URL 에 credential 임베드 → 표준 git clone (extraHeader 불필요)
  if [ -d "$DESTINATION/.git" ]; then
    echo "[init.sh] Repo already exists at $DESTINATION. Skipping clone."
  else
    mkdir -p "$(dirname "$DESTINATION")"
    git clone "${REPOSITORY_URL}" "$DESTINATION"
  fi
  git -C "$DESTINATION" fetch --all --tags --prune || true
  git -C "$DESTINATION" checkout "${COMMIT_HASH}"
else
  # INTERNAL — 사내 gitea Bearer extraHeader 흐름 (기존)
  repo_url="${REPOSITORY_URL}"

  # Extract token (between "llmops:" and "@")
  token="$(printf '%s' "$repo_url" | sed -n 's#^http://llmops:\([^@]*\)@.*#\1#p')"
  if [ -z "${token:-}" ]; then
    echo "ERROR: Could not extract token from REPOSITORY_URL" >&2
    echo "       Expected: http://llmops:<TOKEN>@host:port/owner/repo" >&2
    exit 1
  fi

  # Build HTTPS URL without credentials: https://host:port/owner/repo(.git)
  host_path="$(printf '%s' "$repo_url" | sed -n 's#^http://llmops:[^@]*@\([^/]*\)/\(.*\)$#\1/\2#p')"
  if [ -z "${host_path:-}" ]; then
    echo "ERROR: Could not parse host/path from REPOSITORY_URL" >&2
    exit 1
  fi

  clean_url="http://${host_path}"
  case "$clean_url" in
    *.git) : ;;
    *) clean_url="${clean_url}.git" ;;
  esac

  extra_header="Authorization: Bearer ${token}"

  if [ -d "$DESTINATION/.git" ]; then
    echo "Repo already exists at $DESTINATION. Skipping clone."
  else
    mkdir -p "$(dirname "$DESTINATION")"
    git -c "http.extraHeader=${extra_header}" clone "$clean_url" "$DESTINATION"
  fi

  git -C "$DESTINATION" -c "http.extraHeader=${extra_header}" fetch --all --tags --prune || true
  git -C "$DESTINATION" checkout "${COMMIT_HASH}"
fi

cd "$DESTINATION"

# 빌드 단계 — 사용자 BUILD_COMMAND 우선.
PIP_OPTS="--upgrade-strategy only-if-needed --no-cache-dir"

if [ -n "${BUILD_COMMAND:-}" ]; then
  echo "[init.sh] Running user BUILD_COMMAND: ${BUILD_COMMAND}"
  sh -c "${BUILD_COMMAND}" || echo "[init.sh] WARNING: BUILD_COMMAND failed"
  echo "[init.sh] BUILD_COMMAND completed."
else
  # 디폴트: packages/*.whl 을 먼저 설치하고, requirements.txt 의 나머지 줄을 설치한다.
  # pip 은 PATH 상 /app/.venv/bin/pip (base deps 와 동일 venv) 로 해석된다.
  REQ_FILE="$DESTINATION/requirements.txt"
  PKG_DIR="$DESTINATION/packages"

  # 배포본 wheel(docling 포크 등)은 --no-deps --no-index 로 설치한다.
  #   의존은 base 이미지 venv 에 이미 있다(이미지 빌드 시 docling wheel 의존 충족 검사로 보장).
  #   --no-deps 이므로 의존 해석을 하지 않아 폐쇄망에서도 인덱스에 접근하지 않고, 사내 미러에서
  #   resolver 가 backtracking 에 빠지는 일도 없다.
  #   --force-reinstall: 같은 버전 문자열로 다시 빌드한 wheel 의 내용 변경도 반영한다.
  WHEELS=""
  if [ -d "$PKG_DIR" ]; then
    WHEELS="$(find "$PKG_DIR" -maxdepth 1 -name '*.whl' | sort)"
  fi
  if [ -n "$WHEELS" ]; then
    echo "[init.sh] installing bundled wheels (--no-deps --no-index):"
    echo "$WHEELS"
    # shellcheck disable=SC2086
    pip install --no-deps --no-index --force-reinstall --no-cache-dir $WHEELS 2>&1 \
      || echo "[init.sh] WARNING: bundled wheel install failed"
  fi

  if [ -f "$REQ_FILE" ]; then
    # 위에서 설치한 packages/*.whl 경로 줄과 주석·빈 줄을 뺀 나머지만 설치 대상이다.
    REST_REQ="$(mktemp)"
    grep -vE '^[[:space:]]*(#|$)' "$REQ_FILE" \
      | grep -vE '(^|/)packages/[^[:space:]]*\.whl[[:space:]]*$' > "$REST_REQ" || true
    if [ -s "$REST_REQ" ]; then
      FIND_LINKS=""
      if [ -d "$PKG_DIR" ]; then
        FIND_LINKS="--find-links $PKG_DIR"
      fi
      echo "[init.sh] installing remaining requirements (offline first):"
      cat "$REST_REQ"
      # 먼저 인덱스 없이(이미 설치된 패키지와 packages/ 만으로) 시도하고, 실패할 때만 인덱스로 폴백한다.
      # shellcheck disable=SC2086
      if ! pip install $PIP_OPTS --no-index -r "$REST_REQ" $FIND_LINKS 2>&1; then
        echo "[init.sh] offline install failed, retrying with package index..."
        # shellcheck disable=SC2086
        pip install $PIP_OPTS -r "$REST_REQ" $FIND_LINKS 2>&1 || echo "[init.sh] WARNING: pip install failed"
      fi
    fi
    rm -f "$REST_REQ"
  fi
  echo "[init.sh] Package installation completed."
fi
