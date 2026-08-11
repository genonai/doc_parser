#!/usr/bin/env bash
# 파일명은 기존 예제와의 호환을 위해 유지하지만, 게이트웨이가 아닌 분리 배포된
# parser/chunker 서비스의 /run 엔드포인트를 직접 호출한다.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

PARSER_URL="${PARSER_URL:-http://preprocessor-695:8080/run}"
CHUNKER_URL="${CHUNKER_URL:-http://preprocessor-698:8080/run}"
FILE_PATH="${FILE_PATH:-/nfs-root/DEV/volume/50/file_test/test_small_page.pdf}"
RESULT_DIR="${RESULT_DIR:-${SCRIPT_DIR}/result_serving_gateway_test}"

mkdir -p "${RESULT_DIR}"

echo "[1/3] parser/chunker healthcheck"
python3 "${SCRIPT_DIR}/serving_direct_parser_chunker_test.py" \
  --mode health \
  --parser-url "${PARSER_URL}" \
  --chunker-url "${CHUNKER_URL}"

echo "[2/3] parser 실행"
python3 "${SCRIPT_DIR}/serving_direct_parser_chunker_test.py" \
  --mode parser \
  --parser-url "${PARSER_URL}" \
  --chunker-url "${CHUNKER_URL}" \
  --file-path "${FILE_PATH}" \
  --out-doc "${RESULT_DIR}/doc.json"

echo "[3/3] chunker 실행"
python3 "${SCRIPT_DIR}/serving_direct_parser_chunker_test.py" \
  --mode chunker \
  --parser-url "${PARSER_URL}" \
  --chunker-url "${CHUNKER_URL}" \
  --doc-json "${RESULT_DIR}/doc.json" \
  --chunk-size 0 \
  --out "${RESULT_DIR}/chunks.json"

echo "테스트 완료: ${RESULT_DIR}"

# 한 번에 E2E로 실행하려면:
# python3 "${SCRIPT_DIR}/serving_gateway_test.py" --mode e2e \
#   --file-path "${FILE_PATH}" --chunk-size 0 \
#   --out-doc "${RESULT_DIR}/doc.json" --out "${RESULT_DIR}/chunks.json"

