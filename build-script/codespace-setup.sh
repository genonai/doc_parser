#!/usr/bin/env bash
# 서빙본 코드스페이스 설치 — 저장소 .venv 를 만들고 개발 env(.env.codespace)·worker.sh 를 쓴다(#470).
# (서빙본 루트에서는 setup.sh 라는 이름이다 — sync-serving-repo.sh 가 복사한다. 서빙본 레이아웃 전용: packages/ 의 docling wheel.)
#
#   ./setup.sh --temporal-host <h> --temporal-namespace <ns> [--wheelhouse <폴더>] [--data-root <폴더>] [--user <이름>] [--force-env]
#   (Temporal 접속처는 현장 값 — 이 공개 저장소에 기본값을 두지 않는다. pipeline 저장소 .env 의 TEMPORAL_* 과 같은 값)
#
# - .venv: 코드스페이스 기본 /.venv·코드서빙 /app/.venv 와 분리 — Studio 는 <저장소>/.venv/bin/python 을 쓴다.
# - 설치: --wheelhouse(또는 ./wheelhouse-* 하나)가 있으면 그 폴더의 requirements-lock.txt 를 그 폴더에서만(--no-index) 설치.
#   없으면 인터넷 — uv 로 genon/preprocessor/uv.lock 에서 같은 목록을 뽑아 PyPI(+torch CPU index)에서 설치.
#   docling 은 packages/ 의 저장소 wheel 을 --no-deps 로. (매뉴얼 3.1 의 requirements-dev.txt 는 facade 실행용이라 워커 의존성이 없다.)
# - .env.codespace: TEMPORAL_*, 개발 큐 WORKER_TASK_QUEUE=dev-<이름>-preproc(pipeline 의 DOC_PARSER_TASK_QUEUE 와 같은 값),
#   NFS_ROOT=<data-root>/nfs · ARTIFACT_ROOT=<data-root>/interim(pipeline INTERIM_ROOT 와 같은 자리), PORT=8081(pipeline 이 8080),
#   PYTHONPATH. GENOS_RESOURCE_DIR 은 비워 둔다(사이트 완성본을 쓰려면 그 resource/ 경로 — 옆에 activities/ 가 있어야 한다, #522).
# - 생성 파일(.env.codespace·worker.sh·wheelhouse-*)은 .git/info/exclude 로 추적에서 뺀다 — 커밋하지 않는다.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
WHEELHOUSE="" ; DATA_ROOT="/workspace/.studio-data" ; USER_NAME="${STUDIO_USER:-${USER:-$(id -un)}}" ; FORCE_ENV=0
T_HOST="${TEMPORAL_HOST:-}" ; T_NS="${TEMPORAL_NAMESPACE:-}" ; T_PORT="${TEMPORAL_PORT:-7233}"     # 접속처는 현장 값 — 기본값을 두지 않는다(공개 저장소)
while [ $# -gt 0 ]; do
  case "$1" in
    --wheelhouse) WHEELHOUSE="$2"; shift 2 ;;
    --data-root)  DATA_ROOT="$2"; shift 2 ;;
    --user)       USER_NAME="$2"; shift 2 ;;
    --temporal-host) T_HOST="$2"; shift 2 ;;
    --temporal-namespace) T_NS="$2"; shift 2 ;;
    --force-env)  FORCE_ENV=1; shift ;;
    *) echo "[ERROR] 모르는 인자: $1" >&2; exit 2 ;;
  esac
done
if [ -z "${T_HOST}" ] || [ -z "${T_NS}" ]; then
  if [ -f "${ROOT}/.env.codespace" ] && [ "${FORCE_ENV}" -eq 0 ]; then :; else
    echo "[ERROR] Temporal 접속처가 필요합니다 — --temporal-host <host> --temporal-namespace <ns> (또는 env TEMPORAL_HOST·TEMPORAL_NAMESPACE). 값은 pipeline 저장소 .env 와 같다" >&2; exit 2
  fi
fi
WHEEL="$(ls "${ROOT}"/packages/genon_docling-*.whl 2>/dev/null | head -1)"
[ -n "${WHEEL}" ] || { echo "[ERROR] packages/genon_docling-*.whl 이 없습니다 — 서빙본(doc_parser_code_serving) 체크아웃에서 실행하십시오" >&2; exit 1; }
if [ -z "${WHEELHOUSE}" ]; then
  found=("${ROOT}"/wheelhouse-*/)
  [ -d "${found[0]}" ] && [ ${#found[@]} -eq 1 ] && WHEELHOUSE="${found[0]%/}"
fi

echo "[1/4] .venv — ${PYTHON} ($("${PYTHON}" -V 2>&1))"
[ -x "${ROOT}/.venv/bin/python" ] || "${PYTHON}" -m venv "${ROOT}/.venv"
VPY="${ROOT}/.venv/bin/python"
PIP=("${VPY}" -m pip --disable-pip-version-check)
if [ -n "${WHEELHOUSE}" ]; then
  [ -f "${WHEELHOUSE}/requirements-lock.txt" ] || { echo "[ERROR] ${WHEELHOUSE}/requirements-lock.txt 가 없습니다 — make-wheelhouse.sh 로 만든 폴더여야 합니다" >&2; exit 1; }
  echo "[2/4] 설치 — wheelhouse ${WHEELHOUSE} ($(ls "${WHEELHOUSE}"/*.whl | wc -l | tr -d ' ') wheels, --no-index)"
  "${PIP[@]}" install --no-index --find-links "${WHEELHOUSE}" -r "${WHEELHOUSE}/requirements-lock.txt"
else
  echo "[2/4] 설치 — PyPI(인터넷) + torch CPU index. SCP 코드스페이스는 --wheelhouse 가 필요하다"
  "${PIP[@]}" install -q uv
  "${VPY}" -m uv export --frozen --no-hashes --no-dev --no-emit-project --no-emit-package docling --format requirements-txt \
    --project "${ROOT}/genon/preprocessor" -o "${ROOT}/.venv/requirements-lock.raw" >/dev/null
  CPU_TAG="+cpu"; [ "$(uname -s)" = "Linux" ] || CPU_TAG=""        # macOS 는 CPU 빌드에 +cpu 접미가 없다
  grep -Ev '^(nvidia-|cuda-|triton)' "${ROOT}/.venv/requirements-lock.raw" \
    | sed -E "s/^(torch|torchvision)==([0-9.]+)/\1==\2${CPU_TAG}/" > "${ROOT}/.venv/requirements-lock.txt"
  "${PIP[@]}" install -r "${ROOT}/.venv/requirements-lock.txt" --extra-index-url "${TORCH_INDEX:-https://download.pytorch.org/whl/cpu}"
fi
"${PIP[@]}" install --no-deps --no-index "${WHEEL}"

ENV_FILE="${ROOT}/.env.codespace"
if [ -f "${ENV_FILE}" ] && [ "${FORCE_ENV}" -eq 0 ]; then
  echo "[3/4] ${ENV_FILE} 있음 — 그대로 둔다(--force-env 로 재생성)"
else
  echo "[3/4] ${ENV_FILE} 생성"
  {   # bash 가 source 하므로 값은 %q 로(경로에 공백이 있어도 안전)
    echo "# 전처리기 워커 개발 env — setup.sh 가 만들었다(커밋하지 않는다). 쓰는 법: ./worker.sh  또는  set -a; . .env.codespace; set +a"
    printf '%s=%q\n' TEMPORAL_HOST "${T_HOST}" TEMPORAL_PORT "${T_PORT}" TEMPORAL_NAMESPACE "${T_NS}"
    echo "# 개발 큐(강제) — pipeline 쪽 DOC_PARSER_TASK_QUEUE 와 같은 값. 빠뜨리면 운영 큐로 뜬다"
    printf '%s=%q\n' WORKER_TASK_QUEUE "dev-${USER_NAME}-preproc" WORKER_PROCESSES 1 WORKER_THREADS 1
    echo "# NFS 마운트가 없으므로 로컬 폴더 — pipeline 의 NFS_ROOT·INTERIM_ROOT 와 같은 자리"
    printf '%s=%q\n' NFS_ROOT "${DATA_ROOT}/nfs" ARTIFACT_ROOT "${DATA_ROOT}/interim"
    echo "# /health — pipeline 워커가 8080 을 쓴다"
    printf '%s=%q\n' PORT 8081 PYTHONPATH "${ROOT}:${ROOT}/genon/preprocessor"
    echo "# 사이트 완성본을 쓰려면 그 resource/ 경로(옆에 activities/<slug>/ 가 있어야 한다 — 없으면 호출마다 오류). 비우면 저장소 표준"
    echo "GENOS_RESOURCE_DIR="
  } > "${ENV_FILE}"
fi
mkdir -p "${DATA_ROOT}/nfs" "${DATA_ROOT}/interim"
cat > "${ROOT}/worker.sh" <<'RUN'
#!/usr/bin/env bash
# 전처리기 워커를 개발 큐로 띄운다 — .env.codespace 를 읽고 python -m genon.preprocessor.worker(/health :PORT).
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
set -a; . "${ROOT}/.env.codespace"; set +a
cd "${ROOT}" && exec "${ROOT}/.venv/bin/python" -m genon.preprocessor.worker
RUN
chmod +x "${ROOT}/worker.sh"
if [ -d "${ROOT}/.git" ]; then
  for pat in ".env.codespace" "worker.sh" "wheelhouse-*/" ".venv/"; do
    grep -qxF "${pat}" "${ROOT}/.git/info/exclude" 2>/dev/null || echo "${pat}" >> "${ROOT}/.git/info/exclude"
  done
fi

echo "[4/4] 확인"
(cd "${ROOT}" && set -a && . "${ENV_FILE}" && set +a \
  && "${VPY}" -m genon.preprocessor.activities --help >/dev/null \
  && "${VPY}" -c "from genon.preprocessor.worker import settings; s = settings.load(); print('      워커 설정 OK —', s.temporal_address, s.temporal_namespace, s.task_queue)")
cat <<MSG

[OK] .venv 준비 — ${VPY}
     env: ${ENV_FILE}   큐: dev-${USER_NAME}-preproc   루트: ${DATA_ROOT}
     워커 기동: ./worker.sh      단건: .venv/bin/python -m genon.preprocessor.activities run --activity parse <문서> -o parse.json
MSG
