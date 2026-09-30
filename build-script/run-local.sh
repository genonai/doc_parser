#!/usr/bin/env bash
# 로컬에서 코드서빙 서버(루트 main.py, 포트 7084)를 띄운다. 모델은 로컬(VPN) 프리셋을 쓴다.
#
#   build-script/run-local.sh            # 표준 genon/preprocessor/resource/
#   build-script/run-local.sh monimo     # 사이트 완성본 genon/sites/monimo/resource/
#
# 설정 폴더는 GENOS_RESOURCE_DIR, 모델은 GENOS_MODEL_PRESETS_FILE(기본 genon/sites/dev/model_presets.yaml)로
# 넘긴다. 이미 설정된 GENOS_MODEL_PRESETS_FILE 은 존중한다. 기동 후 호출은
# genon/preprocessor/examples/code_serving/serving_gateway_test.sh 로 한다.

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

# 루트 .venv 와 preprocessor .venv 어느 쪽도 단독으로는 facade 를 실행할 수 없어 둘을 섞는다.
# 리포 루트가 앞이어야 리포 docling 이 pip docling 보다 우선한다(CLAUDE.md "로컬 실행").
SITE_PACKAGES="$(ls -d "${PREPROCESSOR_DIR}"/.venv/lib/python3*/site-packages | head -1)"
export PYTHONPATH="${REPO_ROOT}:${PREPROCESSOR_DIR}:${PREPROCESSOR_DIR}/src:${SITE_PACKAGES}"

echo "[INFO] 설정 폴더: ${GENOS_RESOURCE_DIR:-${PREPROCESSOR_DIR}/resource}"
echo "[INFO] 모델 프리셋: ${GENOS_MODEL_PRESETS_FILE}"
cd "${REPO_ROOT}"
# `python main.py` 는 uvicorn reload 로 뜨는데, 그 자식 프로세스가 `main` 을 이름으로 다시 import 하면서
# PYTHONPATH 의 genon/preprocessor/src/main.py(기본 전처리기 서비스)를 잡는다. --app-dir 로 루트 main.py 를
# 확실히 지정하고 reload 없이 띄운다.
exec "${REPO_ROOT}/.venv/bin/python" -m uvicorn main:app --app-dir "${REPO_ROOT}" --host 0.0.0.0 --port 7084
