#!/usr/bin/env bash
# 로컬에서 코드서빙과 같은 전처리기 Temporal 워커(python -m genon.preprocessor.worker)를 띄운다(#577).
# 모델은 로컬(VPN) 프리셋을 쓴다.
#
#   build-script/run-local.sh            # 표준 genon/preprocessor/activities/
#   build-script/run-local.sh monimo     # 사이트 완성본 genon/sites/monimo/(resource + 옆 activities/)
#
# 워커 env 는 셸에서 준다 — TEMPORAL_HOST(필수, 로컬 개발 서버면 127.0.0.1)·WORKER_TASK_QUEUE·NFS_ROOT·
# ARTIFACT_ROOT. 없으면 워커가 이름을 모두 적어 멈춘다(genon/preprocessor/worker/settings.py).
# 설정 폴더는 GENOS_RESOURCE_DIR, 모델은 GENOS_MODEL_PRESETS_FILE(기본 genon/sites/dev/model_presets.yaml)로
# 넘긴다. 이미 설정된 GENOS_MODEL_PRESETS_FILE 은 존중한다. /health·/version 은 PORT(기본 7084).
# 큐 없이 처리기만 확인하려면 단건 CLI(python -m genon.preprocessor.activities run --activity <slug> …)를 쓴다.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel)"
PREPROCESSOR_DIR="${REPO_ROOT}/genon/preprocessor"

SITE="${1:-}"
if [ -n "${SITE}" ]; then
  GENOS_RESOURCE_DIR="${REPO_ROOT}/genon/sites/${SITE}/resource"
  [ -d "${GENOS_RESOURCE_DIR}" ] || { echo "[ERROR] 사이트 설정 폴더가 없습니다: ${GENOS_RESOURCE_DIR}" >&2; exit 1; }
  export GENOS_RESOURCE_DIR
fi
export GENOS_MODEL_PRESETS_FILE="${GENOS_MODEL_PRESETS_FILE:-${REPO_ROOT}/genon/sites/dev/model_presets.yaml}"
# 서비스 설정은 k8s 파드 이름(HOSTNAME)에서 POD_ID 를 뽑는다. 로컬 셸에는 없으므로 임의 값을 준다.
export HOSTNAME="${HOSTNAME:-local-0}"
# WeasyPrint 네이티브 의존(macOS). parse_chunk_verify.py 와 같은 기본값이다.
export DYLD_FALLBACK_LIBRARY_PATH="${DYLD_FALLBACK_LIBRARY_PATH:-/opt/homebrew/lib:/usr/local/lib:/usr/lib}"

# 워커는 genon/preprocessor/.venv(temporalio 포함)로 돈다. 리포 루트가 앞이어야 리포 docling 이 pip docling 보다
# 우선한다(CLAUDE.md "로컬 실행").
export PYTHONPATH="${REPO_ROOT}"
export PORT="${PORT:-7084}"

echo "[INFO] 설정 폴더: ${GENOS_RESOURCE_DIR:-${PREPROCESSOR_DIR}/resource}"
echo "[INFO] 모델 프리셋: ${GENOS_MODEL_PRESETS_FILE}"
cd "${REPO_ROOT}"
exec "${PREPROCESSOR_DIR}/.venv/bin/python" -m genon.preprocessor.worker
