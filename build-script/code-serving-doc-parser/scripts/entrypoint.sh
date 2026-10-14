#!/bin/sh
# Python code-serving entrypoint.
# - commit hash 별 marker 로 init.sh 실행 가드 — commit 바뀌면 재실행, fail 시 marker 안 박힘.
# - START_COMMAND 가 주어졌으면 사용자 시작 커맨드 실행 (cwd=/app/src/service).
# - 비어있으면 자동 감지:
#     1) /app/src/service/main.py 가 있으면 uvicorn main:app 실행
#     2) 그 외 fallback — 기존 디폴트(gunicorn src.main:app)
# PORT 는 admin-api에서 8080으로 주입됨.
# uvicorn 워커 수는 WEB_CONCURRENCY(기본 5)로 정한다. 워커마다 facade 전체가 따로 메모리에
# 올라가므로, 메모리가 부족한 인스턴스 유형에서는 리비전 환경변수로 값을 낮춘다.
set -eu

# commit hash 별 marker — 새 commit 으로 재배포 시 init.sh 자동 재실행.
# COMMIT_HASH 미설정이면 'noref' 로 fallback (개발 편의).
MARKER_DIR="/app"
MARKER="${MARKER_DIR}/.init_done.${COMMIT_HASH:-noref}"

if [ ! -f "$MARKER" ]; then
  # 이전 commit 의 marker 들 제거 (디스크 누적 방지)
  rm -f "${MARKER_DIR}/.init_done"* 2>/dev/null || true
  if sh scripts/init.sh; then
    touch "$MARKER"
  else
    echo "[entrypoint] WARNING: init.sh failed (exit=$?). Marker not set — will retry on next boot." >&2
    # init.sh 자체는 || echo WARNING 으로 감싸여 거의 항상 0 으로 끝나지만
    # set -e 환경에서 명시적 fail 시 marker 미박힘 보장.
  fi
fi

cd /app/src/service

if [ -n "${START_COMMAND:-}" ]; then
  echo "[entrypoint] Running user START_COMMAND: ${START_COMMAND}"
  exec sh -c "${START_COMMAND}"
fi

# 실행 모드 — PREPROC_MODE=http(기본, 루트 main.py 의 REST API) | worker(Temporal 워커, #514).
# 같은 배포본을 배포할 때 골라 쓴다. START_COMMAND 가 있으면 위에서 이미 그것을 실행했다.
# 두 모드 모두 PORT(8080)의 /health 에 매 요청 응답한다(코드서빙 규약). 워커 모드는 부모 프로세스가 응답한다.
PREPROC_MODE="${PREPROC_MODE:-http}"
case "$PREPROC_MODE" in
  http) ;;
  worker)
    echo "[entrypoint] PREPROC_MODE=worker: python -m genon.preprocessor.worker"
    exec python -m genon.preprocessor.worker ;;
  *)
    echo "[entrypoint] ERROR: PREPROC_MODE 는 http 또는 worker 여야 합니다: ${PREPROC_MODE}" >&2
    exit 1 ;;
esac

# HTTP 모드의 연산 스레드 수 — OMP_NUM_THREADS 가 없으면 "파드 CPU 한도 ÷ 워커 수" 의 내림(최소 1).
# 프로세스는 파드 한도가 아니라 노드 코어 수를 보고 torch·docling 스레드를 띄우므로, 정하지 않으면
# CPU 작업이 겹칠 때 수 배 느려진다(#511 측정). 워커 모드는 워커가 같은 식으로 정한다(worker/settings.py).
if [ -z "${OMP_NUM_THREADS:-}" ]; then
  cpus=""
  if [ -r /sys/fs/cgroup/cpu.max ]; then
    read -r quota period < /sys/fs/cgroup/cpu.max || true
    if [ "${quota:-max}" != "max" ] && [ "${period:-0}" -gt 0 ]; then
      cpus=$(( quota / period ))
    fi
  fi
  [ -n "$cpus" ] || cpus=$(nproc 2>/dev/null || echo 1)
  OMP_NUM_THREADS=$(( cpus / ${WEB_CONCURRENCY:-5} ))
  [ "$OMP_NUM_THREADS" -ge 1 ] || OMP_NUM_THREADS=1
  export OMP_NUM_THREADS
fi

# 디폴트 — main.py 자동 감지 (사용자가 START_COMMAND 안 채워도 동작)
if [ -f "main.py" ]; then
  echo "[entrypoint] Default: uvicorn main:app on port ${PORT:-8080} (workers=${WEB_CONCURRENCY:-5}, OMP_NUM_THREADS=${OMP_NUM_THREADS})"
  exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}" --workers "${WEB_CONCURRENCY:-5}"
fi

if [ -f "src/main.py" ]; then
  echo "[entrypoint] Default: uvicorn src.main:app on port ${PORT:-8080}"
  exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8080}"
fi

# 최후 fallback — 기존 호환 (베이스 이미지 자체 main 실행. 사용자 코드 없을 때)
echo "[entrypoint] No main.py found, falling back to base gunicorn"
cd /app
exec gunicorn -c gunicorn/gunicorn_conf.py src.main:app
