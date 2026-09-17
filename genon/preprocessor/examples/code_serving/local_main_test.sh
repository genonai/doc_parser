#!/usr/bin/env bash
# 루트 main.py 를 그대로 띄워 /parser -> /chunker 를 확인한다 (#363 08-5).
# 배포 형태가 루트 main.py 이므로(07-A 결정) 그 형태로 확인한다.
#
# 원격 서빙을 부르는 serving_gateway_test.sh / serving_direct_parser_chunker_test.sh 와
# 달리 네트워크가 필요 없다 — FastAPI TestClient 로 앱을 직접 호출한다.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PREPROC_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
PY="${PY:-${PREPROC_DIR}/.venv/bin/python}"

# 표 경로(elements) — xlsx 행별 custom_fields 매핑
"${PY}" "${SCRIPT_DIR}/local_main_test.py" \
  --file genon/preprocessor/sample_files/monimo/monimo_menu_sample.xlsx --doc-type menu

echo
# docling 경로(document) — md
"${PY}" "${SCRIPT_DIR}/local_main_test.py" \
  --file genon/preprocessor/sample_files/drill/m1_marker_headings.md --doc-type ""
